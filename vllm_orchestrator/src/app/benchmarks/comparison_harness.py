"""
benchmarks/comparison_harness.py — Base vs Adapter comparison harness.

Runs benchmark cases under multiple configurations:
- base_only: core_text_32b without adapter
- base_plus_adapter: core_text_32b with domain adapter
- base_plus_adapter_plus_review: adapter on + review phase adapter on
- creative_variant: creative tier with/without adapter

Results include rubric scores, pass/fail, diff summary, and
explicit base-vs-adapter improvement/degradation tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .golden_benchmarks import BenchmarkCase, BenchmarkResult, BenchmarkEvaluator


class BenchmarkConfig(str):
    BASE_ONLY = "base_only"
    BASE_PLUS_ADAPTER = "base_plus_adapter"
    BASE_PLUS_ADAPTER_REVIEW = "base_plus_adapter_plus_review"
    CREATIVE_ON = "creative_variant_on"
    CREATIVE_OFF = "creative_variant_off"


@dataclass
class ComparisonEntry:
    """Single benchmark result under a specific configuration."""
    config: str
    result: BenchmarkResult
    adapter_id: str = ""
    model_tier: str = ""
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "result": self.result.to_dict(),
            "adapter_id": self.adapter_id,
            "model_tier": self.model_tier,
            "latency_ms": self.latency_ms,
        }


@dataclass
class DimensionDiff:
    """Diff for a single rubric dimension between configs."""
    dimension: str
    base_score: float = 0.0
    adapter_score: float = 0.0
    delta: float = 0.0
    improved: bool = False
    degraded: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ComparisonResult:
    """Full comparison result for a benchmark case across configs."""
    case_id: str
    domain: str
    entries: list[ComparisonEntry] = field(default_factory=list)
    dimension_diffs: list[DimensionDiff] = field(default_factory=list)
    overall_improvement: float = 0.0
    degraded_dimensions: list[str] = field(default_factory=list)
    improved_dimensions: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "entries": [e.to_dict() for e in self.entries],
            "dimension_diffs": [d.to_dict() for d in self.dimension_diffs],
            "overall_improvement": self.overall_improvement,
            "degraded_dimensions": self.degraded_dimensions,
            "improved_dimensions": self.improved_dimensions,
            "summary": self.summary,
        }


@dataclass
class HarnessReport:
    """Complete harness report across all benchmark cases."""
    comparisons: list[ComparisonResult] = field(default_factory=list)
    total_cases: int = 0
    adapter_improved_count: int = 0
    adapter_degraded_count: int = 0
    no_change_count: int = 0

    def to_dict(self) -> dict:
        return {
            "comparisons": [c.to_dict() for c in self.comparisons],
            "total_cases": self.total_cases,
            "adapter_improved_count": self.adapter_improved_count,
            "adapter_degraded_count": self.adapter_degraded_count,
            "no_change_count": self.no_change_count,
        }


class ComparisonHarness:
    """Runs benchmark cases under multiple configurations and compares results."""

    def __init__(self, evaluator: Optional[BenchmarkEvaluator] = None):
        self._evaluator = evaluator or BenchmarkEvaluator()

    def compare(
        self,
        case: BenchmarkCase,
        base_output: Optional[dict],
        adapter_output: Optional[dict],
        *,
        adapter_id: str = "",
        base_latency_ms: int = 0,
        adapter_latency_ms: int = 0,
    ) -> ComparisonResult:
        """Compare base vs adapter output for a single benchmark case."""
        base_result = self._evaluator.evaluate(case, base_output)
        adapter_result = self._evaluator.evaluate(case, adapter_output)

        entries = [
            ComparisonEntry(
                config=BenchmarkConfig.BASE_ONLY,
                result=base_result,
                model_tier="text",
                latency_ms=base_latency_ms,
            ),
            ComparisonEntry(
                config=BenchmarkConfig.BASE_PLUS_ADAPTER,
                result=adapter_result,
                adapter_id=adapter_id,
                model_tier="text",
                latency_ms=adapter_latency_ms,
            ),
        ]

        # Compute dimension diffs
        diffs = []
        improved = []
        degraded = []
        for dim_name in base_result.scores:
            base_s = base_result.scores.get(dim_name, 0)
            adap_s = adapter_result.scores.get(dim_name, 0)
            delta = adap_s - base_s
            d = DimensionDiff(
                dimension=dim_name,
                base_score=base_s,
                adapter_score=adap_s,
                delta=round(delta, 3),
                improved=delta > 0.1,
                degraded=delta < -0.1,
            )
            diffs.append(d)
            if d.improved:
                improved.append(dim_name)
            if d.degraded:
                degraded.append(dim_name)

        overall = (adapter_result.weighted_average - base_result.weighted_average)

        summary_parts = []
        if improved:
            summary_parts.append(f"Improved: {', '.join(improved)}")
        if degraded:
            summary_parts.append(f"Degraded: {', '.join(degraded)}")
        if not improved and not degraded:
            summary_parts.append("No significant change")

        return ComparisonResult(
            case_id=case.case_id,
            domain=case.domain,
            entries=entries,
            dimension_diffs=diffs,
            overall_improvement=round(overall, 3),
            degraded_dimensions=degraded,
            improved_dimensions=improved,
            summary="; ".join(summary_parts),
        )

    def run_harness(
        self,
        cases: list[BenchmarkCase],
        base_outputs: dict[str, Optional[dict]],
        adapter_outputs: dict[str, Optional[dict]],
    ) -> HarnessReport:
        """Run full comparison harness across all cases."""
        comparisons = []
        improved = 0
        degraded_count = 0
        no_change = 0

        for case in cases:
            base_out = base_outputs.get(case.case_id)
            adap_out = adapter_outputs.get(case.case_id)
            comp = self.compare(case, base_out, adap_out)
            comparisons.append(comp)

            if comp.overall_improvement > 0.1:
                improved += 1
            elif comp.overall_improvement < -0.1:
                degraded_count += 1
            else:
                no_change += 1

        return HarnessReport(
            comparisons=comparisons,
            total_cases=len(cases),
            adapter_improved_count=improved,
            adapter_degraded_count=degraded_count,
            no_change_count=no_change,
        )
