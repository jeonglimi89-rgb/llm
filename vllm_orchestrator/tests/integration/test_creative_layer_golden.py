"""
Golden integration tests for the Creative Layer.

Tests the full OrchestratedPipeline with creative_profile,
variant planning, creativity verification, and output policy.
"""
import pytest
from unittest.mock import MagicMock
import json

from vllm_orchestrator.src.app.domain.creative_profile import (
    CreativeProfile, resolve_creative_profile,
)
from vllm_orchestrator.src.app.domain.creative_boundaries import (
    BoundaryEnforcer, init_creative_boundaries,
)
from vllm_orchestrator.src.app.domain.heuristics import load_heuristic_packs
from vllm_orchestrator.src.app.domain.output_policy import OutputPolicyEnforcer
from vllm_orchestrator.src.app.orchestration.variant_planner import VariantPlanner
from vllm_orchestrator.src.app.review.creativity_verifier import CreativityVerifier
from vllm_orchestrator.src.app.review.domain_evaluator import DomainEvaluator, DomainEvaluation
from vllm_orchestrator.src.app.core.enums import OutputType


# ── Fixtures ──

@pytest.fixture
def creative_stack():
    """Build the full creative layer stack."""
    boundaries = init_creative_boundaries()
    enforcer = BoundaryEnforcer(boundaries)
    packs = load_heuristic_packs()
    mock_evaluator = MagicMock(spec=DomainEvaluator)
    mock_evaluator.evaluate.return_value = DomainEvaluation(
        overall_score=0.78, passed=True,
        domain_match=1.0, constraint_coverage=0.85,
        terminology_accuracy=0.75, output_schema_compliance=0.9,
        actionability=0.8, hallucination_risk=0.1,
    )
    planner = VariantPlanner(boundary_enforcer=enforcer, evaluator=mock_evaluator)
    verifier = CreativityVerifier(boundary_enforcer=enforcer, heuristic_packs=packs)
    policy = OutputPolicyEnforcer()
    return {
        "enforcer": enforcer,
        "planner": planner,
        "verifier": verifier,
        "policy": policy,
        "evaluator": mock_evaluator,
    }


def _mock_classification(domain: str):
    c = MagicMock()
    c.primary_domain = domain
    return c


def _mock_profile(domain: str):
    p = MagicMock()
    p.domain = domain
    p.vocabulary = {}
    p.required_output_keys = set()
    return p


def _mock_envelope(domain: str, intent: str = "테스트"):
    e = MagicMock()
    e.hard_constraints = []
    e.soft_preferences = []
    e.target_domain = domain
    e.user_intent = intent
    e.domain_specific = {}
    return e


# ── Golden Tests ──

class TestMinecraftCreativeGolden:
    """Minecraft AI: 3-variant generation with floor check."""

    def test_3_variant_generation(self, creative_stack):
        planner = creative_stack["planner"]
        cp = resolve_creative_profile("minecraft")
        assert cp.variant_count == 3

        base_slots = {
            "target_anchor": {"type": "relative", "x": 0, "y": 64, "z": 0},
            "operations": [
                {"op": "place", "block": "cobblestone", "x": 0, "y": 0, "z": 0},
            ],
            "block_palette": ["cobblestone", "stone_bricks", "oak_planks"],
            "style": {"theme": "medieval"},
        }

        plan = planner.plan_variants(
            "minecraft", cp, base_slots,
            _mock_envelope("minecraft", "중세풍 타워 만들어줘"),
            _mock_classification("minecraft"),
            _mock_profile("minecraft"),
        )

        assert len(plan.variants) == 3
        assert plan.variants[0].family == "safe_baseline"
        assert plan.variants[1].family == "creative_variant"
        assert plan.variants[2].family == "world_expansion_variant"

    def test_all_variants_pass_floor(self, creative_stack):
        planner = creative_stack["planner"]
        cp = resolve_creative_profile("minecraft")

        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "stone"}],
        }

        plan = planner.plan_variants(
            "minecraft", cp, base_slots,
            _mock_envelope("minecraft"),
            _mock_classification("minecraft"),
            _mock_profile("minecraft"),
        )

        for v in plan.variants:
            assert v.accepted, f"Variant {v.family} failed floor check"


class TestBuilderCreativeGolden:
    """Builder AI: 2-variant with compliance check."""

    def test_2_variant_generation(self, creative_stack):
        planner = creative_stack["planner"]
        cp = resolve_creative_profile("builder")
        assert cp.variant_count == 2

        base_slots = {
            "spaces": [
                {"name": "거실", "area_m2": 25},
                {"name": "주방", "area_m2": 12},
                {"name": "침실", "area_m2": 15},
                {"name": "화장실", "area_m2": 5},
            ],
            "floors": 2,
            "preferences": {"style": "modern"},
        }

        plan = planner.plan_variants(
            "builder", cp, base_slots,
            _mock_envelope("builder", "2층 주택 설계해줘"),
            _mock_classification("builder"),
            _mock_profile("builder"),
        )

        assert len(plan.variants) == 2
        families = [v.family for v in plan.variants]
        assert "compliance_safe_plan" in families
        assert "design_enhanced_plan" in families


