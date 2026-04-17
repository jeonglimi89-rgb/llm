"""Unit tests for output_policy module."""
import pytest
from unittest.mock import MagicMock

from vllm_orchestrator.src.app.domain.output_policy import (
    OutputPolicyEnforcer, OutputPolicyResult,
)
from vllm_orchestrator.src.app.core.enums import OutputType


@pytest.fixture
def enforcer():
    return OutputPolicyEnforcer()


class TestOutputPolicyEnforcer:
    def test_fail_loud_overrides(self, enforcer):
        result = enforcer.classify_and_validate(
            slots={"data": "valid"},
            fail_loud=True,
            fail_loud_reason="schema_fail",
        )
        assert result.output_type == OutputType.FAIL_LOUD_WITH_REASONS.value
        assert result.passed

    def test_null_slots_clarification(self, enforcer):
        result = enforcer.classify_and_validate(slots=None)
        assert result.output_type == OutputType.CLARIFICATION_REQUIRED.value
        assert not result.passed

    def test_single_output(self, enforcer):
        result = enforcer.classify_and_validate(
            slots={"target_anchor": {"type": "relative"}, "operations": [{"block": "stone"}]},
        )
        assert result.output_type == OutputType.EXECUTABLE_COMMAND_GRAPH.value
        assert result.passed

    def test_multi_variant_output(self, enforcer):
        plan = MagicMock()
        plan.variants = [MagicMock(accepted=True, family="baseline"), MagicMock(accepted=True, family="creative")]
        result = enforcer.classify_and_validate(
            slots={"data": "valid"},
            variant_plan=plan,
        )
        assert result.output_type == OutputType.EXECUTABLE_COMMAND_GRAPH_WITH_VARIANTS.value

    def test_banned_pattern_detected(self, enforcer):
        slots = {"response": "일반적으로 이런 건물은 좋습니다"}
        result = enforcer.classify_and_validate(slots=slots)
        assert not result.passed
        assert any("banned_pattern" in v for v in result.violations)

    def test_generic_filler_detected(self, enforcer):
        slots = {"response": "다양한 방법이 있습니다"}
        result = enforcer.classify_and_validate(slots=slots)
        assert not result.passed
        assert any("generic_filler" in v for v in result.violations)

    def test_clean_output_passes(self, enforcer):
        slots = {
            "target_anchor": {"type": "relative", "x": 0, "y": 64, "z": 0},
            "operations": [
                {"op": "place", "block": "stone_bricks", "x": 0, "y": 0, "z": 0},
                {"op": "place", "block": "oak_planks", "x": 1, "y": 0, "z": 0},
            ],
            "block_palette": ["stone_bricks", "oak_planks"],
        }
        result = enforcer.classify_and_validate(slots=slots)
        assert result.passed
        assert result.output_type == OutputType.EXECUTABLE_COMMAND_GRAPH.value

    def test_rejected_variant_flagged(self, enforcer):
        plan = MagicMock()
        v1 = MagicMock(accepted=True, family="baseline")
        v2 = MagicMock(accepted=False, family="creative_variant")
        plan.variants = [v1, v2]
        result = enforcer.classify_and_validate(
            slots={"data": "valid"},
            variant_plan=plan,
        )
        assert any("rejected variant" in v for v in result.violations)

    def test_result_to_dict(self):
        result = OutputPolicyResult(
            output_type="executable_command_graph",
            violations=[],
            passed=True,
        )
        d = result.to_dict()
        assert d["output_type"] == "executable_command_graph"
        assert d["passed"]
