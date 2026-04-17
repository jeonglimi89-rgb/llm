"""
llm/review_model_split.py — Generation/Review/Repair model split policy.

Enforces that generation, review, and repair phases can use different
model tiers and adapter configurations. Even when using the same physical
model, the policy distinction enables future upgrade to stronger review models.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .adapter_policy import PipelinePhase


@dataclass
class PhaseModelConfig:
    """Model configuration for a specific pipeline phase."""
    phase: str
    model_logical_id: str = "core_text_32b"
    tier_preference: str = "text"       # "text"|"creative"|"long_context"|"code"
    adapter_attach: bool = True         # whether adapter is recommended
    strictness_boost: float = 0.0       # additional strictness for review phases
    temperature_override: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DomainReviewPolicy:
    """Complete phase model split for a domain."""
    domain: str
    generation: PhaseModelConfig = field(default_factory=lambda: PhaseModelConfig(PipelinePhase.GENERATION.value))
    review: PhaseModelConfig = field(default_factory=lambda: PhaseModelConfig(PipelinePhase.REVIEW.value))
    repair: PhaseModelConfig = field(default_factory=lambda: PhaseModelConfig(PipelinePhase.REPAIR.value))
    creative: PhaseModelConfig = field(default_factory=lambda: PhaseModelConfig(PipelinePhase.CREATIVE_VARIANT.value))

    def get_config(self, phase: str) -> PhaseModelConfig:
        return {
            PipelinePhase.GENERATION.value: self.generation,
            PipelinePhase.REVIEW.value: self.review,
            PipelinePhase.REPAIR.value: self.repair,
            PipelinePhase.CREATIVE_VARIANT.value: self.creative,
        }.get(phase, self.generation)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "generation": self.generation.to_dict(),
            "review": self.review.to_dict(),
            "repair": self.repair.to_dict(),
            "creative": self.creative.to_dict(),
        }


_DOMAIN_REVIEW_POLICIES: dict[str, DomainReviewPolicy] = {
    "builder": DomainReviewPolicy(
        domain="builder",
        generation=PhaseModelConfig(PipelinePhase.GENERATION.value, adapter_attach=True),
        review=PhaseModelConfig(PipelinePhase.REVIEW.value, adapter_attach=True, strictness_boost=0.1, temperature_override=0.05),
        repair=PhaseModelConfig(PipelinePhase.REPAIR.value, adapter_attach=True),
        creative=PhaseModelConfig(PipelinePhase.CREATIVE_VARIANT.value, tier_preference="creative", adapter_attach=True),
    ),
    "cad": DomainReviewPolicy(
        domain="cad",
        generation=PhaseModelConfig(PipelinePhase.GENERATION.value, adapter_attach=True),
        review=PhaseModelConfig(PipelinePhase.REVIEW.value, adapter_attach=True, strictness_boost=0.15, temperature_override=0.05),
        repair=PhaseModelConfig(PipelinePhase.REPAIR.value, adapter_attach=True),
        creative=PhaseModelConfig(PipelinePhase.CREATIVE_VARIANT.value, tier_preference="creative", adapter_attach=False),
    ),
    "minecraft": DomainReviewPolicy(
        domain="minecraft",
        generation=PhaseModelConfig(PipelinePhase.GENERATION.value, adapter_attach=True),
        review=PhaseModelConfig(PipelinePhase.REVIEW.value, adapter_attach=True),
        repair=PhaseModelConfig(PipelinePhase.REPAIR.value, adapter_attach=True),
        creative=PhaseModelConfig(PipelinePhase.CREATIVE_VARIANT.value, tier_preference="creative", adapter_attach=True),
    ),
    "animation": DomainReviewPolicy(
        domain="animation",
        generation=PhaseModelConfig(PipelinePhase.GENERATION.value, adapter_attach=True),
        review=PhaseModelConfig(PipelinePhase.REVIEW.value, adapter_attach=True, strictness_boost=0.05),
        repair=PhaseModelConfig(PipelinePhase.REPAIR.value, adapter_attach=True),
        creative=PhaseModelConfig(PipelinePhase.CREATIVE_VARIANT.value, tier_preference="creative", adapter_attach=True),
    ),
}


def get_review_policy(domain: str) -> DomainReviewPolicy:
    return _DOMAIN_REVIEW_POLICIES.get(domain, DomainReviewPolicy(domain=domain))
