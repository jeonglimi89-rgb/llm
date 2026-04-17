"""Unit tests for heuristic_checks / HeuristicDispatcher module."""
import pytest
from vllm_orchestrator.src.app.domain.heuristic_checks import (
    HeuristicDispatcher, HeuristicCheckResult,
    check_block_existence, check_anchor_validity, check_dimension_sanity,
    check_code_compliance, check_camera_continuity, check_180_degree_rule,
)
from vllm_orchestrator.src.app.domain.heuristics import (
    Heuristic, HeuristicPack, load_heuristic_packs,
)
from vllm_orchestrator.src.app.domain.creative_profile import CreativeProfile


@pytest.fixture
def dispatcher():
    return HeuristicDispatcher()


@pytest.fixture
def packs():
    return load_heuristic_packs()


class TestIndividualChecks:
    def test_block_existence_valid(self):
        h = Heuristic("test", "minecraft", "safety", "always", 0, "check_block_existence", "fix")
        slots = {"block_palette": ["stone", "oak_planks"], "operations": [{"op": "place", "block": "stone"}]}
        result = check_block_existence(slots, h)
        assert result.passed

    def test_block_existence_invalid(self):
        h = Heuristic("test", "minecraft", "safety", "always", 0, "check_block_existence", "fix")
        slots = {"block_palette": ["_invalid_block", "stone"]}
        result = check_block_existence(slots, h)
        assert not result.passed

    def test_anchor_validity_pass(self):
        h = Heuristic("test", "minecraft", "safety", "always", 0, "check_anchor_validity", "fix")
        result = check_anchor_validity({"target_anchor": {"type": "relative"}}, h)
        assert result.passed

    def test_anchor_validity_fail(self):
        h = Heuristic("test", "minecraft", "safety", "always", 0, "check_anchor_validity", "fix")
        result = check_anchor_validity({}, h)
        assert not result.passed

    def test_dimension_sanity_pass(self):
        h = Heuristic("test", "cad", "safety", "always", 0, "check_dimension_sanity", "fix")
        result = check_dimension_sanity({"dimensions": {"width": 100, "height": 50}}, h)
        assert result.passed

    def test_dimension_sanity_fail_negative(self):
        h = Heuristic("test", "cad", "safety", "always", 0, "check_dimension_sanity", "fix")
        result = check_dimension_sanity({"dimensions": {"width": -5}}, h)
        assert not result.passed

    def test_code_compliance_pass(self):
        h = Heuristic("test", "builder", "safety", "always", 0, "check_code_compliance", "fix")
        result = check_code_compliance({"spaces": [{"name": "living"}]}, h)
        assert result.passed

    def test_code_compliance_fail(self):
        h = Heuristic("test", "builder", "safety", "always", 0, "check_code_compliance", "fix")
        result = check_code_compliance({"style": "modern"}, h)
        assert not result.passed

    def test_camera_continuity_pass(self):
        h = Heuristic("test", "animation", "safety", "always", 0, "check_camera_continuity", "fix")
        result = check_camera_continuity({"shots": [{"framing": "close_up"}]}, h)
        assert result.passed

    def test_camera_continuity_fail(self):
        h = Heuristic("test", "animation", "safety", "always", 0, "check_camera_continuity", "fix")
        result = check_camera_continuity({"shots": [{}]}, h)
        assert not result.passed

    def test_180_degree_rule(self):
        h = Heuristic("test", "animation", "safety", "always", 0, "check_180_degree_rule", "fix")
        result = check_180_degree_rule({"shots": [{"framing": "wide"}, {"framing": "close_up"}]}, h)
        assert result.passed


class TestHeuristicDispatcher:
    def test_run_all_minecraft(self, dispatcher, packs):
        mc_pack = packs["minecraft"]
        slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "stone"}],
            "block_palette": ["stone", "oak_planks"],
        }
        cp = CreativeProfile(mode="balanced", novelty=0.72)
        results = dispatcher.run_all(mc_pack, slots, cp)
        assert len(results) > 0
        assert all(isinstance(r, HeuristicCheckResult) for r in results)

    def test_run_safety_only(self, dispatcher, packs):
        mc_pack = packs["minecraft"]
        slots = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]}
        results = dispatcher.run_safety_only(mc_pack, slots)
        assert len(results) == len(mc_pack.safety_heuristics)

    def test_has_failures_true(self, dispatcher):
        results = [
            HeuristicCheckResult("h1", True),
            HeuristicCheckResult("h2", False, "error", "bad"),
        ]
        assert dispatcher.has_failures(results)

    def test_has_failures_false(self, dispatcher):
        results = [
            HeuristicCheckResult("h1", True),
            HeuristicCheckResult("h2", True),
        ]
        assert not dispatcher.has_failures(results)

    def test_failure_summary(self, dispatcher):
        results = [
            HeuristicCheckResult("h1", True),
            HeuristicCheckResult("h2", False, "error", "something broke", "fix it"),
        ]
        summary = dispatcher.failure_summary(results)
        assert len(summary) == 1
        assert summary[0]["heuristic_id"] == "h2"

    def test_unknown_check_fn_skipped(self, dispatcher):
        pack = HeuristicPack(
            domain="test",
            safety_heuristics=[
                Heuristic("h1", "test", "safety", "always", 0, "nonexistent_fn", "fix"),
            ],
        )
        results = dispatcher.run_safety_only(pack, {"a": 1})
        assert len(results) == 1
        assert results[0].passed  # Skipped with info

    def test_all_domains_run_without_error(self, dispatcher, packs):
        test_slots = {
            "minecraft": {"target_anchor": {"type": "rel"}, "operations": [{"op": "place"}], "block_palette": ["stone"]},
            "builder": {"spaces": [{"name": "living"}], "floors": 2},
            "animation": {"framing": "close_up", "mood": "tense", "shots": [{"framing": "close_up"}]},
            "cad": {"constraints": [{"type": "dim"}], "dimensions": {"width": 100}},
        }
        for domain, slots in test_slots.items():
            pack = packs.get(domain)
            if pack:
                results = dispatcher.run_all(pack, slots)
                # Should not raise
                assert isinstance(results, list)
