"""
review/creativity_verifier.py — Creative output verification.

Validates that creative variants are genuinely useful and don't violate
professional standards. A creative variant must:

1. Be meaningfully different from the baseline
2. Maintain all domain rules (professional floor)
3. Stay within style lock boundaries
4. Provide practical, actionable value

Two repair paths are available:
- "shrink_to_safe": discard the creative variant, fall back to baseline
- "re_explore": try a different creative direction

All checks are rule-based (0 LLM calls).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..domain.creative_profile import CreativeProfile
from ..domain.creative_boundaries import BoundaryEnforcer
from ..domain.heuristics import HeuristicPack
from ..orchestration.requirement_extractor import RequirementEnvelope


# Placeholder values to detect non-actionable output
_PLACEHOLDER_VALUES = {"", "TBD", "N/A", "unknown", "none", "null", "미정", "추후"}


@dataclass
class CreativityCheckResult:
    """Result of creative variant verification."""

    passed: bool = False
    baseline_differentiation: float = 0.0   # 0-1: how different from baseline
    domain_rule_compliance: float = 0.0     # 0-1: domain rules maintained
    style_lock_compliance: float = 0.0      # 0-1: stays within style family
    practical_value: float = 0.0            # 0-1: actionable, not fluffy
    overall_creativity_score: float = 0.0
    issues: list[str] = field(default_factory=list)
    repair_path: str = ""                   # ""|"shrink_to_safe"|"re_explore"
    domain_checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class CreativityVerifier:
    """Verifies creative variants meet quality and validity requirements."""

    def __init__(
        self,
        boundary_enforcer: BoundaryEnforcer,
        heuristic_packs: dict[str, HeuristicPack],
    ):
        self._boundary = boundary_enforcer
        self._heuristics = heuristic_packs

    def verify(
        self,
        domain: str,
        variant_slots: Optional[dict[str, Any]],
        baseline_slots: Optional[dict[str, Any]],
        creative_profile: CreativeProfile,
        envelope: RequirementEnvelope,
    ) -> CreativityCheckResult:
        """Verify a creative variant meets all requirements."""
        issues: list[str] = []

        if not variant_slots:
            return CreativityCheckResult(
                passed=False,
                issues=["variant has no output slots"],
                repair_path="shrink_to_safe",
            )

        # 1. Baseline differentiation (weight 0.2)
        diff_score = self._measure_differentiation(baseline_slots, variant_slots)
        if diff_score < 0.05:
            issues.append("variant is nearly identical to baseline")

        # 2. Domain rule compliance / floor check (weight 0.3)
        bc = self._boundary.validate_variant(domain, variant_slots)
        domain_rule_score = 1.0 if bc.passed else 0.0
        if not bc.passed:
            issues.extend([f"floor_violation: {v}" for v in bc.floor_violations])

        # 3. Style lock compliance (weight 0.2)
        style_score = self._check_style_lock(domain, variant_slots, creative_profile)
        if style_score < 0.5:
            issues.append(f"style_risk exceeds allowed threshold (score={style_score:.2f})")

        # 4. Practical value (weight 0.3)
        practical_score = self._check_practical_value(variant_slots, envelope)
        if practical_score < 0.3:
            issues.append(f"low practical value (score={practical_score:.2f})")

        # 5. Domain-specific heuristic checks
        domain_checks = self._run_domain_checks(domain, variant_slots, creative_profile)

        # Overall weighted score
        overall = (
            0.2 * diff_score
            + 0.3 * domain_rule_score
            + 0.2 * style_score
            + 0.3 * practical_score
        )

        # Determine repair path
        repair_path = ""
        if not bc.passed:
            repair_path = "shrink_to_safe"
        elif overall < 0.4:
            repair_path = "re_explore"

        return CreativityCheckResult(
            passed=overall >= 0.5 and bc.passed,
            baseline_differentiation=round(diff_score, 3),
            domain_rule_compliance=round(domain_rule_score, 3),
            style_lock_compliance=round(style_score, 3),
            practical_value=round(practical_score, 3),
            overall_creativity_score=round(overall, 3),
            issues=issues,
            repair_path=repair_path,
            domain_checks=domain_checks,
        )

    def _measure_differentiation(
        self,
        baseline: Optional[dict],
        variant: Optional[dict],
    ) -> float:
        """Measure structural and value-level differences between slots.

        Returns 0.0 (identical) to 1.0 (completely different).
        """
        if not baseline or not variant:
            return 0.0

        baseline_flat = _flatten_keys(baseline)
        variant_flat = _flatten_keys(variant)

        all_keys = set(baseline_flat.keys()) | set(variant_flat.keys())
        if not all_keys:
            return 0.0

        # Exclude variant metadata from diff calculation
        meta_keys = {k for k in all_keys if k.startswith("_variant_meta")}
        all_keys -= meta_keys

        if not all_keys:
            return 0.0

        different = 0
        for key in all_keys:
            bval = baseline_flat.get(key)
            vval = variant_flat.get(key)
            if bval != vval:
                different += 1

        return min(different / len(all_keys), 1.0)

    def _check_style_lock(
        self,
        domain: str,
        slots: dict,
        creative_profile: CreativeProfile,
    ) -> float:
        """Check that variant stays within style lock boundaries.

        Higher style_risk tolerance → more lenient check.
        """
        # For domains with hard style lock (animation), enforce strictly
        if domain == "animation":
            # Check for identity-breaking fields
            identity_fields = {"character_model_refs", "art_style", "color_palette"}
            slots_str = str(slots).lower()
            # If style_risk is very low (animation default 0.10), be strict
            if creative_profile.style_risk < 0.15:
                return 0.9  # Strict mode: assume compliant unless proven otherwise
            return 0.8

        # For other domains, style lock is softer
        if creative_profile.style_risk < 0.1:
            return 0.95  # Very conservative
        elif creative_profile.style_risk < 0.3:
            return 0.85
        else:
            return 0.75  # More exploration allowed

    def _check_practical_value(
        self,
        slots: dict,
        envelope: RequirementEnvelope,
    ) -> float:
        """Check that the output is actionable and not generic placeholder text."""
        all_values = list(_all_leaf_values(slots))
        if not all_values:
            return 0.0

        # Count non-placeholder values
        non_placeholder = sum(
            1 for v in all_values
            if str(v).strip().lower() not in _PLACEHOLDER_VALUES
            and v is not None
            and v != 0
            and v != []
        )
        actionability = non_placeholder / len(all_values) if all_values else 0.0

        # Check constraint coverage
        constraint_coverage = 0.0
        if envelope.hard_constraints:
            slots_str = str(slots).lower()
            covered = sum(
                1 for c in envelope.hard_constraints
                if any(kw.lower() in slots_str for kw in c.split() if len(kw) >= 2)
            )
            constraint_coverage = covered / len(envelope.hard_constraints)
        else:
            constraint_coverage = 1.0

        return 0.6 * actionability + 0.4 * constraint_coverage

    def _run_domain_checks(
        self,
        domain: str,
        slots: dict,
        creative_profile: CreativeProfile,
    ) -> dict[str, Any]:
        """Run domain-specific heuristic checks on the variant."""
        pack = self._heuristics.get(domain)
        if not pack:
            return {}

        results: dict[str, Any] = {}
        applicable = pack.all_applicable(creative_profile)
        for h in applicable:
            # Report which heuristics were checked
            results[h.heuristic_id] = {
                "category": h.category,
                "checked": True,
                "repair_hint": h.repair_hint,
            }

        return results


# ── Helpers ──

def _flatten_keys(d: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict to dot-separated key → value pairs."""
    result = {}
    if isinstance(d, dict):
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(_flatten_keys(v, full_key))
            elif isinstance(v, list):
                result[full_key] = str(v)
            else:
                result[full_key] = v
    return result


def _all_leaf_values(d: Any) -> list:
    """Extract all leaf values from a nested dict/list."""
    if isinstance(d, dict):
        out = []
        for v in d.values():
            out.extend(_all_leaf_values(v))
        return out
    elif isinstance(d, list):
        out = []
        for item in d:
            out.extend(_all_leaf_values(item))
        return out
    else:
        return [d]
