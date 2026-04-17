"""Unit tests for input_schemas module."""
import pytest
from vllm_orchestrator.src.app.domain.input_schemas import (
    InputSchemaValidator, InputSchema, FieldSpec, InputValidationResult,
    INPUT_SCHEMA_REGISTRY, get_input_schema, get_schema_for_family,
    list_schemas_for_domain, SCHEMA_VERSION,
)


@pytest.fixture
def validator():
    return InputSchemaValidator()


class TestInputSchemaRegistry:
    def test_all_9_schemas_registered(self):
        assert len(INPUT_SCHEMA_REGISTRY) == 9

    def test_minecraft_schemas(self):
        schemas = list_schemas_for_domain("minecraft")
        assert len(schemas) == 3
        families = {s.task_family for s in schemas}
        assert families == {"build", "npc", "resourcepack"}

    def test_builder_schemas(self):
        schemas = list_schemas_for_domain("builder")
        assert len(schemas) == 2
        families = {s.task_family for s in schemas}
        assert families == {"exterior_drawing", "interior_drawing"}

    def test_animation_schemas(self):
        schemas = list_schemas_for_domain("animation")
        assert len(schemas) == 3

    def test_cad_schemas(self):
        schemas = list_schemas_for_domain("cad")
        assert len(schemas) == 1

    def test_get_by_family(self):
        s = get_schema_for_family("build")
        assert s is not None
        assert s.app_domain == "minecraft"

    def test_all_schemas_have_common_fields(self):
        common = {"request_id", "app_domain", "task_family", "objective", "constraints"}
        for sid, schema in INPUT_SCHEMA_REGISTRY.items():
            names = schema.field_names
            for c in common:
                assert c in names, f"{sid} missing common field '{c}'"

    def test_all_schemas_have_required_outputs(self):
        for sid, schema in INPUT_SCHEMA_REGISTRY.items():
            assert any(f.name == "required_outputs" for f in schema.fields), \
                f"{sid} missing required_outputs field"

    def test_schema_version(self):
        for schema in INPUT_SCHEMA_REGISTRY.values():
            assert schema.schema_version == SCHEMA_VERSION


class TestInputSchemaValidator:
    def test_valid_minecraft_build(self, validator):
        schema = get_schema_for_family("build")
        inputs = {
            "app_domain": "minecraft",
            "task_family": "build",
            "objective": "중세풍 타워 만들기",
            "build_type": "tower",
            "anchor_type": "relative",
            "required_outputs": ["operations", "target_anchor"],
        }
        coerced, result = validator.validate(schema, inputs)
        assert result.passed
        assert len(result.missing_required) == 0

    def test_missing_required_field(self, validator):
        schema = get_schema_for_family("build")
        inputs = {
            "app_domain": "minecraft",
            "task_family": "build",
            # missing objective, build_type, anchor_type
        }
        _, result = validator.validate(schema, inputs)
        assert not result.passed
        assert "objective" in result.missing_required

    def test_enum_violation(self, validator):
        schema = get_schema_for_family("build")
        inputs = {
            "app_domain": "minecraft",
            "task_family": "build",
            "objective": "test",
            "build_type": "INVALID_TYPE",
            "anchor_type": "relative",
            "required_outputs": ["operations"],
        }
        _, result = validator.validate(schema, inputs)
        assert len(result.enum_violations) > 0

    def test_range_violation(self, validator):
        schema = get_schema_for_family("build")
        inputs = {
            "app_domain": "minecraft",
            "task_family": "build",
            "objective": "test",
            "build_type": "house",
            "anchor_type": "relative",
            "max_block_types": 999,  # max is 50
            "required_outputs": ["operations"],
        }
        _, result = validator.validate(schema, inputs)
        assert len(result.range_violations) > 0

    def test_type_coercion(self, validator):
        schema = get_schema_for_family("camera_walking")
        inputs = {
            "app_domain": "animation",
            "task_family": "camera_walking",
            "objective": "test",
            "scene_type": "dialogue",
            "emotion": "tension",
            "duration_frames": "144",  # string, should coerce to int
            "required_outputs": ["framing"],
        }
        coerced, result = validator.validate(schema, inputs, allow_coercion=True)
        assert result.passed
        assert coerced["duration_frames"] == 144

    def test_strict_mode(self, validator):
        schema = get_schema_for_family("design_drawing")
        inputs = {
            "app_domain": "cad",
            "task_family": "design_drawing",
            "objective": "test",
            "product_category": "INVALID",
            "required_outputs": ["constraints"],
        }
        _, result = validator.validate(schema, inputs, strict_mode=True)
        assert not result.passed

    def test_cad_required_fields(self, validator):
        schema = get_schema_for_family("design_drawing")
        inputs = {
            "app_domain": "cad",
            "task_family": "design_drawing",
            "objective": "샤워 필터 설계",
            "product_category": "small_appliance",
            "required_outputs": ["constraints", "systems"],
        }
        _, result = validator.validate(schema, inputs)
        assert result.passed

    def test_builder_interior_valid(self, validator):
        schema = get_schema_for_family("interior_drawing")
        inputs = {
            "app_domain": "builder",
            "task_family": "interior_drawing",
            "objective": "2층 주택 내부",
            "floors": 2,
            "rooms": [{"name": "거실", "area_m2": 25}],
            "required_outputs": ["spaces"],
        }
        _, result = validator.validate(schema, inputs)
        assert result.passed

    def test_default_applied_for_required(self, validator):
        schema = get_schema_for_family("build")
        inputs = {
            "app_domain": "minecraft",
            "task_family": "build",
            "objective": "타워",
            "build_type": "tower",
            "anchor_type": "relative",
            # required_outputs is required with a default
        }
        coerced, result = validator.validate(schema, inputs)
        # required_outputs has default=["operations", "target_anchor"]
        assert "required_outputs" in coerced
