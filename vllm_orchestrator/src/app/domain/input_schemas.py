"""
domain/input_schemas.py — Canonical input schemas for each app task family.

Each schema defines:
- required/optional fields with types, units, ranges, enums
- validation logic
- backward compatibility via schema_version

Schema-first: every request entering the pipeline must conform to its
task family's input schema. Freeform text dependency is minimized.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum


# ── Schema version ──
SCHEMA_VERSION = "1.0.0"


@dataclass
class FieldSpec:
    """Specification for a single schema field."""
    name: str
    field_type: str             # "str"|"int"|"float"|"bool"|"list"|"dict"|"enum"
    required: bool = True
    default: Any = None
    unit: str = ""              # "mm"|"m"|"m2"|"frames"|"fps"|"degrees" etc.
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    enum_values: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InputSchema:
    """Canonical input schema for a task family."""
    schema_id: str              # "minecraft.build_generation_request"
    schema_version: str = SCHEMA_VERSION
    app_domain: str = ""
    task_family: str = ""
    fields: list[FieldSpec] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "app_domain": self.app_domain,
            "task_family": self.task_family,
            "fields": [f.to_dict() for f in self.fields],
            "description": self.description,
        }

    @property
    def required_fields(self) -> list[FieldSpec]:
        return [f for f in self.fields if f.required]

    @property
    def optional_fields(self) -> list[FieldSpec]:
        return [f for f in self.fields if not f.required]

    @property
    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}


@dataclass
class InputValidationResult:
    """Result of validating input against a schema."""
    passed: bool = True
    schema_id: str = ""
    missing_required: list[str] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    range_violations: list[str] = field(default_factory=list)
    enum_violations: list[str] = field(default_factory=list)
    coerced_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def all_issues(self) -> list[str]:
        return self.missing_required + self.type_errors + self.range_violations + self.enum_violations


# ── Common fields (shared across all schemas) ──

_COMMON_FIELDS = [
    FieldSpec("request_id", "str", required=False, description="Auto-generated if absent"),
    FieldSpec("app_domain", "str", required=True, description="Target app domain"),
    FieldSpec("task_family", "str", required=True, description="Task family within the domain"),
    FieldSpec("objective", "str", required=True, description="User's primary goal in structured form"),
    FieldSpec("constraints", "list", required=False, default=[], description="Hard constraints"),
    FieldSpec("style_or_theme", "str", required=False, default="", description="Style/theme preference"),
    FieldSpec("creative_profile", "dict", required=False, description="Creative profile override"),
    FieldSpec("validation_targets", "list", required=False, default=[], description="What to validate"),
    FieldSpec("fail_conditions", "list", required=False, default=[], description="Conditions that must cause failure"),
]


# ── Minecraft schemas ──

MINECRAFT_BUILD_SCHEMA = InputSchema(
    schema_id="minecraft.build_generation_request",
    app_domain="minecraft",
    task_family="build",
    description="Request to generate a Minecraft build plan",
    fields=_COMMON_FIELDS + [
        FieldSpec("build_type", "enum", required=True,
                  enum_values=["house", "castle", "tower", "bridge", "farm", "village", "decoration", "redstone", "custom"],
                  description="Type of build"),
        FieldSpec("anchor_type", "enum", required=True,
                  enum_values=["relative", "absolute", "named_location"],
                  description="How the build is positioned"),
        FieldSpec("anchor_position", "dict", required=False, description="x/y/z coordinates"),
        FieldSpec("dimensions", "dict", required=False,
                  description="width/depth/height in blocks"),
        FieldSpec("block_palette", "list", required=False, default=[],
                  description="Preferred block types"),
        FieldSpec("biome", "str", required=False, description="Target biome"),
        FieldSpec("survival_friendly", "bool", required=False, default=True,
                  description="Must be buildable in survival mode"),
        FieldSpec("max_block_types", "int", required=False, min_val=1, max_val=50,
                  description="Maximum unique block types"),
        FieldSpec("required_outputs", "list", required=True, default=["operations", "target_anchor"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False, default=["block_palette", "style"],
                  description="Optional output fields"),
    ],
)

MINECRAFT_NPC_SCHEMA = InputSchema(
    schema_id="minecraft.npc_generation_request",
    app_domain="minecraft",
    task_family="npc",
    description="Request to generate NPC concept",
    fields=_COMMON_FIELDS + [
        FieldSpec("npc_role", "str", required=True, description="NPC's role in the world"),
        FieldSpec("world_theme", "str", required=False, description="World theme for consistency"),
        FieldSpec("personality_traits", "list", required=False, default=[],
                  description="Desired personality traits"),
        FieldSpec("dialogue_style", "str", required=False, description="Dialogue tone"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["npc_concept", "role_description"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["dialogue_samples", "appearance"],
                  description="Optional output fields"),
    ],
)

MINECRAFT_RESOURCEPACK_SCHEMA = InputSchema(
    schema_id="minecraft.resourcepack_generation_request",
    app_domain="minecraft",
    task_family="resourcepack",
    description="Request to generate resource pack style plan",
    fields=_COMMON_FIELDS + [
        FieldSpec("pack_theme", "str", required=True, description="Resource pack theme"),
        FieldSpec("target_blocks", "list", required=False, default=[],
                  description="Blocks to retexture"),
        FieldSpec("color_palette", "list", required=False, default=[],
                  description="Color palette hex codes"),
        FieldSpec("resolution", "enum", required=False, default="16x",
                  enum_values=["16x", "32x", "64x", "128x"],
                  description="Texture resolution"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["style_plan", "palette"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["texture_previews"],
                  description="Optional output fields"),
    ],
)

# ── Builder schemas ──

BUILDER_EXTERIOR_SCHEMA = InputSchema(
    schema_id="builder.exterior_drawing_request",
    app_domain="builder",
    task_family="exterior_drawing",
    description="Request to generate building exterior drawing",
    fields=_COMMON_FIELDS + [
        FieldSpec("building_type", "enum", required=True,
                  enum_values=["residential", "commercial", "mixed", "industrial", "public"],
                  description="Building use type"),
        FieldSpec("floors", "int", required=True, min_val=1, max_val=100,
                  unit="층", description="Number of floors"),
        FieldSpec("total_area_m2", "float", required=False, min_val=10, max_val=100000,
                  unit="m2", description="Total floor area"),
        FieldSpec("lot_area_m2", "float", required=False, min_val=10, max_val=100000,
                  unit="m2", description="Lot area"),
        FieldSpec("facade_style", "str", required=False, description="Desired facade style"),
        FieldSpec("zoning_code", "str", required=False, description="Applicable zoning code"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["exterior_plan", "massing", "facade"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["elevation", "section"],
                  description="Optional output fields"),
    ],
)

BUILDER_INTERIOR_SCHEMA = InputSchema(
    schema_id="builder.interior_drawing_request",
    app_domain="builder",
    task_family="interior_drawing",
    description="Request to generate building interior drawing",
    fields=_COMMON_FIELDS + [
        FieldSpec("floors", "int", required=True, min_val=1, max_val=100, unit="층"),
        FieldSpec("rooms", "list", required=True, description="List of {name, area_m2, priority}"),
        FieldSpec("wet_zones", "list", required=False, default=[],
                  description="Kitchen/bathroom locations"),
        FieldSpec("circulation_type", "enum", required=False, default="corridor",
                  enum_values=["corridor", "open_plan", "mixed"],
                  description="Circulation pattern"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["spaces", "adjacency", "circulation"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["furniture_layout", "mep_zones"],
                  description="Optional output fields"),
    ],
)

# ── Animation schemas ──

ANIMATION_CAMERA_SCHEMA = InputSchema(
    schema_id="animation.camera_walking_request",
    app_domain="animation",
    task_family="camera_walking",
    description="Request to generate camera walking plan",
    fields=_COMMON_FIELDS + [
        FieldSpec("scene_type", "enum", required=True,
                  enum_values=["dialogue", "action", "transition", "montage", "establishing", "emotional", "climax"],
                  description="Type of scene"),
        FieldSpec("emotion", "str", required=True, description="Primary emotion to convey"),
        FieldSpec("duration_frames", "int", required=False, min_val=1, max_val=7200,
                  unit="frames", description="Scene duration in frames"),
        FieldSpec("fps", "int", required=False, default=24, min_val=12, max_val=60,
                  unit="fps", description="Frames per second"),
        FieldSpec("characters", "list", required=False, default=[],
                  description="Characters in the scene"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["framing", "mood", "camera_move"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["shots", "lighting", "continuity_anchors"],
                  description="Optional output fields"),
    ],
)

ANIMATION_STYLE_LOCK_SCHEMA = InputSchema(
    schema_id="animation.style_lock_request",
    app_domain="animation",
    task_family="style_lock",
    description="Request to check style lock compliance",
    fields=_COMMON_FIELDS + [
        FieldSpec("reference_style", "dict", required=True,
                  description="Reference style guide {art_style, color_palette, line_weight}"),
        FieldSpec("check_target", "dict", required=True,
                  description="Target output to check against reference"),
        FieldSpec("tolerance", "float", required=False, default=0.1, min_val=0.0, max_val=1.0,
                  description="Acceptable style deviation"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["style_compliance", "drift_score"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["drift_details", "repair_suggestions"],
                  description="Optional output fields"),
    ],
)

ANIMATION_STYLE_FEEDBACK_SCHEMA = InputSchema(
    schema_id="animation.style_feedback_request",
    app_domain="animation",
    task_family="style_feedback",
    description="Request to generate style feedback",
    fields=_COMMON_FIELDS + [
        FieldSpec("reference_style", "dict", required=True,
                  description="Reference style guide"),
        FieldSpec("current_output", "dict", required=True,
                  description="Current output to evaluate"),
        FieldSpec("feedback_depth", "enum", required=False, default="standard",
                  enum_values=["quick", "standard", "detailed"],
                  description="Level of feedback detail"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["feedback_items", "severity_scores"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["repair_steps", "before_after_comparison"],
                  description="Optional output fields"),
    ],
)

# ── CAD schema ──

CAD_DESIGN_SCHEMA = InputSchema(
    schema_id="cad.design_drawing_request",
    app_domain="cad",
    task_family="design_drawing",
    description="Request to generate engineering design drawing",
    fields=_COMMON_FIELDS + [
        FieldSpec("product_category", "enum", required=True,
                  enum_values=["small_appliance", "iot_device", "lighting", "mechanical_part", "enclosure", "custom"],
                  description="Product category"),
        FieldSpec("dimensions", "dict", required=False,
                  description="{width_mm, depth_mm, height_mm}"),
        FieldSpec("material", "str", required=False, description="Primary material"),
        FieldSpec("sealing_grade", "enum", required=False,
                  enum_values=["none", "IP54", "IP65", "IP67", "IP68"],
                  description="Waterproof/dustproof grade"),
        FieldSpec("power_requirements", "dict", required=False,
                  description="{voltage, current, connector_type}"),
        FieldSpec("manufacturing_method", "enum", required=False,
                  enum_values=["injection_molding", "cnc", "3d_print", "sheet_metal", "casting", "mixed"],
                  description="Primary manufacturing method"),
        FieldSpec("tolerance_class", "enum", required=False, default="standard",
                  enum_values=["rough", "standard", "precision", "ultra_precision"],
                  description="Dimensional tolerance class"),
        FieldSpec("required_outputs", "list", required=True,
                  default=["constraints", "systems", "parts"],
                  description="Required output fields"),
        FieldSpec("optional_outputs", "list", required=False,
                  default=["wiring", "drainage", "assembly_sequence"],
                  description="Optional output fields"),
    ],
)


# ── Schema Registry ──

INPUT_SCHEMA_REGISTRY: dict[str, InputSchema] = {
    "minecraft.build_generation_request": MINECRAFT_BUILD_SCHEMA,
    "minecraft.npc_generation_request": MINECRAFT_NPC_SCHEMA,
    "minecraft.resourcepack_generation_request": MINECRAFT_RESOURCEPACK_SCHEMA,
    "builder.exterior_drawing_request": BUILDER_EXTERIOR_SCHEMA,
    "builder.interior_drawing_request": BUILDER_INTERIOR_SCHEMA,
    "animation.camera_walking_request": ANIMATION_CAMERA_SCHEMA,
    "animation.style_lock_request": ANIMATION_STYLE_LOCK_SCHEMA,
    "animation.style_feedback_request": ANIMATION_STYLE_FEEDBACK_SCHEMA,
    "cad.design_drawing_request": CAD_DESIGN_SCHEMA,
}

# Task family -> schema id mapping
_FAMILY_TO_SCHEMA: dict[str, str] = {
    "build": "minecraft.build_generation_request",
    "npc": "minecraft.npc_generation_request",
    "resourcepack": "minecraft.resourcepack_generation_request",
    "exterior_drawing": "builder.exterior_drawing_request",
    "interior_drawing": "builder.interior_drawing_request",
    "camera_walking": "animation.camera_walking_request",
    "style_lock": "animation.style_lock_request",
    "style_feedback": "animation.style_feedback_request",
    "design_drawing": "cad.design_drawing_request",
}


def get_input_schema(schema_id: str) -> Optional[InputSchema]:
    return INPUT_SCHEMA_REGISTRY.get(schema_id)


def get_schema_for_family(task_family: str) -> Optional[InputSchema]:
    sid = _FAMILY_TO_SCHEMA.get(task_family)
    return INPUT_SCHEMA_REGISTRY.get(sid) if sid else None


def list_schemas_for_domain(domain: str) -> list[InputSchema]:
    return [s for s in INPUT_SCHEMA_REGISTRY.values() if s.app_domain == domain]


# ── Input Validator ──

class InputSchemaValidator:
    """Validates request inputs against canonical schemas."""

    def validate(
        self,
        schema: InputSchema,
        inputs: dict[str, Any],
        *,
        allow_coercion: bool = True,
        strict_mode: bool = False,
    ) -> tuple[dict[str, Any], InputValidationResult]:
        """Validate inputs against schema. Returns (possibly-coerced inputs, result).

        Args:
            schema: The canonical input schema
            inputs: Raw input dict
            allow_coercion: If True, attempt safe type coercion
            strict_mode: If True, any issue is a failure (CAD/Builder)
        """
        result = InputValidationResult(schema_id=schema.schema_id)
        coerced = dict(inputs)

        # 1. Check required fields
        for fs in schema.required_fields:
            if fs.name not in inputs or inputs[fs.name] is None:
                if fs.default is not None:
                    coerced[fs.name] = fs.default
                    result.coerced_fields.append(f"{fs.name}: used default {fs.default}")
                else:
                    result.missing_required.append(fs.name)

        # 2. Type check + coercion
        for fs in schema.fields:
            val = coerced.get(fs.name)
            if val is None:
                continue
            type_ok, coerced_val = self._check_type(fs, val, allow_coercion)
            if not type_ok:
                result.type_errors.append(f"{fs.name}: expected {fs.field_type}, got {type(val).__name__}")
            elif coerced_val is not val:
                coerced[fs.name] = coerced_val
                result.coerced_fields.append(f"{fs.name}: coerced to {fs.field_type}")

        # 3. Range check
        for fs in schema.fields:
            val = coerced.get(fs.name)
            if val is None:
                continue
            if fs.min_val is not None and isinstance(val, (int, float)):
                if val < fs.min_val:
                    result.range_violations.append(f"{fs.name}={val} < min {fs.min_val}")
            if fs.max_val is not None and isinstance(val, (int, float)):
                if val > fs.max_val:
                    result.range_violations.append(f"{fs.name}={val} > max {fs.max_val}")

        # 4. Enum check
        for fs in schema.fields:
            val = coerced.get(fs.name)
            if val is None or not fs.enum_values:
                continue
            if fs.field_type == "enum" and str(val) not in fs.enum_values:
                result.enum_violations.append(
                    f"{fs.name}='{val}' not in {fs.enum_values}"
                )

        # Determine pass/fail
        if strict_mode:
            result.passed = len(result.all_issues) == 0
        else:
            result.passed = len(result.missing_required) == 0 and len(result.type_errors) == 0

        return coerced, result

    def _check_type(
        self, fs: FieldSpec, val: Any, allow_coercion: bool,
    ) -> tuple[bool, Any]:
        """Check type and optionally coerce. Returns (ok, possibly_coerced_val)."""
        expected = fs.field_type
        if expected == "str":
            if isinstance(val, str):
                return True, val
            if allow_coercion:
                return True, str(val)
            return False, val
        elif expected == "int":
            if isinstance(val, int) and not isinstance(val, bool):
                return True, val
            if allow_coercion and isinstance(val, (float, str)):
                try:
                    return True, int(float(val))
                except (ValueError, TypeError):
                    pass
            return False, val
        elif expected == "float":
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return True, float(val)
            if allow_coercion and isinstance(val, str):
                try:
                    return True, float(val)
                except ValueError:
                    pass
            return False, val
        elif expected == "bool":
            if isinstance(val, bool):
                return True, val
            return False, val
        elif expected == "list":
            if isinstance(val, list):
                return True, val
            return False, val
        elif expected == "dict":
            if isinstance(val, dict):
                return True, val
            return False, val
        elif expected == "enum":
            # Enum is stored as str
            if isinstance(val, str):
                return True, val
            if allow_coercion:
                return True, str(val)
            return False, val
        return True, val
