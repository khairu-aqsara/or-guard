"""Post an apply summary to Slack with a bot token (chat.postMessage and a table block)."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from .deals import ALWAYS_ALLOWED, Offer, Plan, ranked

MAX_TABLE_ROWS = 100  # Slack table block limit, header row included
COLUMNS = ["Model", "Discount", "Input $/1M", "Output $/1M"]


def _row(name: str, offers: list[Offer]) -> list[str]:
    """Largest discount and the cheapest offer's prices, as in the app table. Router models have no offers."""
    if not offers:
        return [name, "-", "-", "-"]
    top = max(o.discount for o in offers)
    return [name, f"{top:.0%}" if top > 0 else "-", f"{offers[0].prompt:.3f}", f"{offers[0].completion:.3f}"]


def build_message(
    workspace: dict, policy_name: str, plan: Plan, names: dict[str, str], deals: dict[str, list[Offer]]
) -> tuple[str, list[dict[str, Any]]]:
    """Return (fallback text, blocks): a header line and a table of model name, discount and prices."""
    header = (
        f":shield: *OpenRouter allowed models updated* in *{workspace['name']}* (`{workspace['slug']}`). "
        f"Policy *{policy_name}* now allows {len(plan.allowed_models)} models "
        f"({len(plan.added)} added, {len(plan.removed)} removed)."
    )
    table = [_row(names.get(m, m), deals.get(m, [])) for m in ranked(plan.allowed_models, deals)]
    room = MAX_TABLE_ROWS - 1
    if len(table) > room:
        table = [*table[: room - 1], [f"and {len(table) - room + 1} more", "-", "-", "-"]]
    rows = [[{"type": "raw_text", "text": cell} for cell in row] for row in [COLUMNS, *table]]
    routers = " or ".join(f"`{m}`" for m in ALWAYS_ALLOWED)
    recommendation = (
        f":bulb: *Recommendation:* set the model in your code to {routers}. "
        "The Auto Router chooses a model for each request. Both router models stay allowed on every update, "
        "so your code keeps working when this list changes. "
        "Use a model from the table only when you need that exact model."
    )
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": recommendation}},
        {
            "type": "table",
            "column_settings": [{"align": "left"}, {"align": "right"}, {"align": "right"}, {"align": "right"}],
            "rows": rows,
        },
    ]
    return header, blocks


def post(token: str, channel: str, text: str, blocks: list[dict[str, Any]]) -> None:
    """Raise RuntimeError with Slack's error code when the post fails."""
    body = {"channel": channel, "text": text, "blocks": blocks}
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "unknown Slack error"))
