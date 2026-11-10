"""Textual TUI: review discounted and cheap models and write them to a Model Availability policy."""

from __future__ import annotations

import os

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Select, Static

from . import api, slack
from .deals import (
    ALWAYS_ALLOWED,
    Offer,
    Plan,
    Rules,
    apply_plan,
    build_offers,
    build_plan,
    deals_by_model,
    ranked,
    stored_ids,
)

NEW_POLICY = "__new__"
DEFAULT_POLICY_NAME = "Discounted models"
COLUMNS = ("Allow", "Model", "Max off", "Input $/1M", "Output $/1M", "Providers", "Status")


def _money(value: float) -> str:
    return f"{value:.3f}"


def _names(items: list[str], limit: int = 25) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f", and {len(items) - limit} more" if len(items) > limit else "")


class ConfirmScreen(ModalScreen[str | None]):
    """Show the planned change. Dismiss with the policy name, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog {
        width: 100; max-width: 95%; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #summary { height: auto; max-height: 30; }
    #name { margin-top: 1; }
    #buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, summary: str, policy_name: str, is_new: bool) -> None:
        super().__init__()
        self.summary = summary
        self.policy_name = policy_name
        self.is_new = is_new

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            with VerticalScroll(id="summary"):
                yield Static(self.summary, markup=False)
            if self.is_new:
                yield Input(self.policy_name, placeholder="Policy name", id="name")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", variant="error", id="ok")

    @on(Button.Pressed, "#ok")
    def confirm(self) -> None:
        name = self.query_one("#name", Input).value.strip() if self.is_new else self.policy_name
        if not name:
            self.notify("Enter a policy name.", severity="error")
            return
        self.dismiss(name)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class GuardApp(App):
    TITLE = "OpenRouter model guardrail"
    CSS = """
    #targets, #rules, #cheap, #options { height: auto; padding: 0 1; }
    #targets Select { width: 1fr; }
    #rules Label, #cheap Label { padding: 1 1 0 1; }
    #rules Input, #cheap Input { width: 12; }
    #options Checkbox { margin-left: 1; }
    #options Button { margin-left: 2; }
    #table { height: 1fr; }
    #status { height: auto; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("space", "toggle_model", "Toggle model"),
        Binding("ctrl+a", "select_all(True)", "All deals"),
        Binding("ctrl+n", "select_all(False)", "None"),
        Binding("ctrl+r", "scan", "Rescan"),
        Binding("ctrl+s", "review", "Review and apply"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        key = os.environ.get("OPENROUTER_MANAGEMENT_KEY", "").strip()
        self.mgmt = api.Management(key) if key else None
        self.models: list[dict] = []
        self.canonical: dict[str, str] = {}
        self.offers: list[Offer] = []
        self.deals: dict[str, list[Offer]] = {}
        self.selected: set[str] = set()
        self.workspaces: dict[str, dict] = {}
        self.policies: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="targets"):
            yield Select([], prompt="Workspace", id="workspace", disabled=self.mgmt is None)
            yield Select([], prompt="Policy", id="policy", disabled=True)
        with Horizontal(id="rules"):
            yield Label("Min discount %")
            yield Input("10", type="integer", id="min_discount")
            yield Label("Max input $/1M")
            yield Input("", type="number", placeholder="any", id="max_prompt")
            yield Label("Max output $/1M")
            yield Input("1", type="number", placeholder="any", id="max_completion")
        with Horizontal(id="cheap"):
            yield Label("Cheap input $/1M")
            yield Input("0", type="number", placeholder="min", id="cheap_min_prompt")
            yield Input("1", type="number", placeholder="max", id="cheap_max_prompt")
            yield Label("Cheap output $/1M")
            yield Input("0", type="number", placeholder="min", id="cheap_min_completion")
            yield Input("1", type="number", placeholder="max", id="cheap_max_completion")
        with Horizontal(id="options"):
            yield Checkbox("Include :batch", False, id="batch")
            yield Checkbox("Restrict providers", True, id="restrict")
            yield Button("Scan", id="scan", variant="primary")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for column in COLUMNS:
            table.add_column(column, key=column)
        if self.mgmt is None:
            self.set_status("OPENROUTER_MANAGEMENT_KEY is not set. Scan works, but you cannot pick or change a policy.")
        else:
            self.load_workspaces(self.mgmt)
        self.action_scan()

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    # Current policy state.

    @property
    def workspace_id(self) -> str | None:
        return self.query_one("#workspace", Select).selection

    @property
    def policy_id(self) -> str | None:
        return self.query_one("#policy", Select).selection

    @property
    def policy(self) -> dict | None:
        return self.policies.get(self.policy_id or "")

    def current_models(self) -> list[str] | None:
        """Allowed models of the chosen policy, as model ids. None means every model is allowed."""
        policy = self.policy
        if policy is None:
            return None
        models: list[str] | None = policy["config"].get("allowed_models")
        return None if models is None else [self.canonical.get(m, m) for m in models]

    # Loading data.

    @work(thread=True, exclusive=True, group="workspaces")
    def load_workspaces(self, mgmt: api.Management) -> None:
        try:
            workspaces = mgmt.list_workspaces()
        except api.ApiError as err:
            self.call_from_thread(self.notify, f"Cannot list workspaces. {err}", severity="error")
            return
        self.call_from_thread(self.show_workspaces, workspaces)

    def show_workspaces(self, workspaces: list[dict]) -> None:
        self.workspaces = {w["id"]: w for w in workspaces}
        select = self.query_one("#workspace", Select)
        select.set_options([(f"{w['name']} ({w['slug']})", w["id"]) for w in workspaces])
        if len(workspaces) == 1:
            select.value = workspaces[0]["id"]

    @on(Select.Changed, "#workspace")
    def workspace_changed(self) -> None:
        self.policies = {}
        policy_select = self.query_one("#policy", Select)
        policy_select.set_options([])
        policy_select.disabled = True
        workspace = self.workspaces.get(self.workspace_id or "")
        self.sub_title = f"Workspace: {workspace['name']} ({workspace['slug']})" if workspace else ""
        if self.mgmt and self.workspace_id:
            self.load_policies(self.mgmt, self.workspace_id)

    @work(thread=True, exclusive=True, group="policies")
    def load_policies(self, mgmt: api.Management, workspace_id: str, select_id: str | None = None) -> None:
        try:
            policies = mgmt.list_policies(workspace_id)
        except api.ApiError as err:
            self.call_from_thread(self.notify, f"Cannot list policies. {err}", severity="error")
            return
        self.call_from_thread(self.show_policies, policies, select_id)

    def show_policies(self, policies: list[dict], select_id: str | None) -> None:
        self.policies = {p["id"]: p for p in policies}
        options = []
        for p in policies:
            tags = []
            if p.get("is_workspace_default"):
                tags.append("workspace default")
            if p.get("is_legacy_mapped"):
                tags.append("synced from legacy guardrail")
            label = p["name"] + (f" ({', '.join(tags)})" if tags else "")
            options.append((label, p["id"]))
        options.append(("+ New policy, set as workspace default", NEW_POLICY))
        select = self.query_one("#policy", Select)
        select.set_options(options)
        select.disabled = False
        default = next((p["id"] for p in policies if p.get("is_workspace_default")), NEW_POLICY)
        select.value = select_id if select_id in self.policies else default

    @on(Select.Changed, "#policy")
    def policy_changed(self) -> None:
        self.refresh_table()

    @work(thread=True, exclusive=True, group="scan")
    def action_scan(self) -> None:
        self.call_from_thread(self.set_status, "Scanning every model and provider for discounts and prices...")
        try:
            models = api.fetch_models()
            slugs = api.fetch_provider_slugs()
        except api.ApiError as err:
            self.call_from_thread(self.set_status, f"Scan failed. {err}")
            return
        endpoints = api.fetch_all_endpoints(models)
        offers = build_offers(models, endpoints, slugs)
        self.call_from_thread(self.finish_scan, models, offers)

    def finish_scan(self, models: list[dict], offers: list[Offer]) -> None:
        self.models = models
        # Variants such as ":batch" share the base model's canonical slug. Sort them first so the base id wins.
        variants_first = sorted(models, key=lambda m: ":" not in m["id"])
        self.canonical = {m["canonical_slug"]: m["id"] for m in variants_first if m.get("canonical_slug")}
        self.offers = offers
        self.refresh_table()

    @on(Button.Pressed, "#scan")
    def scan_pressed(self) -> None:
        self.action_scan()

    # Rules and table.

    def read_rules(self) -> Rules | None:
        def number(widget_id: str) -> float | None:
            text = self.query_one(f"#{widget_id}", Input).value.strip()
            return float(text) if text else None

        try:
            min_discount = number("min_discount") or 0
            rules = Rules(
                min_discount=min_discount / 100,
                max_prompt=number("max_prompt"),
                max_completion=number("max_completion"),
                include_batch=self.query_one("#batch", Checkbox).value,
                cheap_min_prompt=number("cheap_min_prompt"),
                cheap_max_prompt=number("cheap_max_prompt"),
                cheap_min_completion=number("cheap_min_completion"),
                cheap_max_completion=number("cheap_max_completion"),
            )
        except ValueError:
            self.notify("Rules must be numbers.", severity="error")
            return None
        return rules

    @on(Input.Submitted)
    @on(Checkbox.Changed, "#batch")
    def rules_changed(self) -> None:
        self.refresh_table()

    @on(Checkbox.Changed, "#restrict")
    def restrict_changed(self) -> None:
        self.update_status()

    def refresh_table(self) -> None:
        """Re-apply the rules. This resets the selection to every model with a deal."""
        if not self.offers:
            return
        rules = self.read_rules()
        if rules is None:
            return
        self.deals = deals_by_model(self.offers, rules)
        current = self.current_models()
        in_policy = set(current or [])
        self.selected = set(self.deals)

        table = self.query_one(DataTable)
        table.clear()
        for model in ALWAYS_ALLOWED:
            table.add_row(self.mark(model), model, "-", "-", "-", "router, no provider", "always allowed", key=model)
        for model in ranked(list(self.deals), self.deals):
            offers = self.deals[model]
            best = offers[0]
            providers = ", ".join(o.provider + (f" -{o.discount:.0%}" if o.discount > 0 else "") for o in offers)
            status = "in policy" if model in in_policy else "new"
            table.add_row(
                self.mark(model),
                model,
                f"{max(o.discount for o in offers):.0%}",
                _money(best.prompt),
                _money(best.completion),
                providers,
                status,
                key=model,
            )
        self.update_status()

    def mark(self, model: str) -> str:
        return "[x]" if model in self.selected or model in ALWAYS_ALLOWED else "[ ]"

    def update_status(self) -> None:
        current = self.current_models()
        if self.policy_id == NEW_POLICY:
            now = "New policy."
        elif self.policy is None:
            now = "No policy chosen."
        elif current is None:
            now = f"Policy '{self.policy['name']}' allows every model now."
        else:
            now = f"Policy '{self.policy['name']}' allows {len(current)} models now."
        offer_count = sum(len(v) for v in self.deals.values())
        self.set_status(
            f"{len(self.deals)} models match the rules ({offer_count} provider offers). "
            f"{len(self.selected)} selected, plus {len(ALWAYS_ALLOWED)} always allowed. {now}"
        )

    def action_toggle_model(self) -> None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        model = str(row_key.value)
        if model in ALWAYS_ALLOWED:
            self.notify(f"{model} is always allowed.")
            return
        self.selected ^= {model}
        table.update_cell(row_key, "Allow", self.mark(model))
        self.update_status()

    def action_select_all(self, value: bool) -> None:
        self.selected = set(self.deals) if value else set()
        table = self.query_one(DataTable)
        for row_key in table.rows:
            table.update_cell(row_key, "Allow", self.mark(str(row_key.value)))
        self.update_status()

    # Review and apply.

    def make_plan(self) -> Plan:
        restrict = self.query_one("#restrict", Checkbox).value
        return build_plan(self.selected, self.deals, self.current_models(), restrict)

    def action_review(self) -> None:
        if self.mgmt is None:
            self.notify("Set OPENROUTER_MANAGEMENT_KEY to change a policy.", severity="error")
            return
        if not self.workspace_id or not self.policy_id:
            self.notify("Choose a workspace and a policy first.", severity="error")
            return
        if not self.selected:
            self.notify("Select at least one discounted model.", severity="error")
            return
        policy = self.policy
        name = DEFAULT_POLICY_NAME if policy is None else policy["name"]
        summary = self.summary(self.make_plan(), self.workspaces[self.workspace_id])
        self.push_screen(ConfirmScreen(summary, name, is_new=policy is None), self.after_confirm)

    def summary(self, plan: Plan, workspace: dict) -> str:
        current = self.current_models()
        old_providers = (self.policy or {}).get("config", {}).get("allowed_providers")
        lines = [
            f"Workspace: {workspace['name']} ({workspace['slug']})",
            f"Policy: {self.policy['name'] if self.policy else 'new policy, set as workspace default'}",
            "",
            f"Allowed models: {'every model' if current is None else len(current)} -> {len(plan.allowed_models)}"
            " (the new list replaces the old list)",
            f"  always allowed: {', '.join(ALWAYS_ALLOWED)}",
            f"  + add ({len(plan.added)}): {_names(plan.added) or '-'}",
            f"  - remove ({len(plan.removed)}): {_names(plan.removed) or '-'}",
            f"Allowed providers: {_names(old_providers) if old_providers else 'any'}"
            f" -> {_names(plan.allowed_providers) if plan.allowed_providers else 'any'}",
            "",
        ]
        target = slack_settings()
        if target and (plan.added or plan.removed):
            lines += [f"Slack: posts this change to channel {target[1]} after a successful save.", ""]
        if current is None and self.policy is not None:
            lines.append("! This policy allows every model now. After apply, only the selected models are allowed.")
        if plan.removed:
            lines.append("! Apps that call a removed model by name get HTTP 403 after apply.")
        if self.policy and self.policy.get("is_legacy_mapped"):
            lines.append(
                "! This policy is synced from a legacy guardrail. The update creates a new policy ID, "
                "and the change does not flow back to the guardrail."
            )
        if self.policy is None:
            default = next((p for p in self.policies.values() if p.get("is_workspace_default")), None)
            if default:
                lines.append(f"! '{default['name']}' is the workspace default now. Check it after apply.")
        return "\n".join(lines)

    def after_confirm(self, name: str | None) -> None:
        # Read the widgets here, on the UI thread. The apply worker gets plain values.
        if name is None or self.mgmt is None or self.workspace_id is None:
            return
        self.apply(self.mgmt, self.workspaces[self.workspace_id], self.policy, name, self.make_plan())

    @work(thread=True, exclusive=True, group="apply")
    def apply(self, mgmt: api.Management, workspace: dict, policy: dict | None, name: str, plan: Plan) -> None:
        config = apply_plan(policy["config"] if policy else {}, plan, stored_ids(self.models))
        try:
            if policy is None:
                result = mgmt.create_policy(workspace["id"], name, config)
            else:
                result = mgmt.update_policy(policy["id"], workspace["id"], name, config)
        except api.ApiError as err:
            self.call_from_thread(self.notify, f"Apply failed. {err}", severity="error", timeout=15)
            return
        self.call_from_thread(self.notify, f"Saved policy '{result['name']}' with {len(plan.allowed_models)} models.")
        self.call_from_thread(self.load_policies, mgmt, workspace["id"], result["id"])
        self.notify_slack(workspace, result["name"], plan)

    def notify_slack(self, workspace: dict, policy_name: str, plan: Plan) -> None:
        """Runs inside the apply worker thread. Posts only when the allowlist changed."""
        target = slack_settings()
        if target is None or not (plan.added or plan.removed):
            return
        token, channel = target
        names = {m["id"]: m.get("name", m["id"]) for m in self.models}
        try:
            slack.post(token, channel, *slack.build_message(workspace, policy_name, plan, names, self.deals))
        except (RuntimeError, OSError) as err:
            self.call_from_thread(self.notify, f"Policy saved, but the Slack post failed. {err}", severity="warning")
            return
        self.call_from_thread(self.notify, f"Posted the change to Slack channel {channel}.")


def slack_settings() -> tuple[str, str] | None:
    """Return (token, channel) when ENABLE_SLACK_NOTIFICATION is true and both values are set."""
    if os.environ.get("ENABLE_SLACK_NOTIFICATION", "false").strip().lower() != "true":
        return None
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL", "").strip()
    return (token, channel) if token and channel else None


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file. Variables already set in the shell win."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def main() -> None:
    load_dotenv()
    GuardApp().run()


if __name__ == "__main__":
    main()
