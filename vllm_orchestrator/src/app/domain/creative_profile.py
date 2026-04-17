"""
domain/creative_profile.py — Creative Profile for domain-specific creativity control.

Each domain has a default creative profile that governs:
- How much novelty the LLM explores
- How strictly constraints are enforced
- How much style deviation is tolerated
- How many variant outputs to produce

When creative_profile is absent from context, the domain default is used.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any, Optional


class CreativeMode(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    EXPRESSIVE = "expressive"


class DiversityTarget(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class CreativeProfile:
    """Controls creativity parameters for a single request."""

    mode: str = CreativeMode.BALANCED.value
    novelty: float = 0.5
    constraint_strictness: float = 0.8
    style_risk: float = 0.3
    variant_count: int = 1
    diversity_target: str = DiversityTarget.MEDIUM.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CreativeProfile:
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_multi_variant(self) -> bool:
        return self.variant_count > 1

    @property
    def diversity_weight(self) -> float:
        """Map diversity_target to numeric weight for variant generator."""
        return {"low": 0.2, "medium": 0.5, "high": 0.8}.get(
            self.diversity_target, 0.5,
        )


# ── Domain defaults (user-confirmed fixed values) ──

_DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "minecraft": {
        "mode": "balanced",
        "novelty": 0.72,
        "constraint_strictness": 0.82,
        "style_risk": 0.38,
        "variant_count": 3,
        "diversity_target": "high",
    },
    "builder": {
        "mode": "balanced",
        "novelty": 0.42,
        "constraint_strictness": 0.95,
        "style_risk": 0.12,
        "variant_count": 2,
        "diversity_target": "medium",
    },
    "animation": {
        "mode": "balanced",
        "novelty": 0.58,
        "constraint_strictness": 0.90,
        "style_risk": 0.10,
        "variant_count": 3,
        "diversity_target": "medium",
    },
    "cad": {
        "mode": "conservative",
        "novelty": 0.34,
        "constraint_strictness": 0.97,
        "style_risk": 0.06,
        "variant_count": 2,
        "diversity_target": "low",
    },
    # product_design inherits from cad
    "product_design": {
        "mode": "conservative",
        "novelty": 0.34,
        "constraint_strictness": 0.97,
        "style_risk": 0.06,
        "variant_count": 2,
        "diversity_target": "low",
    },
}


def get_domain_default(domain: str) -> CreativeProfile:
    """Return the fixed default creative profile for a domain."""
    data = _DOMAIN_DEFAULTS.get(domain, _DOMAIN_DEFAULTS["cad"])
    return CreativeProfile.from_dict(data)


def resolve_creative_profile(
    domain: str,
    context: Optional[dict[str, Any]] = None,
) -> CreativeProfile:
    """Resolve creative profile: user override > domain default.

    The user override comes from context["creative_profile"].
    Values outside domain-allowed range are clamped.
    """
    default = get_domain_default(domain)
    if not context:
        return default

    override = context.get("creative_profile")
    if not override or not isinstance(override, dict):
        return default

    # Merge override into default, clamping numeric ranges
    merged = default.to_dict()
    for key, val in override.items():
        if key not in merged:
            continue
        merged[key] = val

    result = CreativeProfile.from_dict(merged)

    # Clamp numeric fields to valid ranges
    result.novelty = max(0.0, min(1.0, result.novelty))
    result.constraint_strictness = max(0.0, min(1.0, result.constraint_strictness))
    result.style_risk = max(0.0, min(1.0, result.style_risk))
    result.variant_count = max(1, result.variant_count)

    # Validate enum values
    if result.mode not in {m.value for m in CreativeMode}:
        result.mode = default.mode
    if result.diversity_target not in {d.value for d in DiversityTarget}:
        result.diversity_target = default.diversity_target

    return result
