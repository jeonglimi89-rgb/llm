"""
domain/param_contract.py — Capability parameter contracts.

Each capability has a parameter contract defining:
- required/optional params with types, units, ranges, enums
- coercion policy (safe_only, none)
- missing param policy (fail_loud, use_default, skip)
- invalid param policy (fail_loud, repair_suggest, coerce)
- downstream handoff field mapping

Contract validation runs BEFORE capability execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class ParamSpec:
    """Single parameter specification."""
    name: str
    param_type: str             # "str"|"int"|"float"|"bool"|"list"|"dict"|"enum"
    required: bool = True
    default: Any = None
    unit: str = ""
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    enum_values: list[str] = field(default_factory=list)
    coercion_allowed: bool = True   # False for hard-lock params
    downstream_key: str = ""        # maps to tool adapter input key

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParamContract:
    """Parameter contract for a capability."""
    capability_id: str
    params: list[ParamSpec] = field(default_factory=list)
    coercion_policy: str = "safe_only"      # "safe_only"|"none"
    missing_param_policy: str = "use_default"  # "fail_loud"|"use_default"|"skip"
    invalid_param_policy: str = "repair_suggest"  # "fail_loud"|"repair_suggest"|"coerce"

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "params": [p.to_dict() for p in self.params],
            "coercion_policy": self.coercion_policy,
            "missing_param_policy": self.missing_param_policy,
            "invalid_param_policy": self.invalid_param_policy,
        }

    @property
    def required_params(self) -> list[ParamSpec]:
        return [p for p in self.params if p.required]


@dataclass
class ParamValidationResult:
    """Result of parameter contract validation."""
    passed: bool = True
    capability_id: str = ""
    missing_required: list[str] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    range_violations: list[str] = field(default_factory=list)
    enum_violations: list[str] = field(default_factory=list)
    coerced: list[str] = field(default_factory=list)
    repair_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def all_issues(self) -> list[str]:
        return self.missing_required + self.type_errors + self.range_violations + self.enum_violations


# ── Contracts per capability ──

_CONTRACTS: dict[str, ParamContract] = {
    # ── Minecraft ──
    "minecraft.build_plan_generate": ParamContract(
        capability_id="minecraft.build_plan_generate",
        coercion_policy="safe_only",
        missing_param_policy="use_default",
        invalid_param_policy="repair_suggest",
        params=[
            ParamSpec("target_anchor", "dict", required=True, coercion_allowed=False),
            ParamSpec("build_type", "enum", required=False, default="custom",
                      enum_values=["house", "castle", "tower", "bridge", "farm", "village", "decoration", "redstone", "custom"]),
            ParamSpec("block_palette", "list", required=False, default=[]),
            ParamSpec("dimensions", "dict", required=False),
            ParamSpec("style_theme", "str", required=False),
        ],
    ),
    "minecraft.build_style_validate": ParamContract(
        capability_id="minecraft.build_style_validate",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("block_palette", "list", required=True, coercion_allowed=False),
            ParamSpec("style_theme", "str", required=True, coercion_allowed=False),
        ],
    ),
    "minecraft.npc_concept_generate": ParamContract(
        capability_id="minecraft.npc_concept_generate",
        params=[
            ParamSpec("npc_role", "str", required=True),
            ParamSpec("world_theme", "str", required=False),
            ParamSpec("personality_traits", "list", required=False, default=[]),
        ],
    ),
    "minecraft.resourcepack_style_plan": ParamContract(
        capability_id="minecraft.resourcepack_style_plan",
        params=[
            ParamSpec("pack_theme", "str", required=True),
            ParamSpec("target_blocks", "list", required=False, default=[]),
            ParamSpec("resolution", "enum", required=False, default="16x",
                      enum_values=["16x", "32x", "64x", "128x"]),
        ],
    ),

    # ── Builder ──
    "builder.exterior_drawing_generate": ParamContract(
        capability_id="builder.exterior_drawing_generate",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("floors", "int", required=True, min_val=1, max_val=100, unit="층", coercion_allowed=False),
            ParamSpec("building_type", "enum", required=True, coercion_allowed=False,
                      enum_values=["residential", "commercial", "mixed", "industrial", "public"]),
            ParamSpec("facade_style", "str", required=False),
            ParamSpec("total_area_m2", "float", required=False, min_val=10, max_val=100000, unit="m2"),
        ],
    ),
    "builder.interior_drawing_generate": ParamContract(
        capability_id="builder.interior_drawing_generate",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("floors", "int", required=True, min_val=1, max_val=100, coercion_allowed=False),
            ParamSpec("rooms", "list", required=True, coercion_allowed=False),
            ParamSpec("wet_zones", "list", required=False, default=[]),
            ParamSpec("circulation_type", "enum", required=False, default="corridor",
                      enum_values=["corridor", "open_plan", "mixed"]),
        ],
    ),

    # ── Animation ──
    "animation.camera_walk_plan_generate": ParamContract(
        capability_id="animation.camera_walk_plan_generate",
        coercion_policy="safe_only",
        params=[
            ParamSpec("scene_type", "enum", required=True,
                      enum_values=["dialogue", "action", "transition", "montage", "establishing", "emotional", "climax"]),
            ParamSpec("emotion", "str", required=True),
            ParamSpec("duration_frames", "int", required=False, min_val=1, max_val=7200, unit="frames"),
            ParamSpec("fps", "int", required=False, default=24, min_val=12, max_val=60),
        ],
    ),
    "animation.style_lock_check": ParamContract(
        capability_id="animation.style_lock_check",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("reference_style", "dict", required=True, coercion_allowed=False),
            ParamSpec("check_target", "dict", required=True, coercion_allowed=False),
            ParamSpec("tolerance", "float", required=False, default=0.1, min_val=0.0, max_val=1.0),
        ],
    ),
    "animation.style_feedback_generate": ParamContract(
        capability_id="animation.style_feedback_generate",
        params=[
            ParamSpec("reference_style", "dict", required=True),
            ParamSpec("current_output", "dict", required=True),
            ParamSpec("feedback_depth", "enum", required=False, default="standard",
                      enum_values=["quick", "standard", "detailed"]),
        ],
    ),

    # ── CAD ──
    "cad.design_drawing_generate": ParamContract(
        capability_id="cad.design_drawing_generate",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("product_category", "enum", required=True, coercion_allowed=False,
                      enum_values=["small_appliance", "iot_device", "lighting", "mechanical_part", "enclosure", "custom"]),
            ParamSpec("dimensions", "dict", required=False),
            ParamSpec("material", "str", required=False),
            ParamSpec("sealing_grade", "enum", required=False, coercion_allowed=False,
                      enum_values=["none", "IP54", "IP65", "IP67", "IP68"]),
            ParamSpec("manufacturing_method", "enum", required=False,
                      enum_values=["injection_molding", "cnc", "3d_print", "sheet_metal", "casting", "mixed"]),
            ParamSpec("tolerance_class", "enum", required=False, default="standard",
                      enum_values=["rough", "standard", "precision", "ultra_precision"]),
        ],
    ),
    "cad.assembly_feasibility_check": ParamContract(
        capability_id="cad.assembly_feasibility_check",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("parts", "list", required=True, coercion_allowed=False),
            ParamSpec("assembly_sequence", "list", required=False, default=[]),
        ],
    ),
    "cad.manufacturability_check": ParamContract(
        capability_id="cad.manufacturability_check",
        coercion_policy="none",
        missing_param_policy="fail_loud",
        invalid_param_policy="fail_loud",
        params=[
            ParamSpec("parts", "list", required=True, coercion_allowed=False),
            ParamSpec("manufacturing_method", "enum", required=True, coercion_allowed=False,
                      enum_values=["injection_molding", "cnc", "3d_print", "sheet_metal", "casting", "mixed"]),
        ],
    ),
}


def get_param_contract(capability_id: str) -> Optional[ParamContract]:
    return _CONTRACTS.get(capability_id)


class ParamContractValidator:
    """Validates inputs against capability parameter contracts."""

    def validate(
        self,
        contract: ParamContract,
        inputs: dict[str, Any],
    ) -> tuple[dict[str, Any], ParamValidationResult]:
        """Validate inputs against contract. Returns (coerced_inputs, result)."""
        result = ParamValidationResult(capability_id=contract.capability_id)
        coerced = dict(inputs)
        allow_coercion = contract.coercion_policy == "safe_only"

        # 1. Required params
        for ps in contract.required_params:
            if ps.name not in inputs or inputs[ps.name] is None:
                if contract.missing_param_policy == "use_default" and ps.default is not None:
                    coerced[ps.name] = ps.default
                    result.coerced.append(f"{ps.name}: default={ps.default}")
                elif contract.missing_param_policy == "skip":
                    result.repair_suggestions.append(f"Consider providing '{ps.name}'")
                else:
                    result.missing_required.append(ps.name)

        # 2. Type + range + enum checks
        for ps in contract.params:
            val = coerced.get(ps.name)
            if val is None:
                continue

            # Type check
            if not self._type_ok(ps.param_type, val):
                if allow_coercion and ps.coercion_allowed:
                    coerced_val = self._try_coerce(ps.param_type, val)
                    if coerced_val is not None:
                        coerced[ps.name] = coerced_val
                        result.coerced.append(f"{ps.name}: coerced")
                    else:
                        result.type_errors.append(f"{ps.name}: expected {ps.param_type}")
                else:
                    result.type_errors.append(f"{ps.name}: expected {ps.param_type}, coercion not allowed")

            # Range check
            val = coerced.get(ps.name)
            if isinstance(val, (int, float)):
                if ps.min_val is not None and val < ps.min_val:
                    result.range_violations.append(f"{ps.name}={val} < min {ps.min_val}")
                if ps.max_val is not None and val > ps.max_val:
                    result.range_violations.append(f"{ps.name}={val} > max {ps.max_val}")

            # Enum check
            if ps.enum_values and val is not None:
                if str(val) not in ps.enum_values:
                    result.enum_violations.append(f"{ps.name}='{val}' not in {ps.enum_values}")

        # Determine pass/fail based on policy
        if contract.invalid_param_policy == "fail_loud":
            result.passed = len(result.all_issues) == 0
        else:
            result.passed = len(result.missing_required) == 0

        return coerced, result

    def _type_ok(self, expected: str, val: Any) -> bool:
        if expected == "str":
            return isinstance(val, str)
        elif expected == "int":
            return isinstance(val, int) and not isinstance(val, bool)
        elif expected == "float":
            return isinstance(val, (int, float)) and not isinstance(val, bool)
        elif expected == "bool":
            return isinstance(val, bool)
        elif expected == "list":
            return isinstance(val, list)
        elif expected == "dict":
            return isinstance(val, dict)
        elif expected == "enum":
            return isinstance(val, str)
        return True

    def _try_coerce(self, expected: str, val: Any) -> Optional[Any]:
        try:
            if expected == "str":
                return str(val)
            elif expected == "int":
                return int(float(val))
            elif expected == "float":
                return float(val)
            elif expected == "enum":
                return str(val)
        except (ValueError, TypeError):
            pass
        return None
