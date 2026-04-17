"""Unit tests for creativity_verifier module."""
import pytest
from unittest.mock import MagicMock

from vllm_orchestrator.src.app.domain.creative_profile import CreativeProfile
from vllm_orchestrator.src.app.domain.creative_boundaries import (
    BoundaryEnforcer, init_creative_boundaries,
)
from vllm_orchestrator.src.app.domain.heuristics import load_heuristic_packs
from vllm_orchestrator.src.app.review.creativity_verifier import (
    CreativityVerifier, CreativityCheckResult,
)


@pytest.fixture
def verifier():
    boundaries = init_creative_boundaries()
    enforcer = BoundaryEnforcer(boundaries)
    packs = load_heuristic_packs()
    return CreativityVerifier(enforcer, packs)


@pytest.fixture
def mock_envelope():
    e = MagicMock()
    e.hard_constraints = ["방수 IP67"]
    e.soft_preferences = []
    e.target_domain = "minecraft"
    e.user_intent = "중세풍 타워"
    return e


class TestCreativityVerifier:
    def test_valid_variant_passes(self, verifier, mock_envelope):
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "stone"}],
            "block_palette": ["stone", "oak_planks"],
        }
        variant = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "cobblestone"}],
            "block_palette": ["cobblestone", "spruce_planks", "torch"],
            "style": "gothic",
        }
        cp = CreativeProfile(novelty=0.72, style_risk=0.38)
        result = verifier.verify("minecraft", variant, baseline, cp, mock_envelope)
        assert result.passed
        assert result.baseline_differentiation > 0
        assert result.domain_rule_compliance == 1.0

    def test_floor_violation_triggers_shrink_to_safe(self, verifier, mock_envelope):
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        # Variant missing required target_anchor
        variant = {
            "operations": [{"op": "place"}],
        }
        cp = CreativeProfile(novelty=0.72)
        result = verifier.verify("minecraft", variant, baseline, cp, mock_envelope)
        assert not result.passed
        assert result.repair_path == "shrink_to_safe"
        assert result.domain_rule_compliance == 0.0

    def test_identical_variant_low_differentiation(self, verifier, mock_envelope):
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        # Identical to baseline
        variant = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        cp = CreativeProfile(novelty=0.5)
        result = verifier.verify("minecraft", variant, baseline, cp, mock_envelope)
        assert result.baseline_differentiation < 0.1

    def test_none_variant_fails(self, verifier, mock_envelope):
        cp = CreativeProfile()
        result = verifier.verify("minecraft", None, {"a": 1}, cp, mock_envelope)
        assert not result.passed
        assert result.repair_path == "shrink_to_safe"

    def test_cad_variant_pass(self, verifier):
        env = MagicMock()
        env.hard_constraints = ["치수 100mm"]
        env.target_domain = "cad"
        env.user_intent = "설계"
        baseline = {"constraints": [{"type": "dimension", "value": "100mm"}]}
        variant = {
            "constraints": [{"type": "dimension", "value": "100mm"}],
            "parts": [{"name": "housing"}],
        }
        cp = CreativeProfile(novelty=0.34, constraint_strictness=0.97)
        result = verifier.verify("cad", variant, baseline, cp, env)
        assert result.passed

    def test_animation_style_lock(self, verifier):
        env = MagicMock()
        env.hard_constraints = []
        env.target_domain = "animation"
        env.user_intent = "카메라"
        baseline = {"framing": "close_up", "mood": "tense"}
        variant = {"framing": "wide", "mood": "calm"}
        cp = CreativeProfile(novelty=0.58, style_risk=0.10)
        result = verifier.verify("animation", variant, baseline, cp, env)
        # Should pass since framing/mood are valid
        assert result.style_lock_compliance > 0.5

    def test_domain_checks_populated(self, verifier, mock_envelope):
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        variant = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "stone"}],
        }
        cp = CreativeProfile(novelty=0.72, mode="balanced")
        result = verifier.verify("minecraft", variant, baseline, cp, mock_envelope)
        assert isinstance(result.domain_checks, dict)

    def test_result_to_dict(self):
        result = CreativityCheckResult(
            passed=True,
            baseline_differentiation=0.5,
            domain_rule_compliance=1.0,
            style_lock_compliance=0.9,
            practical_value=0.7,
            overall_creativity_score=0.75,
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert d["overall_creativity_score"] == 0.75
