"""Unit tests for routing_calibrator, prompt_budget, runtime_hardening, review_split, adapter_drift."""
import pytest
from vllm_orchestrator.src.app.llm.model_router import ModelRouter, CORE_TEXT_32B, CORE_CODE_32B
from vllm_orchestrator.src.app.llm.adapter_registry import AdapterRegistry
from vllm_orchestrator.src.app.llm.adapter_policy import AdapterActivationPolicy, PipelinePhase
from vllm_orchestrator.src.app.llm.routing_calibrator import RoutingCalibrator
from vllm_orchestrator.src.app.llm.prompt_budget import PromptBudgetManager, PromptSection
from vllm_orchestrator.src.app.llm.runtime_hardening import RuntimeHardening, LLMFailureClass
from vllm_orchestrator.src.app.llm.review_model_split import get_review_policy
from vllm_orchestrator.src.app.llm.adapter_drift import AdapterDriftChecker
from vllm_orchestrator.src.app.benchmarks.comparison_harness import ComparisonHarness
from vllm_orchestrator.src.app.benchmarks.golden_benchmarks import get_benchmark


@pytest.fixture
def calibrator():
    router = ModelRouter()
    registry = AdapterRegistry()
    policy = AdapterActivationPolicy(registry)
    return RoutingCalibrator(router, policy)


class TestRoutingCalibrator:
    def test_basic_text_routing(self, calibrator):
        r = calibrator.calibrate("builder.requirement_parse", "strict-json-pool", "builder")
        assert r.model_decision.logical_id == CORE_TEXT_32B
        assert "domain=builder" in r.explainable_reason

    def test_code_task_routing(self, calibrator):
        r = calibrator.calibrate("builder.floor_plan_generate", "strict-json-pool", "builder",
                                 is_code_task=True)
        assert r.model_decision.logical_id == CORE_CODE_32B
        assert not r.adapter_decision.should_attach

    def test_high_code_density_auto_code(self, calibrator):
        r = calibrator.calibrate("cad.constraint_parse", "strict-json-pool", "cad",
                                 code_density=0.7)
        assert r.model_decision.logical_id == CORE_CODE_32B

    def test_long_context_routing(self, calibrator):
        r = calibrator.calibrate("builder.requirement_parse", "strict-json-pool", "builder",
                                 context_token_count=20000)
        assert r.model_decision.tier == "long_context"

    def test_creative_variant_routing(self, calibrator):
        r = calibrator.calibrate("minecraft.build_parse", "strict-json-pool", "minecraft",
                                 phase=PipelinePhase.CREATIVE_VARIANT.value)
        assert r.model_decision.tier == "creative"

    def test_calibration_factors_populated(self, calibrator):
        r = calibrator.calibrate("animation.shot_parse", "strict-json-pool", "animation",
                                 creativity_demand=0.8, strictness_demand=0.3)
        assert r.calibration_factors["creativity_demand"] == 0.8
        assert r.calibration_factors["strictness_demand"] == 0.3

    def test_result_to_dict(self, calibrator):
        r = calibrator.calibrate("cad.constraint_parse", "strict-json-pool", "cad")
        d = r.to_dict()
        assert "model" in d
        assert "adapter" in d
        assert "calibration" in d
        assert "reason" in d


class TestPromptBudget:
    def test_all_fits(self):
        mgr = PromptBudgetManager(max_context=10000, output_reserve=500)
        sections = [
            PromptSection("system", "You are an expert", priority=0, category="system"),
            PromptSection("user", "만들어줘" * 100, priority=1, category="user"),
        ]
        packed, alloc = mgr.pack(sections)
        assert len(packed) == 2
        assert not alloc.overflow
        assert alloc.utilization < 1.0

    def test_overflow_trims(self):
        mgr = PromptBudgetManager(max_context=500, output_reserve=100)
        sections = [
            PromptSection("system", "critical" * 100, priority=0, category="system"),
            PromptSection("context", "history" * 500, priority=5, category="context"),
            PromptSection("low_value", "extra" * 500, priority=9, category="context"),
        ]
        packed, alloc = mgr.pack(sections)
        assert alloc.overflow
        assert len(alloc.trimmed_sections) > 0

    def test_compact_mode(self):
        mgr = PromptBudgetManager(max_context=10000, output_reserve=500)
        sections = [PromptSection("data", "x" * 8000, priority=5, category="context")]
        packed, alloc = mgr.pack(sections, compact_mode=True)
        # compact mode uses 70% of budget
        assert alloc.total_tokens <= 10000

    def test_allocation_to_dict(self):
        mgr = PromptBudgetManager()
        sections = [PromptSection("sys", "test", category="system")]
        _, alloc = mgr.pack(sections)
        d = alloc.to_dict()
        assert "system_tokens" in d
        assert "remaining" not in d  # property, not in dict


