"""Unit tests for llm_variant_generator module."""
import pytest
from unittest.mock import MagicMock

from vllm_orchestrator.src.app.orchestration.llm_variant_generator import LLMVariantGenerator


@pytest.fixture
def mock_dispatcher():
    d = MagicMock()
    result = MagicMock()
    result.slots = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place", "block": "gold_block"}]}
    d.dispatch.return_value = result
    return d


@pytest.fixture
def mock_router():
    r = MagicMock()
    r.resolve.return_value = MagicMock()
    return r


@pytest.fixture
def generator(mock_dispatcher, mock_router):
    return LLMVariantGenerator(mock_dispatcher, mock_router)


class TestLLMVariantGenerator:
    def test_generate_variant_success(self, generator):
        base = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]}
        result = generator.generate_variant(
            base, "style_shifted", "Generate a creative variant",
            domain="minecraft", task_name="build_parse", user_input="타워 만들어줘",
        )
        assert result is not None
        assert "target_anchor" in result

    def test_generate_variant_no_suffix_returns_none(self, generator):
        base = {"target_anchor": {"type": "relative"}}
        result = generator.generate_variant(base, "original", "")
        assert result is None

    def test_create_generator_fn(self, generator):
        fn = generator.create_generator_fn(
            domain="minecraft", task_name="build_parse",
            user_input="성 만들어줘",
        )
        assert callable(fn)
        result = fn({"ops": [1]}, "style_shifted", "Be creative")
        assert result is not None

    def test_generator_fn_dispatches_to_llm(self, generator, mock_dispatcher):
        fn = generator.create_generator_fn(
            domain="builder", task_name="requirement_parse",
            user_input="주택 설계",
        )
        fn({"spaces": []}, "design_enhanced", "Enhance the design")
        assert mock_dispatcher.dispatch.called

    def test_generate_variant_handles_exception(self, mock_router):
        d = MagicMock()
        d.dispatch.side_effect = RuntimeError("LLM unavailable")
        gen = LLMVariantGenerator(d, mock_router)
        result = gen.generate_variant(
            {"a": 1}, "style_shifted", "prompt",
            domain="minecraft", task_name="build_parse",
        )
        assert result is None

    def test_prompt_contains_baseline(self, generator):
        prompt = generator._build_variant_prompt(
            {"style": "medieval"}, "style_shifted",
            "Generate creative variant", "You are an architect",
        )
        assert "medieval" in prompt
        assert "VARIANT" in prompt
        assert "You are an architect" in prompt
