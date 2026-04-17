"""
orchestration/variant_planner.py — Multi-variant output planning.

Given a baseline output from the pipeline, the VariantPlanner generates
additional creative variants according to the creative_profile.

Each variant is:
1. Generated via prompt perturbation (strategy-specific suffix)
2. Validated against professional floor (BoundaryEnforcer)
3. Evaluated by DomainEvaluator
4. Rejected if floor is violated

Variant families are domain-specific and fixed per user specification.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from uuid import uuid4

from ..domain.creative_profile import CreativeProfile
from ..domain.creative_boundaries import BoundaryEnforcer, BoundaryCheckResult
from ..domain.profiles import DomainProfile
from ..review.domain_evaluator import DomainEvaluator, DomainEvaluation
from ..orchestration.domain_classifier import ClassificationResult
from ..orchestration.requirement_extractor import RequirementEnvelope


@dataclass
class VariantSpec:
    """A single planned variant."""

    variant_id: str = field(default_factory=lambda: f"var_{uuid4().hex[:8]}")
    family: str = "safe_baseline"
    label: str = ""
    strategy: str = ""
    diff_from_baseline: dict[str, Any] = field(default_factory=dict)
    slots: Optional[dict[str, Any]] = None
    boundary_check: Optional[dict[str, Any]] = None
    creativity_check: Optional[dict[str, Any]] = None
    evaluation: Optional[dict[str, Any]] = None
    accepted: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> VariantSpec:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class VariantPlan:
    """Multi-variant plan output."""

    creative_profile: dict[str, Any] = field(default_factory=dict)
    variants: list[VariantSpec] = field(default_factory=list)
    selected_variant_id: str = ""
    selection_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "creative_profile": self.creative_profile,
            "variants": [v.to_dict() for v in self.variants],
            "selected_variant_id": self.selected_variant_id,
            "selection_reason": self.selection_reason,
        }

    @property
    def best_slots(self) -> Optional[dict]:
        """Return slots of the selected variant."""
        for v in self.variants:
            if v.variant_id == self.selected_variant_id and v.slots:
                return v.slots
        return self.variants[0].slots if self.variants else None

    @property
    def accepted_variants(self) -> list[VariantSpec]:
        return [v for v in self.variants if v.accepted]


# ── Domain-specific variant families (user-confirmed) ──

_VARIANT_FAMILIES: dict[str, list[dict[str, str]]] = {
    "minecraft": [
        {"family": "safe_baseline", "label": "Standard build", "strategy": "original"},
        {"family": "creative_variant", "label": "Creative exploration", "strategy": "style_shifted"},
        {"family": "world_expansion_variant", "label": "World expansion", "strategy": "theme_expanded"},
    ],
    "builder": [
        {"family": "compliance_safe_plan", "label": "Code-compliant plan", "strategy": "original"},
        {"family": "design_enhanced_plan", "label": "Design-enhanced plan", "strategy": "design_enhanced"},
    ],
    "animation": [
        {"family": "safe_camera_plan", "label": "Safe camera plan", "strategy": "original"},
        {"family": "cinematic_camera_variant", "label": "Cinematic camera", "strategy": "cinematic_pushed"},
        {"family": "style_feedback_repair_variant", "label": "Style feedback repair", "strategy": "style_feedback"},
    ],
    "cad": [
        {"family": "baseline_engineering_solution", "label": "Reference design", "strategy": "original"},
        {"family": "compact_or_creative_concept_variant", "label": "Compact/creative concept", "strategy": "engineering_alternative"},
    ],
    "product_design": [
        {"family": "baseline_engineering_solution", "label": "Reference design", "strategy": "original"},
        {"family": "compact_or_creative_concept_variant", "label": "Compact/creative concept", "strategy": "engineering_alternative"},
    ],
}


# ── Strategy-specific prompt suffixes ──

_STRATEGY_PROMPTS: dict[str, str] = {
    "original": "",  # baseline, no perturbation
    "style_shifted": (
        "\n\nIMPORTANT: Generate a CREATIVE VARIANT of the same request. "
        "Explore a different style or approach while keeping structural validity. "
        "The result should be meaningfully different from a standard/safe version. "
        "Maintain all hard constraints but push creative boundaries."
    ),
    "theme_expanded": (
        "\n\nIMPORTANT: Generate a WORLD EXPANSION VARIANT. "
        "Expand the world concept with additional elements, deeper lore, or extended scope. "
        "Keep the core theme consistent but broaden the creative vision. "
        "Maintain structural coherence and block validity."
    ),
    "design_enhanced": (
        "\n\nIMPORTANT: Generate a DESIGN-ENHANCED VARIANT of the same plan. "
        "Improve aesthetics, spatial quality, or user experience beyond code minimums. "
        "All regulatory compliance must be maintained. "
        "Focus on elevation rhythm, massing quality, or interior atmosphere."
    ),
    "cinematic_pushed": (
        "\n\nIMPORTANT: Generate a CINEMATIC VARIANT with bolder camera choices. "
        "Push framing, camera movement, and emotional staging further. "
        "Maintain 180-degree rule and character identity. "
        "Make the camera work more expressive and emotionally impactful."
    ),
    "style_feedback": (
        "\n\nIMPORTANT: Generate a STYLE FEEDBACK REPAIR VARIANT. "
        "Identify potential style drift points and provide corrective direction. "
        "Focus on maintaining character identity, line consistency, and color coherence. "
        "Output should include repair guidance for style lock violations."
    ),
    "engineering_alternative": (
        "\n\nIMPORTANT: Generate an ENGINEERING ALTERNATIVE VARIANT. "
        "Propose a different mechanical/structural approach that is equally valid. "
        "Focus on real engineering merit: weight reduction, assembly simplification, "
        "or manufacturing cost optimization. Pure form changes without engineering "
        "value are NOT acceptable as creative alternatives."
    ),
}


def _compute_diff(base: dict, variant: dict) -> dict:
    """Compute a shallow explainable diff between base and variant slots."""
    if not base or not variant:
        return {}
    diff = {}
    all_keys = set(list(base.keys()) + list(variant.keys()))
    for key in all_keys:
        bval = base.get(key)
        vval = variant.get(key)
        if bval != vval:
            diff[key] = {"base": bval, "variant": vval}
    return diff


class VariantPlanner:
    """Plans and generates multi-variant outputs."""

    def __init__(
        self,
        boundary_enforcer: BoundaryEnforcer,
        evaluator: DomainEvaluator,
    ):
        self._boundary = boundary_enforcer
        self._evaluator = evaluator

    def plan_variants(
        self,
        domain: str,
        creative_profile: CreativeProfile,
        base_slots: Optional[dict[str, Any]],
        envelope: RequirementEnvelope,
        classification: ClassificationResult,
        profile: DomainProfile,
        *,
        variant_generator_fn: Optional[Any] = None,
    ) -> VariantPlan:
        """Given a baseline output, plan and validate multiple variants.

        Args:
            domain: Target domain
            creative_profile: Controls variant count and creativity params
            base_slots: Baseline output from the pipeline execution step
            envelope: Extracted requirements
            classification: Domain classification result
            profile: Domain profile
            variant_generator_fn: Optional callable(base_slots, strategy, prompt_suffix) -> dict
                for generating variant slots. If None, variants are produced
                by shallow perturbation of base_slots.
        """
        if not base_slots:
            return VariantPlan(
                creative_profile=creative_profile.to_dict(),
                variants=[],
                selection_reason="no baseline slots to build variants from",
            )

        families = _VARIANT_FAMILIES.get(domain, _VARIANT_FAMILIES.get("cad", []))
        n = min(creative_profile.variant_count, len(families))
        variants: list[VariantSpec] = []

        # Variant 0: always the baseline
        baseline_family = families[0] if families else {"family": "safe_baseline", "label": "Baseline", "strategy": "original"}
        baseline_bc = self._boundary.validate_variant(domain, base_slots)
        baseline_eval = self._evaluator.evaluate(classification, envelope, profile, base_slots)

        baseline = VariantSpec(
            family=baseline_family["family"],
            label=baseline_family["label"],
            strategy="original",
            slots=base_slots,
            boundary_check=baseline_bc.to_dict(),
            evaluation=baseline_eval.to_dict(),
            accepted=baseline_bc.passed,
        )
        variants.append(baseline)

        # Additional variants
        for i in range(1, n):
            family_def = families[i]
            strategy = family_def["strategy"]
            prompt_suffix = _STRATEGY_PROMPTS.get(strategy, "")

            # Generate variant slots
            variant_slots = None
            if variant_generator_fn:
                try:
                    variant_slots = variant_generator_fn(
                        base_slots, strategy, prompt_suffix,
                    )
                except Exception:
                    variant_slots = None

            # Fallback: shallow copy with strategy tag
            if variant_slots is None:
                variant_slots = self._shallow_perturb(base_slots, strategy, creative_profile)

            # Validate
            bc = self._boundary.validate_variant(domain, variant_slots)
            ev = self._evaluator.evaluate(classification, envelope, profile, variant_slots)
            diff = _compute_diff(base_slots, variant_slots)

            vs = VariantSpec(
                family=family_def["family"],
                label=family_def["label"],
                strategy=strategy,
                diff_from_baseline=diff,
                slots=variant_slots,
                boundary_check=bc.to_dict(),
                evaluation=ev.to_dict(),
                accepted=bc.passed,
            )
            variants.append(vs)

        # Select best accepted variant
        accepted = [v for v in variants if v.accepted and v.evaluation]
        if accepted:
            best = max(
                accepted,
                key=lambda v: v.evaluation.get("overall_score", 0) if v.evaluation else 0,
            )
            selected_id = best.variant_id
            reason = f"Highest score ({best.evaluation.get('overall_score', 0):.3f}) among {len(accepted)} accepted variants"
        else:
            selected_id = variants[0].variant_id if variants else ""
            reason = "Fallback to baseline (no variants passed floor)"

        return VariantPlan(
            creative_profile=creative_profile.to_dict(),
            variants=variants,
            selected_variant_id=selected_id,
            selection_reason=reason,
        )

    def _shallow_perturb(
        self,
        base_slots: dict,
        strategy: str,
        creative_profile: CreativeProfile,
    ) -> dict:
        """Create a shallow variant by copying base and adding variant metadata.

        This is used when no LLM-based variant generator is available.
        The variant is structurally identical to the baseline but tagged
        with variant metadata for downstream processing.
        """
        variant = copy.deepcopy(base_slots)
        # Add variant metadata
        variant["_variant_meta"] = {
            "strategy": strategy,
            "novelty": creative_profile.novelty,
            "style_risk": creative_profile.style_risk,
        }
        return variant

    @staticmethod
    def get_variant_families(domain: str) -> list[dict[str, str]]:
        """Return the fixed variant families for a domain."""
        return _VARIANT_FAMILIES.get(domain, _VARIANT_FAMILIES.get("cad", []))

    @staticmethod
    def get_strategy_prompt(strategy: str) -> str:
        """Return the prompt suffix for a generation strategy."""
        return _STRATEGY_PROMPTS.get(strategy, "")
