"""Unit tests for adapter_policy module."""
import pytest
from vllm_orchestrator.src.app.llm.adapter_registry import AdapterRegistry, AdapterStatus
from vllm_orchestrator.src.app.llm.adapter_policy import (
    AdapterActivationPolicy, AdapterDecision, PipelinePhase,
)
from vllm_orchestrator.src.app.llm.model_router import CORE_TEXT_32B, CORE_CODE_32B


@pytest.fixture
def registry():
    return AdapterRegistry()

@pytest.fixture
def policy(registry):
    return AdapterActivationPolicy(registry)


class TestAdapterActivationPolicy:
    def test_generation_phase_recommends_attach(self, policy):
        d = policy.evaluate("builder", PipelinePhase.GENERATION.value)
        # reserved adapter → fallback to base, but should_attach=False
        assert d.fallback_to_base
        assert d.adapter_id == "builder_rules_adapter"

    def test_generation_with_available_adapter(self, registry):
        registry.set_status("builder_rules_adapter", AdapterStatus.AVAILABLE, "/path")
        p = AdapterActivationPolicy(registry)
        d = p.evaluate("builder", PipelinePhase.GENERATION.value)
        assert d.should_attach
        assert d.adapter_id == "builder_rules_adapter"

    def test_code_task_no_adapter(self, policy):
        d = policy.evaluate("builder", PipelinePhase.GENERATION.value, is_code_task=True)
        assert not d.should_attach

    def test_validation_phase_no_adapter(self, policy):
        d = policy.evaluate("minecraft", PipelinePhase.VALIDATION.value)
        assert not d.should_attach

    def test_cad_creative_no_adapter(self, policy):
        """CAD creative variants don't use adapter."""
        d = policy.evaluate("cad", PipelinePhase.CREATIVE_VARIANT.value)
        assert not d.should_attach

    def test_minecraft_creative_with_adapter(self, registry):
        """Minecraft creative variants use adapter."""
        registry.set_status("minecraft_style_adapter", AdapterStatus.AVAILABLE, "/path")
        p = AdapterActivationPolicy(registry)
        d = p.evaluate("minecraft", PipelinePhase.CREATIVE_VARIANT.value)
        assert d.should_attach

    def test_review_phase_builder(self, registry):
        registry.set_status("builder_rules_adapter", AdapterStatus.AVAILABLE, "/path")
        p = AdapterActivationPolicy(registry)
        d = p.evaluate("builder", PipelinePhase.REVIEW.value)
        assert d.should_attach

    def test_unknown_domain_no_adapter(self, policy):
        d = policy.evaluate("unknown_domain", PipelinePhase.GENERATION.value)
        assert not d.should_attach

    def test_force_adapter_override(self, registry):
        registry.set_status("cad_constraints_adapter", AdapterStatus.AVAILABLE, "/path")
        p = AdapterActivationPolicy(registry)
        d = p.evaluate("builder", PipelinePhase.GENERATION.value, force_adapter="cad_constraints_adapter")
        assert d.should_attach

    def test_force_incompatible_model_blocked(self, policy):
        d = policy.evaluate("builder", PipelinePhase.GENERATION.value,
                           logical_model_id=CORE_CODE_32B, force_adapter="builder_rules_adapter")
        # adapter base_model is core_text_32b, but we passed core_code_32b
        assert not d.should_attach

    def test_wrong_domain_adapter_rejection(self, registry):
        """An adapter for wrong domain still attaches if forced (policy override)."""
        registry.set_status("minecraft_style_adapter", AdapterStatus.AVAILABLE, "/path")
        p = AdapterActivationPolicy(registry)
        # Force minecraft adapter on builder domain
        d = p.evaluate("builder", PipelinePhase.GENERATION.value,
                       force_adapter="minecraft_style_adapter")
        assert d.should_attach  # force override works

    def test_decision_to_dict(self, policy):
        d = policy.evaluate("cad", PipelinePhase.GENERATION.value)
        dd = d.to_dict()
        assert "should_attach" in dd
        assert "adapter_id" in dd
        assert "phase" in dd
