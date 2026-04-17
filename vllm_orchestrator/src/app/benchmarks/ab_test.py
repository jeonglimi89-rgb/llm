"""
benchmarks/ab_test.py — A/B test framework for model quality comparison.

Runs identical requests through two configurations (A and B) and
measures quality differences. Supports:
- base vs adapter comparison
- model tier comparison
- creative profile comparison

Results are stored for statistical analysis.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .golden_benchmarks import BenchmarkCase, BenchmarkEvaluator, BenchmarkResult


@dataclass
class ABVariant:
    """Configuration for one side of an A/B test."""
    name: str                           # "base" or "adapter"
    model_tier: str = "text"
    adapter_id: str = ""
    creative_profile: Optional[dict] = None
    extra_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ABResult:
    """Result of a single A/B test run."""
    case_id: str
    domain: str
    variant_a: ABVariant = field(default_factory=lambda: ABVariant("a"))
    variant_b: ABVariant = field(default_factory=lambda: ABVariant("b"))
    score_a: float = 0.0
    score_b: float = 0.0
    winner: str = ""                    # "a"|"b"|"tie"
    delta: float = 0.0
    dimensions_a_better: list[str] = field(default_factory=list)
    dimensions_b_better: list[str] = field(default_factory=list)
    latency_a_ms: int = 0
    latency_b_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "score_a": self.score_a,
            "score_b": self.score_b,
            "winner": self.winner,
            "delta": self.delta,
            "dimensions_a_better": self.dimensions_a_better,
            "dimensions_b_better": self.dimensions_b_better,
        }


@dataclass
class ABReport:
    """Summary report across multiple A/B test cases."""
    experiment_name: str
    results: list[ABResult] = field(default_factory=list)
    a_wins: int = 0
    b_wins: int = 0
    ties: int = 0
    avg_delta: float = 0.0
    conclusion: str = ""

    def to_dict(self) -> dict:
        return {
            "experiment_name": self.experiment_name,
            "total_cases": len(self.results),
            "a_wins": self.a_wins,
            "b_wins": self.b_wins,
            "ties": self.ties,
            "avg_delta": round(self.avg_delta, 3),
            "conclusion": self.conclusion,
            "results": [r.to_dict() for r in self.results],
        }


class ABTestRunner:
    """Runs A/B tests comparing two model configurations."""

    def __init__(
        self,
        evaluator: Optional[BenchmarkEvaluator] = None,
        significance_threshold: float = 0.1,
    ):
        self._evaluator = evaluator or BenchmarkEvaluator()
        self._threshold = significance_threshold

    def compare(
        self,
        case: BenchmarkCase,
        output_a: Optional[dict],
        output_b: Optional[dict],
        variant_a: ABVariant,
        variant_b: ABVariant,
        *,
        latency_a_ms: int = 0,
        latency_b_ms: int = 0,
    ) -> ABResult:
        """Compare two outputs for a single benchmark case."""
        result_a = self._evaluator.evaluate(case, output_a)
        result_b = self._evaluator.evaluate(case, output_b)

        delta = result_b.weighted_average - result_a.weighted_average

        if abs(delta) < self._threshold:
            winner = "tie"
        elif delta > 0:
            winner = "b"
        else:
            winner = "a"

        # Dimension-level comparison
        dims_a_better = []
        dims_b_better = []
        for dim_name in result_a.scores:
            sa = result_a.scores.get(dim_name, 0)
            sb = result_b.scores.get(dim_name, 0)
            if sa - sb > 0.1:
                dims_a_better.append(dim_name)
            elif sb - sa > 0.1:
                dims_b_better.append(dim_name)

        return ABResult(
            case_id=case.case_id,
            domain=case.domain,
            variant_a=variant_a,
            variant_b=variant_b,
            score_a=round(result_a.weighted_average, 3),
            score_b=round(result_b.weighted_average, 3),
            winner=winner,
            delta=round(delta, 3),
            dimensions_a_better=dims_a_better,
            dimensions_b_better=dims_b_better,
            latency_a_ms=latency_a_ms,
            latency_b_ms=latency_b_ms,
        )

    def build_report(
        self,
        experiment_name: str,
        results: list[ABResult],
    ) -> ABReport:
        """Build summary report from A/B test results."""
        a_wins = sum(1 for r in results if r.winner == "a")
        b_wins = sum(1 for r in results if r.winner == "b")
        ties = sum(1 for r in results if r.winner == "tie")
        avg_delta = sum(r.delta for r in results) / len(results) if results else 0

        if b_wins > a_wins * 1.5:
            conclusion = f"Variant B is significantly better ({b_wins} wins vs {a_wins})"
        elif a_wins > b_wins * 1.5:
            conclusion = f"Variant A is significantly better ({a_wins} wins vs {b_wins})"
        else:
            conclusion = f"No significant difference ({a_wins} vs {b_wins}, {ties} ties)"

        return ABReport(
            experiment_name=experiment_name,
            results=results,
            a_wins=a_wins,
            b_wins=b_wins,
            ties=ties,
            avg_delta=round(avg_delta, 3),
            conclusion=conclusion,
        )
