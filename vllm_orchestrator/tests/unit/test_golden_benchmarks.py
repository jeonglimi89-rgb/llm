"""Unit tests for golden_benchmarks module."""
import pytest
from vllm_orchestrator.src.app.benchmarks.golden_benchmarks import (
    BENCHMARK_CASES, BenchmarkCase, BenchmarkEvaluator, BenchmarkResult,
    get_benchmarks_for_domain, get_benchmark,
)


@pytest.fixture
def evaluator():
    return BenchmarkEvaluator()


class TestBenchmarkRegistry:
    def test_total_cases(self):
        assert len(BENCHMARK_CASES) >= 12

    def test_minecraft_cases(self):
        cases = get_benchmarks_for_domain("minecraft")
        assert len(cases) >= 3
        families = {c.task_family for c in cases}
        assert "build" in families
        assert "npc" in families
        assert "resourcepack" in families

    def test_builder_cases(self):
        cases = get_benchmarks_for_domain("builder")
        assert len(cases) >= 2

    def test_animation_cases(self):
        cases = get_benchmarks_for_domain("animation")
        assert len(cases) >= 3

    def test_cad_cases(self):
        cases = get_benchmarks_for_domain("cad")
        assert len(cases) >= 2

    def test_get_by_id(self):
        case = get_benchmark("mc_build_medieval_tower")
        assert case is not None
        assert case.domain == "minecraft"

    def test_all_cases_have_rubric_or_expected_outputs(self):
        for case in BENCHMARK_CASES:
            assert len(case.expected_output_keys) > 0 or len(case.rubric) > 0, \
                f"Case {case.case_id} has no rubric or expected outputs"

    def test_case_to_dict(self):
        case = get_benchmark("mc_build_medieval_tower")
        d = case.to_dict()
        assert "case_id" in d
        assert "rubric" in d


class TestBenchmarkEvaluator:
    def test_evaluate_good_output(self, evaluator):
        case = get_benchmark("mc_build_medieval_tower")
        output = {
            "target_anchor": {"type": "relative", "x": 0, "y": 64, "z": 0},
            "operations": [
                {"op": "place", "block": "cobblestone", "x": 0, "y": 0, "z": 0},
                {"op": "place", "block": "stone_bricks", "x": 1, "y": 0, "z": 0},
            ],
            "block_palette": ["cobblestone", "stone_bricks", "oak_planks"],
            "style": {"theme": "medieval"},
        }
        result = evaluator.evaluate(case, output)
        assert len(result.missing_outputs) == 0
        assert result.weighted_average > 0

    def test_evaluate_missing_outputs(self, evaluator):
        case = get_benchmark("mc_build_medieval_tower")
        output = {"target_anchor": {"type": "relative"}}
        # Missing: operations, block_palette, style
        result = evaluator.evaluate(case, output)
        assert len(result.missing_outputs) > 0

    def test_evaluate_none_output(self, evaluator):
        case = get_benchmark("mc_build_medieval_tower")
        result = evaluator.evaluate(case, None)
        assert not result.passed

    def test_evaluate_cad(self, evaluator):
        case = get_benchmark("cad_shower_filter")
        output = {
            "constraints": [{"type": "sealing", "grade": "IP67"}],
            "systems": ["mechanical", "electrical", "plumbing"],
            "parts": [{"name": "housing"}, {"name": "filter_cartridge"}],
        }
        result = evaluator.evaluate(case, output)
        assert len(result.missing_outputs) == 0

    def test_evaluate_animation(self, evaluator):
        case = get_benchmark("anim_dialogue_closeup")
        output = {
            "framing": "close_up",
            "mood": "tension",
            "camera_move": "dolly_in",
            "shots": [{"shot_type": "close_up", "duration_frames": 72}],
        }
        result = evaluator.evaluate(case, output)
        assert len(result.missing_outputs) == 0

    def test_result_to_dict(self, evaluator):
        case = get_benchmark("mc_build_medieval_tower")
        result = evaluator.evaluate(case, {"target_anchor": {}})
        d = result.to_dict()
        assert "case_id" in d
        assert "weighted_average" in d
