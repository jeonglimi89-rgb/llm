"""Unit tests for training data pipeline, E2E verifier, rubric scorer, A/B test."""
import pytest
from pathlib import Path
from vllm_orchestrator.src.app.training.data_collector import (
    TrainingDataCollector, TrainingSample, CollectionStats,
)
from vllm_orchestrator.src.app.training.adapter_trainer import (
    AdapterTrainingManager, LoRATrainingConfig, get_training_config, list_training_configs,
)
from vllm_orchestrator.src.app.benchmarks.e2e_verifier import (
    E2EVerifier, E2EVerificationResult, E2EReport,
)
from vllm_orchestrator.src.app.benchmarks.rubric_scorer import (
    RubricScorer, HumanScoreSheet,
)
from vllm_orchestrator.src.app.benchmarks.ab_test import (
    ABTestRunner, ABVariant, ABResult, ABReport,
)
from vllm_orchestrator.src.app.benchmarks.golden_benchmarks import get_benchmark, BENCHMARK_CASES


class TestTrainingDataCollector:
    def test_collect_passing_sample(self, tmp_path):
        collector = TrainingDataCollector(output_dir=tmp_path)
        sample = collector.collect(
            domain="minecraft", task_family="build",
            system_prompt="You are a builder", user_input="타워 만들어줘",
            output_slots={"target_anchor": {}, "operations": [{"op": "place"}]},
            evaluation_score=0.8,
        )
        assert sample is not None
        assert sample.domain == "minecraft"
        assert sample.adapter_target == "minecraft_style_adapter"

    def test_collect_failing_sample_rejected(self, tmp_path):
        collector = TrainingDataCollector(output_dir=tmp_path)
        sample = collector.collect(
            domain="cad", task_family="design_drawing",
            system_prompt="sys", user_input="input",
            output_slots={"constraints": []},
            evaluation_score=0.3,  # below threshold
        )
        assert sample is None

    def test_write_sample(self, tmp_path):
        collector = TrainingDataCollector(output_dir=tmp_path)
        sample = collector.collect(
            domain="builder", task_family="exterior_drawing",
            system_prompt="sys", user_input="input",
            output_slots={"spaces": []},
            evaluation_score=0.8,
        )
        path = collector.write_sample(sample)
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_to_chat_format(self, tmp_path):
        collector = TrainingDataCollector(output_dir=tmp_path)
        sample = collector.collect(
            domain="minecraft", task_family="build",
            system_prompt="sys", user_input="input",
            output_slots={"ops": [1]},
            evaluation_score=0.8,
        )
        chat = sample.to_chat_format()
        assert len(chat) == 3
        assert chat[0]["role"] == "system"
        assert chat[2]["role"] == "assistant"

    def test_stats_tracking(self, tmp_path):
        collector = TrainingDataCollector(output_dir=tmp_path)
        collector.collect("minecraft", "build", "sys", "input", {"a": 1}, evaluation_score=0.8)
        collector.collect("minecraft", "build", "sys", "input", {"a": 1}, evaluation_score=0.3)
        stats = collector.get_stats("minecraft")
        assert stats["total_processed"] == 2
        assert stats["quality_passed"] == 1
        assert stats["quality_failed"] == 1


class TestAdapterTrainer:
    def test_all_configs_exist(self):
        configs = list_training_configs()
        assert len(configs) == 4
        domains = {c.domain for c in configs}
        assert domains == {"minecraft", "builder", "animation", "cad"}

    def test_get_config(self):
        c = get_training_config("minecraft_style_adapter")
        assert c is not None
        assert c.rank == 32  # higher rank for creative domain
        assert c.base_model == "Qwen/Qwen2.5-32B-Instruct"

    def test_peft_config(self):
        c = get_training_config("builder_rules_adapter")
        peft = c.to_peft_config()
        assert peft["r"] == 16
        assert peft["task_type"] == "CAUSAL_LM"

    def test_check_readiness_no_data(self, tmp_path):
        mgr = AdapterTrainingManager(data_dir=tmp_path)
        result = mgr.check_readiness("minecraft_style_adapter")
        assert not result["ready"]

    def test_generate_script(self, tmp_path):
        mgr = AdapterTrainingManager(data_dir=tmp_path, output_dir=tmp_path / "out")
        script = mgr.generate_training_script("cad_constraints_adapter")
        assert script is not None
        assert "Qwen/Qwen2.5-32B-Instruct" in script
        assert "cad_constraints_adapter" in script


