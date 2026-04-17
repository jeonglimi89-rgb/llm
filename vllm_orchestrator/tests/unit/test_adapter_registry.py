"""Unit tests for adapter_registry module."""
import pytest
from vllm_orchestrator.src.app.llm.adapter_registry import (
    AdapterRegistry, AdapterSpec, AdapterStatus, AdapterAttachResult,
)


@pytest.fixture
def registry():
    """Fresh registry for each test — no leaked state from other tests."""
    from vllm_orchestrator.src.app.llm.adapter_registry import _ADAPTER_REGISTRY, AdapterSpec
    import copy
    fresh = {k: AdapterSpec(**{f: getattr(v, f) for f in v.__dataclass_fields__}) for k, v in _ADAPTER_REGISTRY.items()}
    return AdapterRegistry(fresh)


class TestAdapterRegistry:
    def test_four_adapters_registered(self, registry):
        all_adapters = registry.list_all()
        assert len(all_adapters) == 4

    def test_all_adapters_reserved(self, registry):
        reserved = registry.list_reserved()
        assert len(reserved) == 4
        for a in reserved:
            assert a.status == AdapterStatus.RESERVED

    def test_none_available_initially(self, registry):
        available = registry.list_available()
        assert len(available) == 0

    def test_get_by_id(self, registry):
        a = registry.get("builder_rules_adapter")
        assert a is not None
        assert a.domain == "builder"

    def test_get_for_domain(self, registry):
        for domain in ["minecraft", "builder", "animation", "cad"]:
            a = registry.get_for_domain(domain)
            assert a is not None, f"No adapter for domain {domain}"

    def test_attach_reserved_falls_back(self, registry):
        result = registry.attach("minecraft_style_adapter")
        assert not result.attached
        assert result.fallback_to_base
        assert "reserved" in result.reason

    def test_attach_unknown_falls_back(self, registry):
        result = registry.attach("nonexistent_adapter")
        assert not result.attached
        assert result.fallback_to_base

    def test_attach_available(self, registry):
        registry.set_status("builder_rules_adapter", AdapterStatus.AVAILABLE, "/path/to/weights")
        result = registry.attach("builder_rules_adapter")
        assert result.attached
        assert not result.fallback_to_base

    def test_attach_for_domain(self, registry):
        result = registry.attach_for_domain("cad")
        assert not result.attached  # reserved, not available
        assert result.fallback_to_base

    def test_set_status(self, registry):
        ok = registry.set_status("cad_constraints_adapter", AdapterStatus.AVAILABLE, "/weights")
        assert ok
        a = registry.get("cad_constraints_adapter")
        assert a.is_available
        assert a.adapter_path == "/weights"

    def test_set_status_unknown(self, registry):
        ok = registry.set_status("nonexistent", AdapterStatus.AVAILABLE)
        assert not ok

    def test_register_new(self, registry):
        spec = AdapterSpec(
            adapter_id="test_adapter",
            domain="test",
            description="test",
        )
        registry.register(spec)
        assert registry.get("test_adapter") is not None

    def test_adapter_spec_to_dict(self, registry):
        a = registry.get("minecraft_style_adapter")
        d = a.to_dict()
        assert d["adapter_id"] == "minecraft_style_adapter"
        assert d["status"] == "reserved"
        assert d["base_model_id"] == "core_text_32b"

    def test_adapter_base_model_is_32b(self, registry):
        for a in registry.list_all():
            assert a.base_model_id == "core_text_32b", \
                f"Adapter {a.adapter_id} base model should be core_text_32b"
