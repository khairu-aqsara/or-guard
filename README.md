# or-guard

or-guard is a terminal UI that sets the **Model Availability** guardrail policy of one OpenRouter
workspace. It scans every model and provider endpoint for a current discount and price, filters the
offers by your rules, and shows the result in a table. You review the list, then apply it to the policy.

or-guard manages only the Model Availability policy. It reads the workspace list to pick a target,
and it does not create, change or delete workspaces.

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- An OpenRouter **Management API key**, to read workspaces and write policies. Without the key, the
  scan works, but you cannot pick or change a policy.
- Optional: a Slack bot token with `chat:write`, to post a notice after an apply.

## Setup

```sh
uv sync
cp .env.example .env   # then fill in the values
chmod 600 .env
```

or-guard reads `.env` from the current directory when it starts. A variable that is already set in
the shell wins over the value in `.env`.

| Variable | Required | Use |
| --- | --- | --- |
| `OPENROUTER_MANAGEMENT_KEY` | Yes, to apply | Lists workspaces and policies, and writes the policy. |
| `ENABLE_SLACK_NOTIFICATION` | No | Set to `true` to post a notice after an apply. Default `false`. |
| `SLACK_BOT_TOKEN` | No | Bot token (`xoxb-...`) for the notice after an apply. |
| `SLACK_CHANNEL` | No | Channel ID for the notice, for example `C02KKDWRFR7`. |

The Slack notice needs `ENABLE_SLACK_NOTIFICATION=true` and both Slack variables. Any other value
turns the notice off. The bot must be a member of the channel.

## Usage

```sh
uv run or-guard
```

1. Choose the **workspace**. The header shows the active workspace as `Workspace: name (slug)`.
2. Choose the **policy**. or-guard selects the workspace default policy. If the workspace has none,
   it selects **+ New policy, set as workspace default**.
3. Set the rules, then press Enter in a field or click **Scan**.
4. Review the table. Press space to remove or add a model.
5. Press `ctrl+s`. A dialog shows the change and its warnings. Click **Apply** to save.

### Rules

| Field | Effect |
| --- | --- |
| Min discount % | An offer qualifies only when its discount is this value or more. Default 10. |
| Max input $/1M | Upper limit for the input price, in USD per 1M tokens, after discount. Empty means no limit. Default empty. |
| Max output $/1M | Upper limit for the output price, in USD per 1M tokens, after discount. Empty means no limit. Default 1. |
| Cheap input $/1M | Min and max input price, in USD per 1M tokens. A model inside the cheap range is in the table without a discount. Default 0 to 1. |
| Cheap output $/1M | Min and max output price, in USD per 1M tokens. Both the input and the output price must be inside the range. The cheap range is off while both max fields are empty. Default 0 to 1. |
| Include :batch | Also include `:batch` model variants. Off by default. |
| Restrict providers | Also set `allowed_providers` to the providers of the qualifying offers. On by default. |

A model is in the table when at least one provider endpoint qualifies. `:free` model variants never
qualify. The table shows discounted models first, largest discount first. Cheap models without a
discount follow, cheapest input price first. The price columns show the cheapest qualifying offer.

### Keys

| Key | Action |
| --- | --- |
| `space` | Toggle the model in the current row |
| `ctrl+a` | Select every model with a deal |
| `ctrl+n` | Select no model |
| `ctrl+r` | Scan again |
| `ctrl+s` | Review and apply |
| `ctrl+q` | Quit |

## What apply does

- Apply **replaces** the whole `allowed_models` list with the selected models. A model that is not
  selected is removed. Apps that call a removed model by name get HTTP 403.
- `openrouter/auto` and `openrouter/auto-beta` are **always** in the list. You cannot remove them.
- or-guard writes each model as its `canonical_slug`, for example `deepseek/deepseek-v4-flash-20260423`.
  The OpenRouter site shows a warning icon for a model stored by its short id. Variants such as
  `:free` keep their id, because they share the canonical slug of the base model.
- Every other setting of the policy stays as it is, for example `enforce_zdr_openai`. The member and
  API key assignments also stay.
- A new policy becomes the workspace default.
- A policy that is synced from a legacy guardrail gets a new policy ID when you save it. The change
  does not flow back to the legacy guardrail.
- When the list changed and the Slack notice is on, or-guard posts a notice to `SLACK_CHANNEL`. The
  notice holds a table of the allowed models with the largest discount and the input and output
  price of the cheapest offer, the same values as the app table. The rows use the app table order:
  discounted models first, largest discount first, then cheap models, cheapest input price first. The
  router models come last. The notice also holds a recommendation to use `openrouter/auto` or
  `openrouter/auto-beta` in code. The notice shows the name and icon of the Slack app that owns the bot
  token. If the post fails, the policy stays saved and the app shows a warning.

## How the scan works

The public `/models` route has no discount field. or-guard reads `/models/{author}/{slug}/endpoints`
for each model, about 460 requests in parallel. Each endpoint has `pricing.discount`, and its listed
price already includes the discount. `/providers` maps the provider name to the slug that
`allowed_providers` expects.

The Model Availability policy has two separate lists: models and providers. It cannot allow one model
only through one provider. With **Restrict providers** on, a selected model can also use any other
allowed provider that serves it, without a discount.

## Development

```sh
uv sync                    # installs the dev group: ruff, mypy, pytest
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

The tests use fake OpenRouter data and a fake Management client. They make no network calls and send
no Slack message.

| Path | Content |
| --- | --- |
| `src/or_guard/api.py` | HTTP client for the public and Management routes (stdlib `urllib`) |
| `src/or_guard/deals.py` | Offers, rules, and the plan that replaces the allowlist |
| `src/or_guard/slack.py` | The Slack notice (Block Kit `table`) |
| `src/or_guard/app.py` | The Textual app and the `.env` loader |
| `tests/` | pytest tests |

## Known limits

- Not tested: whether `openrouter/auto` routes only inside the allowlist when `allowed_providers` is set.
- Not tested: what OpenRouter does when a second policy is created with `is_workspace_default`.
