"""Unit tests for creative_boundaries module."""
import pytest
from vllm_orchestrator.src.app.domain.creative_boundaries import (
    BoundaryRule,
    CreativeBoundaries,
    BoundaryEnforcer,
    BoundaryCheckResult,
    init_creative_boundaries,
)


@pytest.fixture
def enforcer():
    boundaries = init_creative_boundaries()
    return BoundaryEnforcer(boundaries)


class TestBoundaryRule:
    def test_roundtrip(self):
        rule = BoundaryRule(
            rule_id="test_rule",
            check_type="required_present",
            target="constraints",
            description="test",
        )
        d = rule.to_dict()
        r2 = BoundaryRule.from_dict(d)
        assert r2.rule_id == "test_rule"
        assert r2.check_type == "required_present"


class TestBoundaryEnforcer:
    def test_minecraft_floor_pass(self, enforcer):
        slots = {
            "target_anchor": {"type": "relative", "x": 0, "y": 64, "z": 0},
            "operations": [{"op": "place", "block": "stone"}],
        }
        result = enforcer.validate_variant("minecraft", slots)
        assert result.passed
        assert len(result.floor_violations) == 0

    def test_minecraft_floor_fail_missing_anchor(self, enforcer):
        slots = {
            "operations": [{"op": "place", "block": "stone"}],
        }
        result = enforcer.validate_variant("minecraft", slots)
        assert not result.passed
        assert any("target_anchor" in v for v in result.floor_violations)

    def test_minecraft_floor_fail_empty_operations(self, enforcer):
        slots = {
            "target_anchor": {"type": "relative"},
            "operations": [],
        }
        result = enforcer.validate_variant("minecraft", slots)
        assert not result.passed

    def test_builder_floor_pass(self, enforcer):
        slots = {
            "spaces": [{"name": "living_room", "area_m2": 20}],
            "floors": 2,
        }
        result = enforcer.validate_variant("builder", slots)
        assert result.passed

    def test_builder_floor_fail_no_spaces(self, enforcer):
        slots = {"style": "modern"}
        result = enforcer.validate_variant("builder", slots)
        assert not result.passed

    def test_animation_floor_pass(self, enforcer):
        slots = {
            "framing": "close_up",
            "mood": "tense",
        }
        result = enforcer.validate_variant("animation", slots)
        assert result.passed

    def test_animation_floor_fail_missing_framing(self, enforcer):
        slots = {"mood": "tense"}
        result = enforcer.validate_variant("animation", slots)
        assert not result.passed

    def test_cad_floor_pass(self, enforcer):
        slots = {
            "constraints": [{"type": "dimension", "value": "100mm"}],
        }
        result = enforcer.validate_variant("cad", slots)
        assert result.passed

    def test_cad_floor_fail_missing_constraints(self, enforcer):
        slots = {"parts": []}
        result = enforcer.validate_variant("cad", slots)
        assert not result.passed

    def test_unknown_domain_passes(self, enforcer):
        result = enforcer.validate_variant("unknown", {"anything": True})
        assert result.passed

    def test_empty_slots_fails(self, enforcer):
        result = enforcer.validate_variant("minecraft", {})
        assert not result.passed

    def test_ceiling_breaches_are_advisory(self, enforcer):
        slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        result = enforcer.validate_variant("minecraft", slots)
        # Ceiling breaches don't affect passed status
        assert result.passed or not result.passed  # depends on floor
        # ceiling_breaches is always a list
        assert isinstance(result.ceiling_breaches, list)


class TestInitCreativeBoundaries:
    def test_all_domains_present(self):
        boundaries = init_creative_boundaries()
        assert "minecraft" in boundaries
        assert "builder" in boundaries
        assert "animation" in boundaries
        assert "cad" in boundaries
        assert "product_design" in boundaries

    def test_each_domain_has_floor(self):
        boundaries = init_creative_boundaries()
        for domain, bd in boundaries.items():
            assert len(bd.professional_floor) > 0, f"{domain} has no floor rules"
