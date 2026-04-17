"""Unit tests for param_contract module."""
import pytest
from vllm_orchestrator.src.app.domain.param_contract import (
    ParamContractValidator, ParamContract, ParamSpec, ParamValidationResult,
    get_param_contract,
)


@pytest.fixture
def validator():
    return ParamContractValidator()


class TestParamContractValidator:
    def test_minecraft_build_valid(self, validator):
        contract = get_param_contract("minecraft.build_plan_generate")
        inputs = {"target_anchor": {"type": "relative"}, "build_type": "tower"}
        _, result = validator.validate(contract, inputs)
        assert result.passed

    def test_minecraft_build_missing_anchor(self, validator):
        contract = get_param_contract("minecraft.build_plan_generate")
        inputs = {"build_type": "tower"}
        _, result = validator.validate(contract, inputs)
        assert not result.passed
        assert "target_anchor" in result.missing_required

    def test_builder_exterior_strict(self, validator):
        """Builder uses fail_loud for missing params."""
        contract = get_param_contract("builder.exterior_drawing_generate")
        inputs = {"building_type": "residential"}  # missing floors
        _, result = validator.validate(contract, inputs)
        assert not result.passed

    def test_builder_exterior_valid(self, validator):
        contract = get_param_contract("builder.exterior_drawing_generate")
        inputs = {"floors": 2, "building_type": "residential"}
        _, result = validator.validate(contract, inputs)
        assert result.passed

    def test_cad_design_strict_no_coercion(self, validator):
        """CAD uses coercion_policy='none'."""
        contract = get_param_contract("cad.design_drawing_generate")
        inputs = {"product_category": 123}  # should be str enum, no coercion
        _, result = validator.validate(contract, inputs)
        assert not result.passed

    def test_cad_design_valid(self, validator):
        contract = get_param_contract("cad.design_drawing_generate")
        inputs = {"product_category": "small_appliance"}
        _, result = validator.validate(contract, inputs)
        assert result.passed

    def test_animation_camera_coercion(self, validator):
        contract = get_param_contract("animation.camera_walk_plan_generate")
        inputs = {"scene_type": "dialogue", "emotion": "tension", "fps": "24"}
        coerced, result = validator.validate(contract, inputs)
        # safe_only coercion: string "24" -> int 24
        assert result.passed

    def test_animation_style_lock_no_coercion(self, validator):
        """Style lock uses coercion_policy='none'."""
        contract = get_param_contract("animation.style_lock_check")
        inputs = {"reference_style": "not_a_dict", "check_target": {}}
        _, result = validator.validate(contract, inputs)
        assert not result.passed

    def test_enum_violation(self, validator):
        contract = get_param_contract("cad.design_drawing_generate")
        inputs = {"product_category": "INVALID_CATEGORY"}
        _, result = validator.validate(contract, inputs)
        assert len(result.enum_violations) > 0

    def test_range_violation(self, validator):
        contract = get_param_contract("builder.exterior_drawing_generate")
        inputs = {"floors": 0, "building_type": "residential"}
        _, result = validator.validate(contract, inputs)
        assert len(result.range_violations) > 0

    def test_result_to_dict(self, validator):
        contract = get_param_contract("minecraft.build_plan_generate")
        _, result = validator.validate(contract, {"target_anchor": {}})
        d = result.to_dict()
        assert "capability_id" in d
        assert "passed" in d

    def test_all_registered_contracts(self):
        """Verify contracts exist for key capabilities."""
        cap_ids = [
            "minecraft.build_plan_generate",
            "builder.exterior_drawing_generate",
            "builder.interior_drawing_generate",
            "animation.camera_walk_plan_generate",
            "animation.style_lock_check",
            "cad.design_drawing_generate",
            "cad.manufacturability_check",
        ]
        for cid in cap_ids:
            assert get_param_contract(cid) is not None, f"Missing contract for {cid}"
