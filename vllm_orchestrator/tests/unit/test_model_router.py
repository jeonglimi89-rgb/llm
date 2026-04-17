"""Unit tests for model_router module — 32B canonical baseline."""
import pytest
from vllm_orchestrator.src.app.llm.model_router import (
    ModelRouter, ModelEndpoint, ModelRoutingDecision, ModelTier,
    ProviderMetadata, CORE_TEXT_32B, CORE_CODE_32B,
    DEFAULT_PROVIDERS, DEFAULT_ENDPOINTS,
)


@pytest.fixture
def router():
    return ModelRouter()


class TestProviderRegistry:
    def test_core_text_32b_registered(self):
        assert CORE_TEXT_32B in DEFAULT_PROVIDERS
        p = DEFAULT_PROVIDERS[CORE_TEXT_32B]
        assert p.canonical_identity == "Qwen/Qwen2.5-32B-Instruct"
        assert p.parameter_scale == "32B"
        assert p.modality == "text"

    def test_core_code_32b_registered(self):
        assert CORE_CODE_32B in DEFAULT_PROVIDERS
        p = DEFAULT_PROVIDERS[CORE_CODE_32B]
        assert p.canonical_identity == "Qwen/Qwen2.5-Coder-32B-Instruct"
        assert p.modality == "code"

    def test_providers_are_adapter_capable(self):
        for pid, p in DEFAULT_PROVIDERS.items():
            assert p.adapter_capable, f"Provider {pid} should be adapter-capable"

    def test_provider_metadata_to_dict(self):
        p = DEFAULT_PROVIDERS[CORE_TEXT_32B]
        d = p.to_dict()
        assert d["logical_id"] == "core_text_32b"
        assert d["model_family"] == "qwen2.5"


class TestModelRouter:
    def test_text_tier_default(self, router):
        d = router.route("builder.requirement_parse", "strict-json-pool", "builder")
        assert d.tier == ModelTier.TEXT.value
        assert d.logical_id == CORE_TEXT_32B
        assert "32B" in d.model_path

    def test_code_task_routes_to_coder(self, router):
        d = router.route("builder.floor_plan_generate", "strict-json-pool", is_code_task=True)
        assert d.tier == ModelTier.CODE.value
        assert d.logical_id == CORE_CODE_32B
        assert "Coder" in d.model_path

    def test_creative_variant(self, router):
        d = router.route("minecraft.build_parse", "strict-json-pool", "minecraft", is_variant=True)
        assert d.tier == ModelTier.CREATIVE.value
        assert d.temperature == 0.4

    def test_long_context(self, router):
        d = router.route("builder.project_context", "long-context-pool")
        assert d.tier == ModelTier.LONG_CONTEXT.value
        assert d.timeout_ms == 30000

    def test_explicit_code_task_flag(self, router):
        d = router.route("cad.constraint_parse", "strict-json-pool", "cad", is_code_task=True)
        assert d.logical_id == CORE_CODE_32B

    def test_lora_disabled_by_default(self, router):
        d = router.route("minecraft.build_parse", "strict-json-pool", "minecraft")
        assert d.lora_adapter_id == ""

    def test_lora_enabled(self):
        r = ModelRouter(lora_enabled=True)
        d = r.route("minecraft.build_parse", "strict-json-pool", "minecraft")
        assert d.lora_adapter_id == "minecraft_style_adapter"

    def test_lora_per_domain(self):
        r = ModelRouter(lora_enabled=True)
        assert r.route("builder.requirement_parse", "strict-json-pool", "builder").lora_adapter_id == "builder_rules_adapter"
        assert r.route("animation.shot_parse", "strict-json-pool", "animation").lora_adapter_id == "animation_direction_adapter"
        assert r.route("cad.constraint_parse", "strict-json-pool", "cad").lora_adapter_id == "cad_constraints_adapter"

    def test_all_domains_default_to_32b(self, router):
        for domain in ["minecraft", "builder", "animation", "cad"]:
            d = router.route(f"{domain}.test", "strict-json-pool", domain)
            assert d.logical_id == CORE_TEXT_32B
            assert "32B" in d.model_path

    def test_decision_to_dict(self, router):
        d = router.route("cad.constraint_parse", "strict-json-pool")
        dd = d.to_dict()
        assert "logical_id" in dd
        assert "tier" in dd

    def test_list_providers(self, router):
        providers = router.list_providers()
        assert len(providers) == 2
        ids = {p.logical_id for p in providers}
        assert CORE_TEXT_32B in ids
        assert CORE_CODE_32B in ids

    def test_reason_includes_model(self, router):
        d = router.route("builder.requirement_parse", "strict-json-pool")
        assert "core_text_32b" in d.reason


class TestNoLegacy7BReferences:
    """Regression: ensure no 7B model IDs leak through."""

    def test_no_7b_in_endpoints(self):
        for eid, ep in DEFAULT_ENDPOINTS.items():
            assert "7B" not in ep.model_path, f"Endpoint {eid} still references 7B: {ep.model_path}"

    def test_no_7b_in_providers(self):
        for pid, p in DEFAULT_PROVIDERS.items():
            assert "7B" not in p.canonical_identity, f"Provider {pid} references 7B"


class TestModelEndpoint:
    def test_all_endpoints_are_32b(self):
        for eid, ep in DEFAULT_ENDPOINTS.items():
            assert "32B" in ep.model_path or "32b" in ep.model_path.lower(), \
                f"Endpoint {eid} model path doesn't reference 32B: {ep.model_path}"