class TestE2EVerifier:
    def test_verify_success(self):
        v = E2EVerifier()
        case = get_benchmark("mc_build_medieval_tower")
        result = v.verify_output(
            case,
            {"target_anchor": {}, "operations": [{"a": 1}], "block_palette": ["s"], "style": {}},
            evaluation_score=0.8,
        )
        assert result.success

    def test_verify_routing_failure(self):
        v = E2EVerifier()
        case = get_benchmark("mc_build_medieval_tower")
        result = v.verify_output(case, {"a": 1}, routing_passed=False)
        assert not result.success
        assert "routing_failed" in result.errors

    def test_verify_none_output(self):
        v = E2EVerifier()
        case = get_benchmark("cad_shower_filter")
        result = v.verify_output(case, None)
        assert not result.success

    def test_build_report(self):
        v = E2EVerifier()
        results = [
            E2EVerificationResult("c1", "minecraft", success=True, evaluation_score=0.8, total_ms=100),
            E2EVerificationResult("c2", "builder", success=False, evaluation_score=0.3, total_ms=200),
        ]
        report = v.build_report(results)
        assert report.passed == 1
        assert report.failed == 1

    def test_smoke_cases(self):
        cases = E2EVerifier.get_smoke_cases()
        domains = {c.domain for c in cases}
        assert len(domains) >= 4


class TestRubricScorer:
    def test_score_case(self, tmp_path):
        scorer = RubricScorer(storage_dir=tmp_path)
        case = get_benchmark("mc_build_medieval_tower")
        sheet = scorer.score(case, {
            "structural_validity": 4.0,
            "style_coherence": 3.5,
            "functional_completeness": 4.0,
            "creative_quality": 3.0,
        })
        assert sheet.weighted_average > 0
        assert len(sheet.scores) == 4

    def test_save_and_correlation(self, tmp_path):
        scorer = RubricScorer(storage_dir=tmp_path)
        case = get_benchmark("mc_build_medieval_tower")
        sheet = scorer.score(case, {"structural_validity": 4.0}, auto_scores={"structural_validity": 3.5})
        path = scorer.save(sheet)
        assert path.exists()

    def test_correlation_no_data(self, tmp_path):
        scorer = RubricScorer(storage_dir=tmp_path)
        report = scorer.compute_correlation("minecraft")
        assert report.summary == "no data"


class TestABTestRunner:
    def test_compare_two_outputs(self):
        runner = ABTestRunner()
        case = get_benchmark("mc_build_medieval_tower")
        output_a = {"target_anchor": {}, "operations": [], "block_palette": [], "style": {}}
        output_b = {"target_anchor": {}, "operations": [{"a": 1}], "block_palette": ["s"], "style": {"t": "m"}}
        result = runner.compare(
            case, output_a, output_b,
            ABVariant("base"), ABVariant("adapter", adapter_id="minecraft_style_adapter"),
        )
        assert result.winner in ("a", "b", "tie")
        assert isinstance(result.delta, float)

    def test_build_report(self):
        runner = ABTestRunner()
        results = [
            ABResult("c1", "minecraft", winner="b", delta=0.2),
            ABResult("c2", "minecraft", winner="a", delta=-0.15),
            ABResult("c3", "minecraft", winner="tie", delta=0.05),
        ]
        report = runner.build_report("base_vs_adapter", results)
        assert report.a_wins == 1
        assert report.b_wins == 1
        assert report.ties == 1

    def test_report_to_dict(self):
        runner = ABTestRunner()
        report = runner.build_report("test", [])
        d = report.to_dict()
        assert "experiment_name" in d
        assert "conclusion" in d
