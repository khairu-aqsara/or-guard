"""Drive the TUI with fake OpenRouter data. No network calls."""

import asyncio

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Select
from textual.worker import WorkerState

from or_guard import api, app, slack

WORKSPACE = {"id": "ws-1", "name": "Dev", "slug": "dev"}
MODELS = [
    {"id": "a/cheap", "name": "A: Cheap", "canonical_slug": "a/cheap-20260101", "links": {"details": "/x"}},
    {"id": "b/full", "name": "B: Full", "canonical_slug": "b/full-20260101", "links": {"details": "/y"}},
]
ENDPOINTS = {
    "a/cheap": [
        {"provider_name": "Novita", "pricing": {"prompt": "0.0000005", "completion": "0.000001", "discount": 0.5}}
    ],
    "b/full": [{"provider_name": "Groq", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}],
}
POLICY = {
    "id": "pol-1",
    "name": "Team models",
    "is_workspace_default": True,
    "config": {"allowed_models": ["b/full-20260101"], "allowed_providers": None, "enforce_zdr_openai": True},
}


class FakeManagement:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, str, dict]] = []

    def list_workspaces(self) -> list[dict]:
        return [WORKSPACE]

    def list_policies(self, workspace_id: str) -> list[dict]:
        return [POLICY]

    def update_policy(self, policy_id: str, workspace_id: str, name: str, config: dict) -> dict:
        self.updates.append((policy_id, workspace_id, name, config))
        return {"id": policy_id, "name": name}


@pytest.fixture
def fake_openrouter(monkeypatch):
    monkeypatch.setattr(api, "fetch_models", lambda: MODELS)
    monkeypatch.setattr(api, "fetch_provider_slugs", lambda: {"Novita": "novita", "Groq": "groq"})
    monkeypatch.setattr(api, "fetch_all_endpoints", lambda models: ENDPOINTS)
    monkeypatch.setenv("ENABLE_SLACK_NOTIFICATION", "true")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")
    posts: list[tuple] = []
    monkeypatch.setattr(slack, "post", lambda *args: posts.append(args))
    return posts


@pytest.mark.parametrize(
    ("enabled", "token", "expected"),
    [
        (None, "xoxb-test", None),  # off by default
        ("false", "xoxb-test", None),
        ("TRUE", "xoxb-test", ("xoxb-test", "C123")),
        ("true", None, None),  # on, but no token
    ],
)
def test_slack_settings_need_the_flag_and_both_values(monkeypatch, enabled, token, expected):
    for name, value in {
        "ENABLE_SLACK_NOTIFICATION": enabled,
        "SLACK_BOT_TOKEN": token,
        "SLACK_CHANNEL": "C123",
    }.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert app.slack_settings() == expected


async def settle(pilot: Pilot) -> None:
    """Wait until no worker runs. workers.wait_for_complete() raises on Textual's own cancelled workers."""
    for _ in range(100):
        await pilot.pause()
        if not any(w.state in (WorkerState.PENDING, WorkerState.RUNNING) for w in pilot.app.workers):
            return
        await asyncio.sleep(0.05)


async def test_apply_replaces_allowlist_and_posts_to_slack(fake_openrouter):
    mgmt = FakeManagement()
    guard = app.GuardApp()
    guard.mgmt = mgmt  # type: ignore[assignment]

    async with guard.run_test(size=(140, 45)) as pilot:
        await settle(pilot)
        assert guard.query_one("#workspace", Select).value == "ws-1"
        assert guard.query_one("#policy", Select).value == "pol-1"

        table = guard.query_one(DataTable)
        table.focus()
        table.move_cursor(row=0)  # openrouter/auto cannot be removed
        await pilot.press("space")
        table.move_cursor(row=2)  # a/cheap
        await pilot.press("space")
        assert guard.selected == set()
        await pilot.press("space")
        assert guard.selected == {"a/cheap"}

        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.click("#ok")
        await settle(pilot)

    policy_id, workspace_id, name, config = mgmt.updates[0]
    assert (policy_id, workspace_id, name) == ("pol-1", "ws-1", "Team models")
    assert config == {
        "allowed_models": ["a/cheap-20260101", "openrouter/auto", "openrouter/auto-beta"],
        "allowed_providers": ["novita"],
        "enforce_zdr_openai": True,
    }
    token, channel, text, _ = fake_openrouter[0]
    assert (token, channel) == ("xoxb-test", "C123")
    assert "(3 added, 1 removed)" in text
