"""Unit tests for intervention_policy module."""
import pytest
from vllm_orchestrator.src.app.domain.intervention_policy import (
    InterventionPolicy, InterventionPolicyResult,
)


@pytest.fixture
def policy():
    return InterventionPolicy()


class TestInterventionPolicy:
    # ── A. Cross-domain rejection tests ──

    def test_minecraft_rejects_exterior_drawing(self, policy):
        r = policy.check("minecraft", "exterior_drawing_generate")
        assert not r.passed
        assert r.violation_type == "role_scope_violation"
        assert "Builder AI" in r.violation_detail

    def test_builder_rejects_npc_generation(self, policy):
        r = policy.check("builder", "character_parse")
        assert not r.passed
        assert r.violation_type == "role_scope_violation"
        assert "Minecraft AI" in r.violation_detail

    def test_animation_rejects_engineering_drawing(self, policy):
        r = policy.check("animation", "constraint_parse")
        assert not r.passed
        assert r.violation_type == "role_scope_violation"
        assert "CAD AI" in r.violation_detail

    def test_cad_rejects_resourcepack(self, policy):
        r = policy.check("cad", "style_parse")
        assert not r.passed
        assert r.violation_type == "role_scope_violation"
        assert "Minecraft AI" in r.violation_detail

    def test_builder_rejects_camera_planning(self, policy):
        r = policy.check("builder", "shot_parse")
        assert not r.passed

    def test_cad_rejects_camera_planning(self, policy):
        r = policy.check("cad", "camera_intent_parse")
        assert not r.passed

    # ── Valid tasks ──

    def test_minecraft_allows_build(self, policy):
        r = policy.check("minecraft", "build_parse")
        assert r.passed

    def test_minecraft_allows_npc(self, policy):
        r = policy.check("minecraft", "character_parse")
        assert r.passed

    def test_minecraft_allows_resourcepack(self, policy):
        r = policy.check("minecraft", "style_parse")
        assert r.passed

    def test_builder_allows_exterior(self, policy):
        r = policy.check("builder", "requirement_parse")
        assert r.passed

    def test_builder_allows_interior(self, policy):
        r = policy.check("builder", "zone_priority_parse")
        assert r.passed

    def test_animation_allows_camera(self, policy):
        r = policy.check("animation", "shot_parse")
        assert r.passed

    def test_animation_allows_style_lock(self, policy):
        r = policy.check("animation", "style_lock_check")
        assert r.passed

    def test_animation_allows_style_feedback(self, policy):
        r = policy.check("animation", "style_feedback_generate")
        assert r.passed

    def test_cad_allows_design(self, policy):
        r = policy.check("cad", "constraint_parse")
        assert r.passed

    def test_context_query_universal(self, policy):
        for domain in ["minecraft", "builder", "animation", "cad"]:
            r = policy.check(domain, "context_query")
            assert r.passed, f"context_query should be allowed in {domain}"

    # ── Graph node validation ──

    def test_graph_node_valid(self, policy):
        r = policy.check_graph_node("minecraft", "minecraft.build_plan_generate")
        assert r.passed

    def test_graph_node_cross_domain(self, policy):
        r = policy.check_graph_node("builder", "animation.camera_walk_plan_generate")
        assert not r.passed
        assert r.violation_type == "cross_domain_capability_violation"

    def test_graph_node_npc_in_minecraft(self, policy):
        r = policy.check_graph_node("minecraft", "npc.character_parse")
        assert r.passed  # npc is minecraft sub-domain

    def test_graph_node_cad_in_animation(self, policy):
        r = policy.check_graph_node("animation", "cad.design_drawing_generate")
        assert not r.passed
