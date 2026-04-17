"""
orchestration/cross_domain_handoff.py — Cross-domain dependency reasoning.

Defines allowed handoff connections between domains and validates
that cross-domain data flows use approved handoff schemas.

Currently supported handoffs:
1. builder output → cad input (building plan → engineering detail)
2. animation camera plan → style feedback (camera plan → style check)

All other cross-domain connections are forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class HandoffSpec:
    """Specification for an allowed cross-domain handoff."""
    handoff_id: str
    source_domain: str
    source_task_family: str
    target_domain: str
    target_task_family: str
    required_keys: list[str] = field(default_factory=list)
    key_mapping: dict[str, str] = field(default_factory=dict)  # source_key → target_key
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HandoffValidationResult:
    """Result of handoff validation."""
    valid: bool = True
    handoff_id: str = ""
    missing_keys: list[str] = field(default_factory=list)
    mapped_output: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Allowed handoff map ──

_HANDOFF_MAP: dict[str, HandoffSpec] = {
    "builder_to_cad": HandoffSpec(
        handoff_id="builder_to_cad",
        source_domain="builder",
        source_task_family="exterior_drawing",
        target_domain="cad",
        target_task_family="design_drawing",
        required_keys=["spaces", "floors"],
        key_mapping={
            "spaces": "room_specifications",
            "floors": "floor_count",
            "total_area_m2": "target_area_m2",
            "building_type": "building_category",
            "constraints": "constraints",
        },
        description="Building plan provides spatial constraints for engineering design",
    ),
    "animation_camera_to_style_feedback": HandoffSpec(
        handoff_id="animation_camera_to_style_feedback",
        source_domain="animation",
        source_task_family="camera_walking",
        target_domain="animation",
        target_task_family="style_feedback",
        required_keys=["framing", "mood"],
        key_mapping={
            "framing": "framing",
            "mood": "mood",
            "shots": "shots",
            "camera_move": "camera_move",
        },
        description="Camera plan feeds into style consistency verification",
    ),
}


def get_handoff(source_domain: str, target_domain: str, target_family: str = "") -> Optional[HandoffSpec]:
    """Find an allowed handoff between domains."""
    for spec in _HANDOFF_MAP.values():
        if spec.source_domain == source_domain and spec.target_domain == target_domain:
            if not target_family or spec.target_task_family == target_family:
                return spec
    return None


def list_allowed_handoffs() -> list[HandoffSpec]:
    return list(_HANDOFF_MAP.values())


class CrossDomainHandoffManager:
    """Manages cross-domain data handoffs between graph executions."""

    def validate_handoff(
        self,
        source_domain: str,
        target_domain: str,
        source_output: dict[str, Any],
        target_family: str = "",
    ) -> HandoffValidationResult:
        """Validate and map a cross-domain handoff.

        Args:
            source_domain: Domain that produced the output
            target_domain: Domain that will consume it
            source_output: Output from the source domain
            target_family: Target task family (for disambiguation)

        Returns:
            HandoffValidationResult with mapped output or failure reason
        """
        spec = get_handoff(source_domain, target_domain, target_family)

        if spec is None:
            return HandoffValidationResult(
                valid=False,
                reason=(
                    f"No allowed handoff from '{source_domain}' to '{target_domain}'. "
                    f"Cross-domain chaining must use approved handoff specs."
                ),
            )

        # Check required keys in source output
        missing = [k for k in spec.required_keys if k not in source_output]
        if missing:
            return HandoffValidationResult(
                valid=False,
                handoff_id=spec.handoff_id,
                missing_keys=missing,
                reason=f"Source output missing required keys for handoff: {missing}",
            )

        # Map keys
        mapped = {}
        for src_key, tgt_key in spec.key_mapping.items():
            if src_key in source_output:
                mapped[tgt_key] = source_output[src_key]

        return HandoffValidationResult(
            valid=True,
            handoff_id=spec.handoff_id,
            mapped_output=mapped,
        )

    def is_handoff_allowed(
        self,
        source_domain: str,
        target_domain: str,
    ) -> bool:
        """Check if any handoff exists between two domains."""
        return get_handoff(source_domain, target_domain) is not None
