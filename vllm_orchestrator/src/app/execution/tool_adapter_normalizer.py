"""
execution/tool_adapter_normalizer.py — Tool Adapter I/O Normalization.

Sits between graph executor and raw tool adapters:
1. graph node input → tool-native input conversion
2. tool raw output → normalized output conversion
3. output schema validation
4. failure classification
5. retryability determination
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum


class FailureType(str, Enum):
    NONE = "none"
    SCHEMA_MISMATCH = "schema_mismatch"
    MISSING_REQUIRED_INPUT = "missing_required_input"
    INVALID_ENUM = "invalid_enum"
    UNIT_RANGE_VIOLATION = "unit_range_violation"
    HARD_LOCK_VIOLATION = "hard_lock_violation"
    TOOL_RUNTIME_FAILURE = "tool_runtime_failure"
    DOWNSTREAM_INCOMPATIBLE = "downstream_incompatible_output"


@dataclass
class NormalizationResult:
    """Result of input/output normalization."""
    success: bool = True
    failure_type: str = FailureType.NONE.value
    failure_detail: str = ""
    retryable: bool = False
    normalized_input: dict[str, Any] = field(default_factory=dict)
    normalized_output: dict[str, Any] = field(default_factory=dict)
    dropped_fields: list[str] = field(default_factory=list)
    added_defaults: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# Tool-specific input key mappings: graph key → tool-native key
_INPUT_MAPPINGS: dict[str, dict[str, str]] = {
    "minecraft.compile_archetype": {
        "target_anchor": "anchor",
        "operations": "ops",
        "block_palette": "palette",
        "style_theme": "theme",
        "build_type": "archetype",
    },
    "builder.generate_plan": {
        "spaces": "program",
        "floors": "floor_count",
        "building_type": "use_type",
        "total_area_m2": "area",
        "wet_zones": "wet_program",
    },
    "animation.solve_shot": {
        "framing": "shot_framing",
        "mood": "scene_mood",
        "camera_move": "camera_motion",
        "duration_frames": "duration",
        "scene_type": "scene_category",
    },
    "cad.generate_part": {
        "constraints": "design_constraints",
        "dimensions": "dims",
        "material": "mat_spec",
        "sealing_grade": "ip_grade",
        "manufacturing_method": "mfg_method",
    },
}

# Tool output key mappings: tool-native key → normalized key
_OUTPUT_MAPPINGS: dict[str, dict[str, str]] = {
    "minecraft.compile_archetype": {
        "blocks": "operations",
        "anchor": "target_anchor",
        "palette": "block_palette",
    },
    "builder.generate_plan": {
        "rooms": "spaces",
        "floor_count": "floors",
    },
    "animation.solve_shot": {
        "shot_list": "shots",
        "framing_result": "framing",
    },
    "cad.generate_part": {
        "part_list": "parts",
        "constraint_list": "constraints",
    },
}

# Required output keys per tool
_REQUIRED_OUTPUTS: dict[str, list[str]] = {
    "minecraft.compile_archetype": ["status"],
    "builder.generate_plan": ["status"],
    "animation.solve_shot": ["status"],
    "cad.generate_part": ["status"],
}

# Failure retryability
_RETRYABLE_FAILURES = {
    FailureType.TOOL_RUNTIME_FAILURE,
    FailureType.SCHEMA_MISMATCH,
}


class ToolAdapterNormalizer:
    """Normalizes I/O between graph nodes and raw tool adapters."""

    def normalize_input(
        self,
        tool_name: str,
        graph_inputs: dict[str, Any],
    ) -> NormalizationResult:
        """Convert graph node inputs to tool-native format."""
        mapping = _INPUT_MAPPINGS.get(tool_name, {})
        normalized = {}
        dropped = []

        for key, val in graph_inputs.items():
            if key.startswith("_"):  # skip metadata keys
                continue
            native_key = mapping.get(key, key)
            normalized[native_key] = val

        return NormalizationResult(
            success=True,
            normalized_input=normalized,
            dropped_fields=dropped,
        )

    def normalize_output(
        self,
        tool_name: str,
        raw_output: dict[str, Any],
    ) -> NormalizationResult:
        """Convert tool raw output to normalized graph format."""
        if not raw_output:
            return NormalizationResult(
                success=False,
                failure_type=FailureType.TOOL_RUNTIME_FAILURE.value,
                failure_detail="Tool returned empty output",
                retryable=True,
            )

        # Check for tool error
        if "error" in raw_output and raw_output.get("status") != "executed":
            return NormalizationResult(
                success=False,
                failure_type=FailureType.TOOL_RUNTIME_FAILURE.value,
                failure_detail=str(raw_output.get("error", "")),
                retryable=True,
                normalized_output=raw_output,
            )

        mapping = _OUTPUT_MAPPINGS.get(tool_name, {})
        normalized = {}

        for key, val in raw_output.items():
            norm_key = mapping.get(key, key)
            normalized[norm_key] = val

        # Check required outputs
        required = _REQUIRED_OUTPUTS.get(tool_name, [])
        missing = [k for k in required if k not in normalized]
        if missing:
            return NormalizationResult(
                success=False,
                failure_type=FailureType.SCHEMA_MISMATCH.value,
                failure_detail=f"Missing required outputs: {missing}",
                retryable=False,
                normalized_output=normalized,
            )

        return NormalizationResult(
            success=True,
            normalized_output=normalized,
        )

    def classify_failure(
        self,
        error: Exception,
        tool_name: str,
    ) -> NormalizationResult:
        """Classify a tool execution failure."""
        err_str = str(error).lower()

        if "missing" in err_str or "required" in err_str:
            ft = FailureType.MISSING_REQUIRED_INPUT
        elif "enum" in err_str or "invalid value" in err_str:
            ft = FailureType.INVALID_ENUM
        elif "range" in err_str or "out of bounds" in err_str:
            ft = FailureType.UNIT_RANGE_VIOLATION
        elif "lock" in err_str or "violation" in err_str:
            ft = FailureType.HARD_LOCK_VIOLATION
        else:
            ft = FailureType.TOOL_RUNTIME_FAILURE

        return NormalizationResult(
            success=False,
            failure_type=ft.value,
            failure_detail=str(error),
            retryable=ft in _RETRYABLE_FAILURES,
        )

    def check_downstream_compatibility(
        self,
        output: dict[str, Any],
        expected_keys: list[str],
    ) -> NormalizationResult:
        """Check if output is compatible with downstream consumer."""
        missing = [k for k in expected_keys if k not in output]
        if missing:
            return NormalizationResult(
                success=False,
                failure_type=FailureType.DOWNSTREAM_INCOMPATIBLE.value,
                failure_detail=f"Missing keys for downstream: {missing}",
                normalized_output=output,
            )
        return NormalizationResult(success=True, normalized_output=output)
