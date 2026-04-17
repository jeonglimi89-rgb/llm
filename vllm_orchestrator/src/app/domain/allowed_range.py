"""
domain/allowed_range.py — Domain-specific allowed ranges for creative_profile.

Prevents creative_profile overrides from exceeding domain-safe boundaries.

Two enforcement modes:
- normalize: clamp to range, record violation in telemetry
- strict: reject with fail-loud if out of range

Default: normalize for Minecraft/Animation, strict option for CAD/Builder.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .creative_profile import CreativeProfile


@dataclass
class AllowedRange:
    """Allowed range for a single creative profile field."""
    field_name: str
    min_val: float = 0.0
    max_val: float = 1.0

    def contains(self, value: float) -> bool:
        return self.min_val <= value <= self.max_val

    def clamp(self, value: float) -> float:
        return max(self.min_val, min(self.max_val, value))


@dataclass
class DomainAllowedRange:
    """Complete allowed range definition for a domain."""
    domain: str
    novelty: AllowedRange = field(default_factory=lambda: AllowedRange("novelty"))
    constraint_strictness: AllowedRange = field(default_factory=lambda: AllowedRange("constraint_strictness"))
    style_risk: AllowedRange = field(default_factory=lambda: AllowedRange("style_risk"))
    variant_count_min: int = 1
    variant_count_max: int = 4
    allowed_modes: list[str] = field(default_factory=lambda: ["conservative", "balanced", "expressive"])
    allowed_diversity_targets: list[str] = field(default_factory=lambda: ["low", "medium", "high"])
    enforcement_mode: str = "normalize"   # "normalize" | "strict"

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "novelty": {"min": self.novelty.min_val, "max": self.novelty.max_val},
            "constraint_strictness": {"min": self.constraint_strictness.min_val, "max": self.constraint_strictness.max_val},
            "style_risk": {"min": self.style_risk.min_val, "max": self.style_risk.max_val},
            "variant_count": {"min": self.variant_count_min, "max": self.variant_count_max},
            "allowed_modes": self.allowed_modes,
            "allowed_diversity_targets": self.allowed_diversity_targets,
            "enforcement_mode": self.enforcement_mode,
        }


@dataclass
class RangeEnforcementResult:
    """Result of allowed range enforcement."""
    passed: bool = True
    adjusted: bool = False
    violations: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    enforcement_mode: str = "normalize"
    original_values: dict[str, Any] = field(default_factory=dict)
    final_values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Domain allowed ranges (user-confirmed) ──

_DOMAIN_RANGES: dict[str, DomainAllowedRange] = {
    "minecraft": DomainAllowedRange(
        domain="minecraft",
        novelty=AllowedRange("novelty", 0.45, 0.85),
        constraint_strictness=AllowedRange("constraint_strictness", 0.70, 0.92),
        style_risk=AllowedRange("style_risk", 0.15, 0.45),
        variant_count_min=1, variant_count_max=4,
        allowed_modes=["balanced", "expressive"],
        allowed_diversity_targets=["medium", "high"],
        enforcement_mode="normalize",
    ),
    "builder": DomainAllowedRange(
        domain="builder",
        novelty=AllowedRange("novelty", 0.20, 0.55),
        constraint_strictness=AllowedRange("constraint_strictness", 0.88, 1.00),
        style_risk=AllowedRange("style_risk", 0.00, 0.18),
        variant_count_min=1, variant_count_max=3,
        allowed_modes=["conservative", "balanced"],
        allowed_diversity_targets=["low", "medium"],
        enforcement_mode="normalize",
    ),
    "animation": DomainAllowedRange(
        domain="animation",
        novelty=AllowedRange("novelty", 0.35, 0.70),
        constraint_strictness=AllowedRange("constraint_strictness", 0.82, 0.97),
        style_risk=AllowedRange("style_risk", 0.00, 0.15),
        variant_count_min=1, variant_count_max=4,
        allowed_modes=["balanced", "expressive"],
        allowed_diversity_targets=["medium", "high"],
        enforcement_mode="normalize",
    ),
    "cad": DomainAllowedRange(
        domain="cad",
        novelty=AllowedRange("novelty", 0.15, 0.45),
        constraint_strictness=AllowedRange("constraint_strictness", 0.92, 1.00),
        style_risk=AllowedRange("style_risk", 0.00, 0.08),
        variant_count_min=1, variant_count_max=3,
        allowed_modes=["conservative", "balanced"],
        allowed_diversity_targets=["low", "medium"],
        enforcement_mode="normalize",
    ),
    "product_design": DomainAllowedRange(
        domain="product_design",
        novelty=AllowedRange("novelty", 0.15, 0.45),
        constraint_strictness=AllowedRange("constraint_strictness", 0.92, 1.00),
        style_risk=AllowedRange("style_risk", 0.00, 0.08),
        variant_count_min=1, variant_count_max=3,
        allowed_modes=["conservative", "balanced"],
        allowed_diversity_targets=["low", "medium"],
        enforcement_mode="normalize",
    ),
}


def get_domain_allowed_range(domain: str) -> DomainAllowedRange:
    return _DOMAIN_RANGES.get(domain, _DOMAIN_RANGES["cad"])


class AllowedRangeEnforcer:
    """Enforces creative profile values stay within domain allowed ranges."""

    def enforce(
        self,
        domain: str,
        profile: CreativeProfile,
        *,
        strict_override: Optional[bool] = None,
    ) -> tuple[CreativeProfile, RangeEnforcementResult]:
        """Enforce allowed range on a creative profile.

        Returns (possibly-adjusted profile, enforcement result).
        In strict mode, returns the original profile with passed=False on violation.
        In normalize mode, clamps values and records adjustments.
        """
        dar = get_domain_allowed_range(domain)
        mode = "strict" if strict_override else dar.enforcement_mode

        violations: list[str] = []
        adjustments: list[str] = []
        original = profile.to_dict()

        # Check numeric ranges
        if not dar.novelty.contains(profile.novelty):
            violations.append(f"novelty={profile.novelty} outside [{dar.novelty.min_val}, {dar.novelty.max_val}]")
        if not dar.constraint_strictness.contains(profile.constraint_strictness):
            violations.append(f"constraint_strictness={profile.constraint_strictness} outside [{dar.constraint_strictness.min_val}, {dar.constraint_strictness.max_val}]")
        if not dar.style_risk.contains(profile.style_risk):
            violations.append(f"style_risk={profile.style_risk} outside [{dar.style_risk.min_val}, {dar.style_risk.max_val}]")
        if not (dar.variant_count_min <= profile.variant_count <= dar.variant_count_max):
            violations.append(f"variant_count={profile.variant_count} outside [{dar.variant_count_min}, {dar.variant_count_max}]")
        if profile.mode not in dar.allowed_modes:
            violations.append(f"mode='{profile.mode}' not in allowed {dar.allowed_modes}")
        if profile.diversity_target not in dar.allowed_diversity_targets:
            violations.append(f"diversity_target='{profile.diversity_target}' not in allowed {dar.allowed_diversity_targets}")

        if not violations:
            return profile, RangeEnforcementResult(
                passed=True,
                enforcement_mode=mode,
                original_values=original,
                final_values=original,
            )

        # Strict mode: fail without adjusting
        if mode == "strict":
            return profile, RangeEnforcementResult(
                passed=False,
                violations=violations,
                enforcement_mode="strict",
                original_values=original,
                final_values=original,
            )

        # Normalize mode: clamp and record
        adjusted = CreativeProfile(
            mode=profile.mode if profile.mode in dar.allowed_modes else dar.allowed_modes[0],
            novelty=dar.novelty.clamp(profile.novelty),
            constraint_strictness=dar.constraint_strictness.clamp(profile.constraint_strictness),
            style_risk=dar.style_risk.clamp(profile.style_risk),
            variant_count=max(dar.variant_count_min, min(dar.variant_count_max, profile.variant_count)),
            diversity_target=(
                profile.diversity_target
                if profile.diversity_target in dar.allowed_diversity_targets
                else dar.allowed_diversity_targets[0]
            ),
        )

        # Record what was adjusted
        final = adjusted.to_dict()
        for key in original:
            if original[key] != final[key]:
                adjustments.append(f"{key}: {original[key]} -> {final[key]}")

        return adjusted, RangeEnforcementResult(
            passed=True,
            adjusted=True,
            violations=violations,
            adjustments=adjustments,
            enforcement_mode="normalize",
            original_values=original,
            final_values=final,
        )
