from or_guard.deals import Offer, Plan
from or_guard.slack import MAX_TABLE_ROWS, build_message

WORKSPACE = {"name": "Dev", "slug": "dev"}


def cells(blocks: list[dict]) -> list[list[str]]:
    table = next(b for b in blocks if b["type"] == "table")
    assert {len(row) for row in table["rows"]} == {4}
    return [[cell["text"] for cell in row] for row in table["rows"]]


def test_message_table_shows_discount_and_prices_in_rank_order():
    plan = Plan(["openrouter/auto", "a/m", "b/m", "c/m", "d/m"], None, added=["a/m"], removed=["x/m"])
    deals = {
        "a/m": [Offer("a/m", "A", "p1", 0.0, 0.6, 0.8)],  # cheap, no discount
        "b/m": [Offer("b/m", "B", "p1", 0.2, 0.5, 1.0)],
        "c/m": [Offer("c/m", "C", "p1", 0.0, 0.2, 0.4)],  # cheap, no discount
        "d/m": [Offer("d/m", "D", "p1", 0.3, 0.5, 1.0), Offer("d/m", "D", "p2", 0.5, 0.8, 1.6)],
    }
    names = {"a/m": "A", "b/m": "B", "c/m": "C", "d/m": "D"}

    text, blocks = build_message(WORKSPACE, "Team", plan, names, deals)

    assert cells(blocks) == [
        ["Model", "Discount", "Input $/1M", "Output $/1M"],
        ["D", "50%", "0.500", "1.000"],
        ["B", "20%", "0.500", "1.000"],
        ["C", "-", "0.200", "0.400"],
        ["A", "-", "0.600", "0.800"],
        ["openrouter/auto", "-", "-", "-"],
    ]
    assert "5 models (1 added, 1 removed)" in text
    assert any("openrouter/auto" in b.get("text", {}).get("text", "") for b in blocks)


def test_long_lists_fit_the_slack_row_limit():
    models = [f"m/{i:03}" for i in range(150)]

    _, blocks = build_message(WORKSPACE, "Team", Plan(models, None), {}, {})

    rows = cells(blocks)
    assert len(rows) == MAX_TABLE_ROWS
    assert rows[-1] == ["and 52 more", "-", "-", "-"]