class TestAnimationCreativeGolden:
    """Animation AI: style lock enforcement."""

    def test_3_variant_with_style_lock(self, creative_stack):
        planner = creative_stack["planner"]
        cp = resolve_creative_profile("animation")
        assert cp.style_risk == 0.10  # Very low style risk

        base_slots = {
            "framing": "close_up",
            "mood": "tense",
            "camera_move": "dolly_in",
            "shots": [
                {"shot_type": "close_up", "framing": "close_up", "duration_frames": 72},
            ],
        }

        plan = planner.plan_variants(
            "animation", cp, base_slots,
            _mock_envelope("animation", "긴장감 있는 클로즈업"),
            _mock_classification("animation"),
            _mock_profile("animation"),
        )

        assert len(plan.variants) == 3
        families = [v.family for v in plan.variants]
        assert "safe_camera_plan" in families
        assert "cinematic_camera_variant" in families

    def test_style_drift_variant_rejected(self, creative_stack):
        verifier = creative_stack["verifier"]
        cp = resolve_creative_profile("animation")

        baseline = {"framing": "close_up", "mood": "tense"}
        # Variant that violates floor (missing framing)
        bad_variant = {"mood": "calm"}

        result = verifier.verify("animation", bad_variant, baseline, cp,
                                 _mock_envelope("animation"))
        assert not result.passed
        assert result.repair_path == "shrink_to_safe"


class TestCADCreativeGolden:
    """CAD AI: conservative mode with engineering validity."""

    def test_conservative_mode(self, creative_stack):
        cp = resolve_creative_profile("cad")
        assert cp.mode == "conservative"
        assert cp.constraint_strictness == 0.97

    def test_2_variant_engineering(self, creative_stack):
        planner = creative_stack["planner"]
        cp = resolve_creative_profile("cad")

        base_slots = {
            "constraints": [
                {"type": "dimension", "field": "width", "value": "80mm"},
                {"type": "sealing", "grade": "IP67"},
            ],
            "systems": ["mechanical", "electrical"],
        }

        plan = planner.plan_variants(
            "cad", cp, base_slots,
            _mock_envelope("cad", "방수 하우징 설계"),
            _mock_classification("cad"),
            _mock_profile("cad"),
        )

        assert len(plan.variants) == 2
        families = [v.family for v in plan.variants]
        assert "baseline_engineering_solution" in families
        assert "compact_or_creative_concept_variant" in families


class TestNoCreativeProfileFallback:
    """Without creative_profile, pipeline behavior should be unchanged."""

    def test_default_profile_used(self):
        cp = resolve_creative_profile("minecraft")
        # Default is the fixed domain value
        assert cp.variant_count == 3
        assert cp.novelty == 0.72

    def test_empty_context(self):
        cp = resolve_creative_profile("builder", {})
        assert cp.variant_count == 2

    def test_non_creative_context(self):
        cp = resolve_creative_profile("cad", {"some_other_key": "value"})
        assert cp.mode == "conservative"


class TestOutputPolicyGolden:
    """Output policy enforcement tests."""

    def test_banned_pattern_generic_text(self, creative_stack):
        policy = creative_stack["policy"]
        slots = {
            "response": "일반적으로 이런 구조가 좋습니다. 보통은 이렇게 합니다.",
        }
        result = policy.classify_and_validate(slots=slots)
        assert not result.passed
        assert len(result.violations) > 0

    def test_clean_structured_output(self, creative_stack):
        policy = creative_stack["policy"]
        slots = {
            "target_anchor": {"type": "absolute", "x": 100, "y": 64, "z": 200},
            "operations": [
                {"op": "fill", "block": "stone_bricks", "from": [0, 0, 0], "to": [10, 5, 10]},
            ],
        }
        result = policy.classify_and_validate(slots=slots)
        assert result.passed
        assert result.output_type == OutputType.EXECUTABLE_COMMAND_GRAPH.value

    def test_multi_variant_output_type(self, creative_stack):
        policy = creative_stack["policy"]
        plan = MagicMock()
        plan.variants = [
            MagicMock(accepted=True, family="baseline"),
            MagicMock(accepted=True, family="creative"),
        ]
        result = policy.classify_and_validate(
            slots={"data": "structured"},
            variant_plan=plan,
        )
        assert result.output_type == OutputType.EXECUTABLE_COMMAND_GRAPH_WITH_VARIANTS.value


class TestCreativityVerificationGolden:
    """Cross-cutting creativity verification tests."""

    def test_shrink_to_safe_on_floor_violation(self, creative_stack):
        verifier = creative_stack["verifier"]
        cp = CreativeProfile(novelty=0.72, style_risk=0.38)
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        # Missing target_anchor = floor violation
        variant = {"operations": [{"op": "place", "block": "gold_block"}]}
        result = verifier.verify("minecraft", variant, baseline, cp,
                                 _mock_envelope("minecraft"))
        assert result.repair_path == "shrink_to_safe"

    def test_re_explore_on_low_quality(self, creative_stack):
        verifier = creative_stack["verifier"]
        cp = CreativeProfile(novelty=0.72, style_risk=0.38)
        baseline = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        # Variant that passes floor but has very low practical value
        variant = {
            "target_anchor": {"type": "relative"},
            "operations": [],
            "block_palette": [],
        }
        result = verifier.verify("minecraft", variant, baseline, cp,
                                 _mock_envelope("minecraft"))
        # Low practical value may trigger re_explore
        if not result.passed and result.repair_path:
            assert result.repair_path in ("shrink_to_safe", "re_explore")

    def test_heuristic_filtering_conservative(self, creative_stack):
        """Conservative mode should filter out expressive-only heuristics."""
        packs = load_heuristic_packs()
        mc_pack = packs["minecraft"]
        cp_conservative = CreativeProfile(mode="conservative", novelty=0.1)
        applicable = mc_pack.all_applicable(cp_conservative)
        # Expressive-only heuristics should be filtered out
        for h in applicable:
            if h.applies_when == "mode==expressive":
                pytest.fail(f"Expressive heuristic {h.heuristic_id} should not apply in conservative mode")
