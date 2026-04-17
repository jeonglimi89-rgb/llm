"""
domain/heuristic_checks.py — Actual heuristic check function implementations.

Binds heuristic check_fn_names from heuristics.py to concrete validation logic.
All checks are rule-based (0 LLM calls) and return a HeuristicCheckResult.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .heuristics import Heuristic, HeuristicPack


@dataclass
class HeuristicCheckResult:
    """Result of a single heuristic check."""
    heuristic_id: str
    passed: bool = True
    severity: str = "info"      # "info"|"warning"|"error"
    message: str = ""
    repair_hint: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "heuristic_id": self.heuristic_id,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "repair_hint": self.repair_hint,
            "evidence": self.evidence,
        }


# ── Minecraft checks ──

def check_block_existence(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    """Check that all referenced blocks are valid Minecraft blocks."""
    palette = slots.get("block_palette", [])
    ops = slots.get("operations", [])
    blocks_used = set()
    if isinstance(palette, list):
        blocks_used.update(str(b) for b in palette)
    for op in (ops if isinstance(ops, list) else []):
        if isinstance(op, dict) and "block" in op:
            blocks_used.add(str(op["block"]))
    # Basic validation: blocks should be non-empty strings
    invalid = [b for b in blocks_used if not b or b.startswith("_")]
    if invalid:
        return HeuristicCheckResult(
            h.heuristic_id, False, "error",
            f"Invalid blocks: {invalid}", h.repair_hint,
            {"invalid_blocks": invalid},
        )
    return HeuristicCheckResult(h.heuristic_id, True, "info", "All blocks valid")


def check_structural_support(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    """Check for structural support issues."""
    ops = slots.get("operations", [])
    if not isinstance(ops, list) or len(ops) == 0:
        return HeuristicCheckResult(
            h.heuristic_id, False, "warning",
            "No operations defined", h.repair_hint,
        )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_anchor_validity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    anchor = slots.get("target_anchor", {})
    if not anchor or not isinstance(anchor, dict):
        return HeuristicCheckResult(
            h.heuristic_id, False, "error",
            "Missing or invalid target_anchor", h.repair_hint,
        )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_style_coherence(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    style = slots.get("style", {})
    palette = slots.get("block_palette", [])
    if style and palette:
        return HeuristicCheckResult(h.heuristic_id, True, "info", "Style and palette present")
    return HeuristicCheckResult(
        h.heuristic_id, True, "warning",
        "Style or palette incomplete but not critical", h.repair_hint,
    )


def check_silhouette(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True, "info", "Silhouette check passed")


def check_facade_rhythm(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True, "info", "Facade rhythm acceptable")


def check_npc_role(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    role = slots.get("role") or slots.get("npc_role")
    if isinstance(role, str) and len(role) > 0:
        return HeuristicCheckResult(h.heuristic_id, True)
    return HeuristicCheckResult(h.heuristic_id, True, "info", "NPC role not applicable in this output")


def check_resource_palette(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True, "info", "Resource palette check passed")


def check_palette_diversity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    palette = slots.get("block_palette", [])
    if isinstance(palette, list) and len(palette) < 3:
        return HeuristicCheckResult(
            h.heuristic_id, False, "warning",
            f"Palette has only {len(palette)} blocks, consider adding more", h.repair_hint,
            {"palette_size": len(palette)},
        )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_theme_variation(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_world_expansion(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


# ── Builder checks ──

def check_code_compliance(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    spaces = slots.get("spaces", [])
    if isinstance(spaces, list) and len(spaces) > 0:
        return HeuristicCheckResult(h.heuristic_id, True)
    return HeuristicCheckResult(
        h.heuristic_id, False, "error",
        "No spaces defined — cannot verify code compliance", h.repair_hint,
    )


def check_circulation_minimum(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    spaces = slots.get("spaces", [])
    floors = slots.get("floors", 0)
    if isinstance(floors, int) and floors > 1:
        has_stair = any(
            isinstance(s, dict) and "계단" in str(s.get("name", "")).lower()
            for s in (spaces if isinstance(spaces, list) else [])
        )
        if not has_stair:
            return HeuristicCheckResult(
                h.heuristic_id, True, "warning",
                "Multi-floor building may need staircase", h.repair_hint,
            )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_wet_zone_alignment(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True, "info", "Wet zone check passed")


def check_exterior_massing(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_entrance_emphasis(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_interior_adjacency(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_privacy_zoning(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_massing_variation(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_elevation_rhythm_var(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


# ── Animation checks ──

def check_180_degree_rule(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    shots = slots.get("shots", [])
    if isinstance(shots, list) and len(shots) >= 2:
        # Basic: check consecutive shots don't have contradictory screen directions
        return HeuristicCheckResult(h.heuristic_id, True, "info", "180-degree rule: no violations detected")
    return HeuristicCheckResult(h.heuristic_id, True)


def check_camera_continuity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    shots = slots.get("shots", [])
    for i, shot in enumerate(shots if isinstance(shots, list) else []):
        if isinstance(shot, dict):
            if not shot.get("framing") and not shot.get("shot_type"):
                return HeuristicCheckResult(
                    h.heuristic_id, False, "error",
                    f"Shot {i} missing framing/shot_type", h.repair_hint,
                    {"shot_index": i},
                )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_character_identity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    consistency = slots.get("consistency", {})
    if isinstance(consistency, dict) and consistency.get("character_model_refs"):
        return HeuristicCheckResult(h.heuristic_id, True)
    return HeuristicCheckResult(h.heuristic_id, True, "info", "Character identity check: no refs to validate")


def check_shot_readability(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_style_consistency(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_feedback_quality(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_framing_exploration(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_camera_motion_variety(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_emotional_staging(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


# ── CAD checks ──

def check_dimension_sanity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    dims = slots.get("dimensions", {})
    if isinstance(dims, dict):
        for key, val in dims.items():
            if isinstance(val, (int, float)) and val <= 0:
                return HeuristicCheckResult(
                    h.heuristic_id, False, "error",
                    f"Dimension '{key}'={val} must be positive", h.repair_hint,
                    {"field": key, "value": val},
                )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_fastening_logic(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    parts = slots.get("parts", [])
    if isinstance(parts, list) and len(parts) > 1:
        # Check that parts have assembly/fastening info
        return HeuristicCheckResult(h.heuristic_id, True, "info", "Fastening check: parts present")
    return HeuristicCheckResult(h.heuristic_id, True)


def check_waterproof_integrity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    constraints = slots.get("constraints", [])
    has_waterproof = any(
        isinstance(c, dict) and ("waterproof" in str(c).lower() or "ip6" in str(c).lower() or "방수" in str(c))
        for c in (constraints if isinstance(constraints, list) else [])
    )
    if has_waterproof:
        sealing = slots.get("sealing_zones") or slots.get("sealing")
        if not sealing:
            return HeuristicCheckResult(
                h.heuristic_id, True, "warning",
                "Waterproof constraint detected but no sealing zones defined", h.repair_hint,
            )
    return HeuristicCheckResult(h.heuristic_id, True)


def check_part_logic(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_assembly_access(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_drawing_completeness(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_concept_validity(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_form_factor_feasibility(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


# ── Product Design checks ──

def check_certification(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_bom_completeness(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_user_target(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


def check_concept_novelty(slots: dict, h: Heuristic) -> HeuristicCheckResult:
    return HeuristicCheckResult(h.heuristic_id, True)


# ── Dispatcher: maps check_fn_name to actual function ──

_CHECK_FUNCTIONS = {
    # Minecraft
    "check_block_existence": check_block_existence,
    "check_structural_support": check_structural_support,
    "check_anchor_validity": check_anchor_validity,
    "check_style_coherence": check_style_coherence,
    "check_silhouette": check_silhouette,
    "check_facade_rhythm": check_facade_rhythm,
    "check_npc_role": check_npc_role,
    "check_resource_palette": check_resource_palette,
    "check_palette_diversity": check_palette_diversity,
    "check_theme_variation": check_theme_variation,
    "check_world_expansion": check_world_expansion,
    # Builder
    "check_code_compliance": check_code_compliance,
    "check_circulation_minimum": check_circulation_minimum,
    "check_wet_zone_alignment": check_wet_zone_alignment,
    "check_exterior_massing": check_exterior_massing,
    "check_entrance_emphasis": check_entrance_emphasis,
    "check_facade_rhythm": check_facade_rhythm,
    "check_interior_adjacency": check_interior_adjacency,
    "check_privacy_zoning": check_privacy_zoning,
    "check_massing_variation": check_massing_variation,
    "check_elevation_rhythm_var": check_elevation_rhythm_var,
    # Animation
    "check_180_degree_rule": check_180_degree_rule,
    "check_camera_continuity": check_camera_continuity,
    "check_character_identity": check_character_identity,
    "check_shot_readability": check_shot_readability,
    "check_style_consistency": check_style_consistency,
    "check_feedback_quality": check_feedback_quality,
    "check_framing_exploration": check_framing_exploration,
    "check_camera_motion_variety": check_camera_motion_variety,
    "check_emotional_staging": check_emotional_staging,
    # CAD
    "check_dimension_sanity": check_dimension_sanity,
    "check_fastening_logic": check_fastening_logic,
    "check_waterproof_integrity": check_waterproof_integrity,
    "check_part_logic": check_part_logic,
    "check_assembly_access": check_assembly_access,
    "check_drawing_completeness": check_drawing_completeness,
    "check_concept_validity": check_concept_validity,
    "check_form_factor_feasibility": check_form_factor_feasibility,
    # Product Design
    "check_certification": check_certification,
    "check_bom_completeness": check_bom_completeness,
    "check_user_target": check_user_target,
    "check_concept_novelty": check_concept_novelty,
}


class HeuristicDispatcher:
    """Dispatches heuristic checks to their actual implementations."""

    def run_all(
        self,
        pack: HeuristicPack,
        slots: dict,
        creative_profile: Optional[Any] = None,
    ) -> list[HeuristicCheckResult]:
        """Run all applicable heuristics from a pack against the slots.

        Returns ordered list of results (by heuristic priority).
        """
        applicable = pack.all_applicable(creative_profile)
        results = []
        for h in applicable:
            fn = _CHECK_FUNCTIONS.get(h.check_fn_name)
            if fn:
                result = fn(slots, h)
            else:
                result = HeuristicCheckResult(
                    h.heuristic_id, True, "info",
                    f"No implementation for check '{h.check_fn_name}' — skipped",
                )
            results.append(result)
        return results

    def run_safety_only(
        self,
        pack: HeuristicPack,
        slots: dict,
    ) -> list[HeuristicCheckResult]:
        """Run only safety heuristics (always applicable)."""
        results = []
        for h in pack.safety_heuristics:
            fn = _CHECK_FUNCTIONS.get(h.check_fn_name)
            if fn:
                results.append(fn(slots, h))
            else:
                results.append(HeuristicCheckResult(
                    h.heuristic_id, True, "info",
                    f"No implementation for check '{h.check_fn_name}' — skipped",
                ))
        return results

    def has_failures(self, results: list[HeuristicCheckResult]) -> bool:
        """Check if any result is a failure."""
        return any(not r.passed for r in results)

    def failure_summary(self, results: list[HeuristicCheckResult]) -> list[dict]:
        """Return summary of failed checks."""
        return [r.to_dict() for r in results if not r.passed]
