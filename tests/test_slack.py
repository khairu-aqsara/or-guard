from or_guard.deals import Plan
from or_guard.slack import MAX_TABLE_ROWS, build_message

WORKSPACE = {"name": "Dev", "slug": "dev"}


def cells(blocks: list[dict]) -> list[str]:
    table = next(b for b in blocks if b["type"] == "table")
    assert {len(row) for row in table["rows"]} == {1}
    return [row[0]["text"] for row in table["rows"]]


def test_message_is_a_one_column_table_of_model_names():
    plan = Plan(["b/m", "a/m"], None, added=["a/m"], removed=["c/m"])

    text, blocks = build_message(WORKSPACE, "Team", plan, {"a/m": "A Model", "b/m": "B Model"})

    assert cells(blocks) == ["Model", "A Model", "B Model"]
    assert "2 models (1 added, 1 removed)" in text
    assert any("openrouter/auto" in b.get("text", {}).get("text", "") for b in blocks)


def test_long_lists_fit_the_slack_row_limit():
    models = [f"m/{i:03}" for i in range(150)]

    _, blocks = build_message(WORKSPACE, "Team", Plan(models, None), {})

    rows = cells(blocks)
    assert len(rows) == MAX_TABLE_ROWS
    assert rows[-1] == "and 52 more"
