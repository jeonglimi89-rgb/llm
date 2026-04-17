"""Unit tests for cross_domain_handoff module."""
import pytest
from vllm_orchestrator.src.app.orchestration.cross_domain_handoff import (
    CrossDomainHandoffManager, HandoffSpec, HandoffValidationResult,
    get_handoff, list_allowed_handoffs,
)


@pytest.fixture
def manager():
    return CrossDomainHandoffManager()


class TestHandoffRegistry:
    def test_builder_to_cad_exists(self):
        spec = get_handoff("builder", "cad")
        assert spec is not None
        assert spec.handoff_id == "builder_to_cad"

    def test_animation_camera_to_feedback_exists(self):
        spec = get_handoff("animation", "animation", "style_feedback")
        assert spec is not None

    def test_minecraft_to_builder_not_allowed(self):
        spec = get_handoff("minecraft", "builder")
        assert spec is None

    def test_cad_to_animation_not_allowed(self):
        spec = get_handoff("cad", "animation")
        assert spec is None

    def test_list_all_handoffs(self):
        handoffs = list_allowed_handoffs()
        assert len(handoffs) >= 2


class TestCrossDomainHandoffManager:
    def test_valid_builder_to_cad(self, manager):
        source_output = {
            "spaces": [{"name": "living", "area_m2": 25}],
            "floors": 2,
            "total_area_m2": 120,
            "building_type": "residential",
        }
        result = manager.validate_handoff("builder", "cad", source_output)
        assert result.valid
        assert "room_specifications" in result.mapped_output
        assert "floor_count" in result.mapped_output

    def test_builder_to_cad_missing_keys(self, manager):
        source_output = {"style": "modern"}  # missing spaces, floors
        result = manager.validate_handoff("builder", "cad", source_output)
        assert not result.valid
        assert len(result.missing_keys) > 0

    def test_animation_camera_to_feedback(self, manager):
        source_output = {
            "framing": "close_up",
            "mood": "tense",
            "shots": [{"shot_type": "close_up"}],
        }
        result = manager.validate_handoff("animation", "animation", source_output, "style_feedback")
        assert result.valid
        assert "framing" in result.mapped_output

    def test_forbidden_handoff(self, manager):
        result = manager.validate_handoff("minecraft", "cad", {"ops": [1]})
        assert not result.valid
        assert "No allowed handoff" in result.reason

    def test_is_handoff_allowed(self, manager):
        assert manager.is_handoff_allowed("builder", "cad")
        assert not manager.is_handoff_allowed("minecraft", "cad")
        assert not manager.is_handoff_allowed("cad", "minecraft")

    def test_result_to_dict(self, manager):
        result = manager.validate_handoff("builder", "cad", {"spaces": [], "floors": 1})
        d = result.to_dict()
        assert "valid" in d
        assert "handoff_id" in d
