"""Unit tests for variant_planner module."""
import pytest
from unittest.mock import MagicMock

from vllm_orchestrator.src.app.domain.creative_profile import CreativeProfile
from vllm_orchestrator.src.app.domain.creative_boundaries import (
    BoundaryEnforcer, BoundaryCheckResult, init_creative_boundaries,
)
from vllm_orchestrator.src.app.review.domain_evaluator import DomainEvaluation
from vllm_orchestrator.src.app.orchestration.variant_planner import (
    VariantPlanner, VariantPlan, VariantSpec, _compute_diff,
)


@pytest.fixture
def enforcer():
    return BoundaryEnforcer(init_creative_boundaries())


@pytest.fixture
def mock_evaluator():
    evaluator = MagicMock()
    evaluator.evaluate.return_value = DomainEvaluation(
        overall_score=0.75, passed=True,
        domain_match=1.0, constraint_coverage=0.8,
        terminology_accuracy=0.7, output_schema_compliance=0.9,
        actionability=0.8, hallucination_risk=0.1,
    )
    return evaluator


@pytest.fixture
def planner(enforcer, mock_evaluator):
    return VariantPlanner(
        boundary_enforcer=enforcer,
        evaluator=mock_evaluator,
    )


@pytest.fixture
def mock_classification():
    c = MagicMock()
    c.primary_domain = "minecraft"
    return c


@pytest.fixture
def mock_profile():
    p = MagicMock()
    p.domain = "minecraft"
    p.vocabulary = {"블록": 2.5}
    p.required_output_keys = {"target_anchor", "operations"}
    return p


@pytest.fixture
def mock_envelope():
    e = MagicMock()
    e.hard_constraints = []
    e.soft_preferences = []
    e.target_domain = "minecraft"
    e.user_intent = "중세풍 타워 만들어줘"
    e.domain_specific = {}
    return e


class TestVariantPlanner:
    def test_single_variant_returns_baseline_only(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=1)
        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        plan = planner.plan_variants(
            "minecraft", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        assert len(plan.variants) == 1
        assert plan.variants[0].family == "safe_baseline"

    def test_multi_variant_generates_requested_count(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=3)
        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        plan = planner.plan_variants(
            "minecraft", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        assert len(plan.variants) == 3
        families = [v.family for v in plan.variants]
        assert "safe_baseline" in families
        assert "creative_variant" in families
        assert "world_expansion_variant" in families

    def test_baseline_always_first(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=2)
        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        plan = planner.plan_variants(
            "minecraft", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        assert plan.variants[0].strategy == "original"

    def test_selected_variant_is_accepted(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=3)
        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        plan = planner.plan_variants(
            "minecraft", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        selected = next(
            (v for v in plan.variants if v.variant_id == plan.selected_variant_id),
            None,
        )
        assert selected is not None
        assert selected.accepted

    def test_none_base_slots_returns_empty(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=3)
        plan = planner.plan_variants(
            "minecraft", cp, None, mock_envelope,
            mock_classification, mock_profile,
        )
        assert len(plan.variants) == 0

    def test_builder_variant_families(self, planner, mock_classification, mock_profile, mock_envelope):
        mock_profile.domain = "builder"
        cp = CreativeProfile(variant_count=2)
        base_slots = {"spaces": [{"name": "living"}], "floors": 2}
        plan = planner.plan_variants(
            "builder", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        families = [v.family for v in plan.variants]
        assert "compliance_safe_plan" in families
        assert "design_enhanced_plan" in families

    def test_cad_variant_families(self, planner, mock_classification, mock_profile, mock_envelope):
        mock_profile.domain = "cad"
        cp = CreativeProfile(variant_count=2)
        base_slots = {"constraints": [{"type": "dimension"}]}
        plan = planner.plan_variants(
            "cad", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        families = [v.family for v in plan.variants]
        assert "baseline_engineering_solution" in families
        assert "compact_or_creative_concept_variant" in families

    def test_variant_plan_to_dict(self, planner, mock_classification, mock_profile, mock_envelope):
        cp = CreativeProfile(variant_count=2)
        base_slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place"}],
        }
        plan = planner.plan_variants(
            "minecraft", cp, base_slots, mock_envelope,
            mock_classification, mock_profile,
        )
        d = plan.to_dict()
        assert "creative_profile" in d
        assert "variants" in d
        assert "selected_variant_id" in d
        assert isinstance(d["variants"], list)


class TestComputeDiff:
    def test_identical(self):
        assert _compute_diff({"a": 1}, {"a": 1}) == {}

    def test_different_value(self):
        diff = _compute_diff({"a": 1}, {"a": 2})
        assert "a" in diff
        assert diff["a"]["base"] == 1
        assert diff["a"]["variant"] == 2

    def test_new_key(self):
        diff = _compute_diff({"a": 1}, {"a": 1, "b": 2})
        assert "b" in diff

    def test_empty_inputs(self):
        assert _compute_diff({}, {}) == {}
        assert _compute_diff(None, None) == {}


class TestVariantSpec:
    def test_to_dict(self):
        vs = VariantSpec(family="safe_baseline", label="Test", strategy="original")
        d = vs.to_dict()
        assert d["family"] == "safe_baseline"
        assert "variant_id" in d

    def test_get_variant_families(self):
        families = VariantPlanner.get_variant_families("minecraft")
        assert len(families) == 3
        assert families[0]["family"] == "safe_baseline"

    def test_get_strategy_prompt(self):
        prompt = VariantPlanner.get_strategy_prompt("style_shifted")
        assert "CREATIVE VARIANT" in prompt
        assert VariantPlanner.get_strategy_prompt("original") == ""
