"""Unit tests for domain profile model routing fields."""
import pytest


class TestDomainProfileModelFields:
    """Test that DomainProfile has 32B-era model routing fields."""

    def test_profile_has_llm_default_fields(self):
        from vllm_orchestrator.src.app.domain.profiles import DomainProfile
        p = DomainProfile(domain="test")
        assert p.llm_default == "core_text_32b"
        assert p.llm_code_default == "core_code_32b"
        assert p.llm_review_default == "core_text_32b"
        assert p.lora_adapter_id == ""

    def test_profile_custom_values(self):
        from vllm_orchestrator.src.app.domain.profiles import DomainProfile
        p = DomainProfile(
            domain="minecraft",
            llm_default="core_text_32b",
            lora_adapter_id="minecraft_style_adapter",
        )
        assert p.lora_adapter_id == "minecraft_style_adapter"

    def test_all_profiles_default_to_32b(self):
        from vllm_orchestrator.src.app.domain.profiles import DomainProfile
        for domain in ["minecraft", "builder", "animation", "cad"]:
            p = DomainProfile(domain=domain)
            assert p.llm_default == "core_text_32b", f"{domain} should default to core_text_32b"
            assert p.llm_code_default == "core_code_32b"


class TestSettingsModel:
    """Test that LLMSettings has 32B fields."""

    def test_settings_defaults(self):
        from vllm_orchestrator.src.app.settings import LLMSettings
        s = LLMSettings()
        assert s.text_model_id == "core_text_32b"
        assert s.code_model_id == "core_code_32b"
        assert s.runtime_mode == "local_quantized"
        assert "32B" in s.model

    def test_settings_quantization(self):
        from vllm_orchestrator.src.app.settings import LLMSettings
        s = LLMSettings()
        assert s.quantization == "awq"
        assert s.enable_adapters is False
        assert s.max_context == 32768
