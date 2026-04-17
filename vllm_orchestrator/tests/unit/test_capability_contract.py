"""Unit tests for capability_contract module."""
import pytest
from vllm_orchestrator.src.app.domain.capability_contract import (
    CAPABILITY_REGISTRY, CapabilityContract,
    get_capability, list_capabilities_for_domain, get_hard_locks_for_domain,
)


class TestCapabilityRegistry:
    def test_all_domains_have_capabilities(self):
        domains = {"minecraft", "builder", "animation", "cad"}
        for domain in domains:
            caps = list_capabilities_for_domain(domain)
            assert len(caps) >= 2, f"Domain '{domain}' has fewer than 2 capabilities"

    def test_minecraft_capabilities(self):
        caps = list_capabilities_for_domain("minecraft")
        cap_ids = {c.capability_id for c in caps}
        assert "minecraft.build_plan_generate" in cap_ids
        assert "minecraft.npc_concept_generate" in cap_ids
        assert "minecraft.resourcepack_style_plan" in cap_ids

    def test_builder_capabilities(self):
        caps = list_capabilities_for_domain("builder")
        cap_ids = {c.capability_id for c in caps}
        assert "builder.exterior_drawing_generate" in cap_ids
        assert "builder.interior_drawing_generate" in cap_ids

    def test_animation_capabilities(self):
        caps = list_capabilities_for_domain("animation")
        cap_ids = {c.capability_id for c in caps}
        assert "animation.camera_walk_plan_generate" in cap_ids
        assert "animation.style_lock_check" in cap_ids
        assert "animation.style_feedback_generate" in cap_ids

    def test_cad_capabilities(self):
        caps = list_capabilities_for_domain("cad")
        cap_ids = {c.capability_id for c in caps}
        assert "cad.design_drawing_generate" in cap_ids
        assert "cad.assembly_feasibility_check" in cap_ids
        assert "cad.manufacturability_check" in cap_ids

    def test_get_capability(self):
        cap = get_capability("minecraft.build_plan_generate")
        assert cap is not None
        assert cap.app_domain == "minecraft"
        assert cap.creativity_allowed is True

    def test_get_unknown_capability(self):
        assert get_capability("nonexistent.capability") is None

    def test_hard_locks_per_domain(self):
        mc_locks = get_hard_locks_for_domain("minecraft")
        assert "theme_coherence" in mc_locks
        assert "structure_coherence" in mc_locks

        builder_locks = get_hard_locks_for_domain("builder")
        assert "zoning_compliance" in builder_locks
        assert "circulation_sanity" in builder_locks

        anim_locks = get_hard_locks_for_domain("animation")
        assert "camera_continuity" in anim_locks
        assert "style_lock" in anim_locks

        cad_locks = get_hard_locks_for_domain("cad")
        assert "manufacturability" in cad_locks
        assert "assembly_logic" in cad_locks

    def test_strict_capabilities_no_creativity(self):
        """Capabilities with creativity_tier='strict' should not allow creativity."""
        for cap_id, cap in CAPABILITY_REGISTRY.items():
            if cap.creativity_tier == "strict":
                assert not cap.creativity_allowed, \
                    f"{cap_id} is strict but creativity_allowed=True"

    def test_intervention_scope_matches_task_family(self):
        """intervention_scope should match allowed_task_family."""
        for cap_id, cap in CAPABILITY_REGISTRY.items():
            assert cap.intervention_scope == cap.allowed_task_family, \
                f"{cap_id}: intervention_scope ({cap.intervention_scope}) != allowed_task_family ({cap.allowed_task_family})"

    def test_contract_to_dict(self):
        cap = get_capability("minecraft.build_plan_generate")
        d = cap.to_dict()
        assert d["capability_id"] == "minecraft.build_plan_generate"
        assert isinstance(d["hard_locks"], list)
