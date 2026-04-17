"""Unit tests for tool_adapter_normalizer module."""
import pytest
from vllm_orchestrator.src.app.execution.tool_adapter_normalizer import (
    ToolAdapterNormalizer, NormalizationResult, FailureType,
)


@pytest.fixture
def normalizer():
    return ToolAdapterNormalizer()


class TestInputNormalization:
    def test_minecraft_input_mapping(self, normalizer):
        result = normalizer.normalize_input("minecraft.compile_archetype", {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
            "block_palette": ["stone"],
        })
        assert result.success
        assert "anchor" in result.normalized_input
        assert "ops" in result.normalized_input
        assert "palette" in result.normalized_input

    def test_builder_input_mapping(self, normalizer):
        result = normalizer.normalize_input("builder.generate_plan", {
            "spaces": [{"name": "living"}],
            "floors": 2,
        })
        assert result.success
        assert "program" in result.normalized_input
        assert "floor_count" in result.normalized_input

    def test_cad_input_mapping(self, normalizer):
        result = normalizer.normalize_input("cad.generate_part", {
            "constraints": [{"type": "dim"}],
            "material": "ABS",
        })
        assert result.success
        assert "design_constraints" in result.normalized_input
        assert "mat_spec" in result.normalized_input

    def test_unknown_tool_passthrough(self, normalizer):
        result = normalizer.normalize_input("unknown.tool", {"a": 1, "b": 2})
        assert result.success
        assert result.normalized_input == {"a": 1, "b": 2}

    def test_metadata_keys_dropped(self, normalizer):
        result = normalizer.normalize_input("minecraft.compile_archetype", {
            "target_anchor": {},
            "_variant_meta": {"strategy": "style_shifted"},
        })
        assert "_variant_meta" not in result.normalized_input


class TestOutputNormalization:
    def test_success_output(self, normalizer):
        result = normalizer.normalize_output("minecraft.compile_archetype", {
            "status": "executed",
            "blocks": 42,
        })
        assert result.success
        assert "operations" in result.normalized_output  # blocks → operations

    def test_empty_output_fails(self, normalizer):
        result = normalizer.normalize_output("minecraft.compile_archetype", {})
        assert not result.success
        assert result.failure_type == FailureType.TOOL_RUNTIME_FAILURE.value

    def test_error_output(self, normalizer):
        result = normalizer.normalize_output("cad.generate_part", {
            "error": "dimension overflow",
        })
        assert not result.success
        assert result.retryable

    def test_missing_required_output(self, normalizer):
        result = normalizer.normalize_output("minecraft.compile_archetype", {
            "blocks": 10,
            # missing "status"
        })
        assert not result.success
        assert result.failure_type == FailureType.SCHEMA_MISMATCH.value


class TestFailureClassification:
    def test_missing_input(self, normalizer):
        result = normalizer.classify_failure(ValueError("missing required field"), "test")
        assert result.failure_type == FailureType.MISSING_REQUIRED_INPUT.value

    def test_enum_error(self, normalizer):
        result = normalizer.classify_failure(ValueError("invalid value for enum"), "test")
        assert result.failure_type == FailureType.INVALID_ENUM.value

    def test_range_error(self, normalizer):
        result = normalizer.classify_failure(ValueError("out of bounds"), "test")
        assert result.failure_type == FailureType.UNIT_RANGE_VIOLATION.value

    def test_lock_violation(self, normalizer):
        result = normalizer.classify_failure(RuntimeError("hard lock violation"), "test")
        assert result.failure_type == FailureType.HARD_LOCK_VIOLATION.value

    def test_generic_error(self, normalizer):
        result = normalizer.classify_failure(RuntimeError("unknown"), "test")
        assert result.failure_type == FailureType.TOOL_RUNTIME_FAILURE.value
        assert result.retryable


class TestDownstreamCompatibility:
    def test_compatible(self, normalizer):
        result = normalizer.check_downstream_compatibility(
            {"framing": "close_up", "mood": "tense"}, ["framing", "mood"],
        )
        assert result.success

    def test_incompatible(self, normalizer):
        result = normalizer.check_downstream_compatibility(
            {"framing": "close_up"}, ["framing", "mood", "shots"],
        )
        assert not result.success
        assert result.failure_type == FailureType.DOWNSTREAM_INCOMPATIBLE.value
