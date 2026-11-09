"""HTTP client for the OpenRouter routes this tool uses."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

BASE = "https://openrouter.ai/api/v1"
POLICY_PATH = "/guardrails/policies/model-availability"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _request(
    method: str,
    path: str,
    key: str | None = None,
    body: dict | None = None,
    params: dict | None = None,
) -> Any:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        raw = err.read().decode(errors="replace")
        try:
            message = json.loads(raw)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = raw[:300] or err.reason
        raise ApiError(err.code, message) from None
    except (OSError, ValueError) as err:  # URLError, read timeout, or a body that is not JSON
        raise ApiError(0, str(getattr(err, "reason", err))) from None


# Public routes: no key needed.


def fetch_models() -> list[dict]:
    return _request("GET", "/models")["data"]


def fetch_provider_slugs() -> dict[str, str]:
    """Map provider display name (as in endpoint `provider_name`) to its slug."""
    return {p["name"]: p["slug"] for p in _request("GET", "/providers")["data"]}


def fetch_all_endpoints(models: list[dict], workers: int = 16) -> dict[str, list[dict]]:
    """Return {model id: provider endpoints}. A model whose request fails maps to []."""

    def one(model: dict) -> tuple[str, list[dict]]:
        path = model.get("links", {}).get("details", "").removeprefix("/api/v1")
        if not path:
            return model["id"], []
        try:
            return model["id"], _request("GET", path)["data"]["endpoints"]
        except (ApiError, KeyError, TypeError):
            return model["id"], []

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, models))


# Management routes: need a Management API key.


class Management:
    def __init__(self, key: str) -> None:
        self.key = key

    def list_workspaces(self) -> list[dict]:
        items: list[dict] = []
        while True:
            page = _request("GET", "/workspaces", self.key, params={"offset": len(items), "limit": 100})
            items += page["data"]
            if not page["data"] or len(items) >= page["total_count"]:
                return items

    def list_policies(self, workspace_id: str) -> list[dict]:
        # Without offset and limit the route returns the full list.
        return _request("GET", POLICY_PATH, self.key, params={"workspace_id": workspace_id})["data"]

    def create_policy(self, workspace_id: str, name: str, config: dict) -> dict:
        body = {
            "name": name,
            "policy_type": "model_availability",
            "workspace_id": workspace_id,
            "config": config,
            "is_workspace_default": True,
        }
        return _request("POST", POLICY_PATH, self.key, body=body)["data"]

    def update_policy(self, policy_id: str, workspace_id: str, name: str, config: dict) -> dict:
        # Omitting member_ids and api_key_hashes keeps the stored assignments.
        # A policy migrated from a legacy guardrail forks: the response carries a new id.
        body = {"name": name, "workspace_id": workspace_id, "config": config}
        return _request("PUT", f"{POLICY_PATH}/{policy_id}", self.key, body=body)["data"]
