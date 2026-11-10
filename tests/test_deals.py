from or_guard.deals import (
    ALWAYS_ALLOWED,
    Offer,
    Rules,
    apply_plan,
    build_offers,
    build_plan,
    deals_by_model,
    stored_ids,
)


def offer(model: str, provider: str = "novita", discount: float = 0.5, prompt: float = 1.0) -> Offer:
    return Offer(model, model, provider, discount, prompt, prompt * 2)


def test_build_offers_converts_prices_and_skips_router_prices():
    models = [{"id": "a/m", "name": "A M"}, {"id": "openrouter/auto"}]
    endpoints = {
        "a/m": [
            {"provider_name": "Novita", "pricing": {"prompt": "0.0000005", "completion": "0.000001", "discount": 0.4}},
            {"provider_name": "Unknown", "tag": "deepinfra/fp8", "pricing": {"prompt": "0.000001", "completion": "0"}},
        ],
        "openrouter/auto": [{"provider_name": "Novita", "pricing": {"prompt": "-1", "completion": "-1"}}],
    }

    offers = build_offers(models, endpoints, {"Novita": "novita"})

    assert offers == [
        Offer("a/m", "A M", "novita", 0.4, 0.5, 1.0),
        Offer("a/m", "A M", "deepinfra", 0.0, 1.0, 0.0),
    ]


def test_deals_by_model_applies_rules_and_sorts_cheapest_first():
    offers = [
        offer("a/m", "p1", discount=0.5, prompt=3.0),
        offer("a/m", "p2", discount=0.3, prompt=1.0),
        offer("b/m", discount=0.1),  # below min discount
        offer("c/m", discount=0.0),  # no discount
        offer("d/m:batch", discount=0.5),  # batch excluded by default
        offer("e/m", discount=0.9, prompt=50.0),  # over the input price cap
        offer("f/m:free", discount=1.0, prompt=0.0),  # free variants never qualify
    ]

    deals = deals_by_model(offers, Rules(min_discount=0.3, max_prompt=10.0))

    assert list(deals) == ["a/m"]
    assert [o.provider for o in deals["a/m"]] == ["p2", "p1"]
    assert "d/m:batch" in deals_by_model(offers, Rules(include_batch=True))


def test_cheap_range_adds_models_without_a_discount():
    offers = [
        offer("a/m", discount=0.5, prompt=3.0),  # discounted, outside the cheap range
        offer("b/m", discount=0.0, prompt=0.2),  # input 0.2, output 0.4: cheap
        offer("c/m", discount=0.0, prompt=0.05),  # below the cheap minimum
        offer("d/m", discount=0.0, prompt=0.4),  # output 0.8 over the cheap output maximum
        offer("e/m:free", discount=0.0, prompt=0.2),  # free variants never qualify
    ]
    rules = Rules(cheap_min_prompt=0.1, cheap_max_prompt=0.5, cheap_max_completion=0.5)

    assert sorted(deals_by_model(offers, rules)) == ["a/m", "b/m"]
    assert sorted(deals_by_model(offers, Rules())) == ["a/m"]  # no cheap maximum, no cheap models


def test_build_plan_replaces_the_list_and_keeps_routers():
    deals = {"a/m": [offer("a/m", "novita")], "b/m": [offer("b/m", "groq")]}

    plan = build_plan({"a/m"}, deals, current_models=["old/m", "openrouter/auto"], restrict_providers=True)

    assert plan.allowed_models == sorted(["a/m", *ALWAYS_ALLOWED])
    assert plan.added == ["a/m", "openrouter/auto-beta"]
    assert plan.removed == ["old/m"]
    assert plan.allowed_providers == ["novita"]
    assert build_plan({"a/m"}, deals, None, restrict_providers=False).allowed_providers is None


def test_apply_plan_writes_canonical_slugs_and_keeps_other_settings():
    models = [
        {"id": "a/m", "canonical_slug": "a/m-20260101"},
        {"id": "a/m:free", "canonical_slug": "a/m-20260101"},
    ]
    plan = build_plan({"a/m", "a/m:free"}, {}, None, restrict_providers=False)

    config = apply_plan({"allowed_models": ["x"], "enforce_zdr_openai": True}, plan, stored_ids(models))

    assert config == {
        "allowed_models": ["a/m-20260101", "a/m:free", "openrouter/auto", "openrouter/auto-beta"],
        "allowed_providers": None,
        "enforce_zdr_openai": True,
    }
