"""Find discounted model offers and plan the change to the policy allowlist."""

from __future__ import annotations

from dataclasses import dataclass, field

PER_MILLION = 1_000_000

# Router models that every allowlist keeps. They have no provider endpoints, so they never qualify as a deal.
ALWAYS_ALLOWED = ("openrouter/auto", "openrouter/auto-beta")


@dataclass(frozen=True)
class Offer:
    """One provider endpoint of one model. Prices are USD per 1M tokens, after discount."""

    model: str
    model_name: str
    provider: str
    discount: float
    prompt: float
    completion: float


@dataclass
class Rules:
    min_discount: float = 0.3  # fraction, 0.3 = 30% off
    max_prompt: float | None = None  # USD per 1M input tokens
    max_completion: float | None = None  # USD per 1M output tokens
    include_batch: bool = False


def build_offers(models: list[dict], endpoints: dict[str, list[dict]], provider_slugs: dict[str, str]) -> list[Offer]:
    names = {m["id"]: m.get("name", m["id"]) for m in models}
    offers = []
    for model_id, eps in endpoints.items():
        for ep in eps:
            pricing = ep.get("pricing", {})
            try:
                prompt = float(pricing["prompt"]) * PER_MILLION
                completion = float(pricing["completion"]) * PER_MILLION
            except (KeyError, TypeError, ValueError):
                continue
            if prompt < 0 or completion < 0:  # router models use -1 for "variable"
                continue
            tag = ep.get("tag") or ""
            provider = provider_slugs.get(ep.get("provider_name", "")) or tag.split("/")[0]
            if not provider:
                continue
            offers.append(
                Offer(
                    model=model_id,
                    model_name=names.get(model_id, model_id),
                    provider=provider,
                    discount=float(pricing.get("discount") or 0),
                    prompt=prompt,
                    completion=completion,
                )
            )
    return offers


def qualifies(offer: Offer, rules: Rules) -> bool:
    if offer.discount <= 0 or offer.discount < rules.min_discount:
        return False
    if rules.max_prompt is not None and offer.prompt > rules.max_prompt:
        return False
    if rules.max_completion is not None and offer.completion > rules.max_completion:
        return False
    return rules.include_batch or not offer.model.endswith(":batch")


def deals_by_model(offers: list[Offer], rules: Rules) -> dict[str, list[Offer]]:
    """Qualifying offers grouped by model, cheapest input price first."""
    grouped: dict[str, list[Offer]] = {}
    for offer in offers:
        if qualifies(offer, rules):
            grouped.setdefault(offer.model, []).append(offer)
    for group in grouped.values():
        group.sort(key=lambda o: (o.prompt, o.completion))
    return grouped


@dataclass
class Plan:
    allowed_models: list[str]
    allowed_providers: list[str] | None
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def build_plan(
    selected: set[str],
    deals: dict[str, list[Offer]],
    current_models: list[str] | None,
    restrict_providers: bool,
) -> Plan:
    """Replace the whole allowlist: the selected deals plus ALWAYS_ALLOWED."""
    allowed = sorted(selected | set(ALWAYS_ALLOWED))
    current = set(current_models or [])
    providers = None
    if restrict_providers:
        providers = sorted({o.provider for m in allowed for o in deals.get(m, [])})
    return Plan(
        allowed_models=allowed,
        allowed_providers=providers,
        added=[m for m in allowed if m not in current],
        removed=sorted(current - set(allowed)),
    )


def stored_ids(models: list[dict]) -> dict[str, str]:
    """Map model id to the canonical slug, the form the OpenRouter site recognises in a policy.

    Variants (":free", ":batch") share the base model's canonical slug, so they keep their id.
    """
    return {m["id"]: m["canonical_slug"] for m in models if ":" not in m["id"] and m.get("canonical_slug")}


def apply_plan(config: dict, plan: Plan, stored: dict[str, str]) -> dict:
    """Return a full config for PUT: the stored settings plus the new allowlists."""
    models = [stored.get(m, m) for m in plan.allowed_models]
    return {**config, "allowed_models": models, "allowed_providers": plan.allowed_providers}