class TestRuntimeHardening:
    def test_classify_timeout(self):
        rh = RuntimeHardening()
        e = rh.classify_failure(TimeoutError("connection timed out"))
        assert e.event_type == LLMFailureClass.TIMEOUT.value
        assert e.retryable

    def test_classify_connection_refused(self):
        rh = RuntimeHardening()
        e = rh.classify_failure(ConnectionError("connection refused"))
        assert e.event_type == LLMFailureClass.MODEL_ENDPOINT_UNAVAILABLE.value

    def test_classify_adapter_not_found(self):
        rh = RuntimeHardening()
        e = rh.classify_failure(RuntimeError("adapter not found: test_adapter"))
        assert e.event_type == LLMFailureClass.ADAPTER_NOT_FOUND.value
        assert not e.retryable

    def test_classify_context_exceeded(self):
        rh = RuntimeHardening()
        e = rh.classify_failure(ValueError("input too long, context limit exceeded"))
        assert e.event_type == LLMFailureClass.CONTEXT_LIMIT_EXCEEDED.value

    def test_should_retry(self):
        rh = RuntimeHardening()
        timeout_event = rh.classify_failure(TimeoutError("timeout"))
        assert rh.should_retry(timeout_event)
        adapter_event = rh.classify_failure(RuntimeError("adapter not found"))
        assert not rh.should_retry(adapter_event)

    def test_degraded_event(self):
        rh = RuntimeHardening()
        e = rh.create_degraded_event("creative", "text", "creative endpoint down")
        assert e.degraded
        assert e.event_type == LLMFailureClass.DEGRADED_MODE_FALLBACK.value


class TestReviewModelSplit:
    def test_builder_review_strictness(self):
        p = get_review_policy("builder")
        review = p.get_config("review")
        assert review.strictness_boost == 0.1
        assert review.adapter_attach is True

    def test_cad_review_highest_strictness(self):
        p = get_review_policy("cad")
        review = p.get_config("review")
        assert review.strictness_boost == 0.15

    def test_minecraft_creative_with_adapter(self):
        p = get_review_policy("minecraft")
        creative = p.get_config("creative_variant")
        assert creative.adapter_attach is True
        assert creative.tier_preference == "creative"

    def test_cad_creative_no_adapter(self):
        p = get_review_policy("cad")
        creative = p.get_config("creative_variant")
        assert creative.adapter_attach is False

    def test_all_domains_have_policy(self):
        for domain in ["builder", "cad", "minecraft", "animation"]:
            p = get_review_policy(domain)
            assert p.domain == domain

    def test_generation_vs_review_distinction(self):
        p = get_review_policy("builder")
        gen = p.get_config("generation")
        rev = p.get_config("review")
        assert rev.strictness_boost > gen.strictness_boost


class TestAdapterDrift:
    def test_no_drift(self):
        checker = AdapterDriftChecker()
        report = checker.analyze(
            "test_adapter", "test",
            base_scores={"generic_reasoning": 0.8, "instruction_following": 0.9},
            adapter_scores={"generic_reasoning": 0.78, "instruction_following": 0.88},
            domain_benchmark_gain=0.2,
        )
        assert report.acceptable
        assert len(report.warnings) == 0

    def test_significant_drift(self):
        checker = AdapterDriftChecker()
        report = checker.analyze(
            "bad_adapter", "test",
            base_scores={"generic_reasoning": 0.8, "instruction_following": 0.9, "hallucination_rate": 0.1},
            adapter_scores={"generic_reasoning": 0.5, "instruction_following": 0.6, "hallucination_rate": 0.4},
            domain_benchmark_gain=0.1,
        )
        assert not report.acceptable
        assert len(report.warnings) > 0
        assert report.general_loss < 0

    def test_net_tradeoff(self):
        checker = AdapterDriftChecker()
        report = checker.analyze(
            "adapter", "domain",
            base_scores={"generic_reasoning": 0.7},
            adapter_scores={"generic_reasoning": 0.65},
            domain_benchmark_gain=0.3,
        )
        assert report.net_tradeoff > 0  # gain > loss

    def test_report_to_dict(self):
        checker = AdapterDriftChecker()
        report = checker.analyze("a", "d", {}, {})
        d = report.to_dict()
        assert "adapter_id" in d
        assert "net_tradeoff" in d


class TestComparisonHarness:
    def test_compare_base_vs_adapter(self):
        harness = ComparisonHarness()
        case = get_benchmark("mc_build_medieval_tower")
        base = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}], "block_palette": ["stone"], "style": {}}
        adapter = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}, {"op": "fill"}], "block_palette": ["stone", "oak"], "style": {"theme": "medieval"}}
        result = harness.compare(case, base, adapter, adapter_id="minecraft_style_adapter")
        assert len(result.entries) == 2
        assert result.entries[0].config == "base_only"
        assert result.entries[1].config == "base_plus_adapter"

    def test_harness_report(self):
        harness = ComparisonHarness()
        case = get_benchmark("mc_build_medieval_tower")
        report = harness.run_harness(
            [case],
            {"mc_build_medieval_tower": {"target_anchor": {}, "operations": [], "block_palette": [], "style": {}}},
            {"mc_build_medieval_tower": {"target_anchor": {}, "operations": [{"a": 1}], "block_palette": ["s"], "style": {"t": "m"}}},
        )
        assert report.total_cases == 1

    def test_result_to_dict(self):
        harness = ComparisonHarness()
        case = get_benchmark("cad_shower_filter")
        result = harness.compare(case, {"constraints": []}, {"constraints": [{"a": 1}], "systems": [], "parts": []})
        d = result.to_dict()
        assert "entries" in d
        assert "dimension_diffs" in d
