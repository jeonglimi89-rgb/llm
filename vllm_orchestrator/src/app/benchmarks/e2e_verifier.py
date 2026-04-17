"""
benchmarks/e2e_verifier.py — End-to-end verification framework.

Runs the full pipeline against golden test cases and verifies:
- Schema compliance
- Domain evaluation scores
- Intervention policy compliance
- Output policy compliance
- Command graph validity
- Benchmark rubric scores

Can run against live vLLM server or mock adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import time

from .golden_benchmarks import BenchmarkCase, BenchmarkEvaluator, BenchmarkResult, BENCHMARK_CASES


@dataclass
class E2EVerificationResult:
    """Result of a single end-to-end verification run."""
    case_id: str
    domain: str
    success: bool = False
    # Pipeline stages
    routing_passed: bool = False
    intervention_passed: bool = False
    schema_passed: bool = False
    evaluation_score: float = 0.0
    output_policy_passed: bool = False
    graph_valid: bool = False
    benchmark_result: Optional[dict] = None
    # Timing
    total_ms: int = 0
    # Errors
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class E2EReport:
    """Complete E2E verification report."""
    results: list[E2EVerificationResult] = field(default_factory=list)
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    avg_score: float = 0.0
    avg_latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.passed / self.total_cases, 2) if self.total_cases else 0,
            "avg_score": round(self.avg_score, 3),
            "avg_latency_ms": self.avg_latency_ms,
            "results": [r.to_dict() for r in self.results],
        }


class E2EVerifier:
    """Runs end-to-end verification against the pipeline."""

    def __init__(self, evaluator: Optional[BenchmarkEvaluator] = None):
        self._evaluator = evaluator or BenchmarkEvaluator()

    def verify_output(
        self,
        case: BenchmarkCase,
        pipeline_output: Optional[dict],
        *,
        routing_passed: bool = True,
        intervention_passed: bool = True,
        schema_passed: bool = True,
        evaluation_score: float = 0.0,
        output_policy_passed: bool = True,
        graph_valid: bool = True,
        latency_ms: int = 0,
    ) -> E2EVerificationResult:
        """Verify a single pipeline output against a benchmark case."""
        errors = []
        if not routing_passed:
            errors.append("routing_failed")
        if not intervention_passed:
            errors.append("intervention_policy_violated")
        if not schema_passed:
            errors.append("schema_validation_failed")
        if not output_policy_passed:
            errors.append("output_policy_violated")
        if not graph_valid:
            errors.append("graph_invalid")

        benchmark_result = None
        if pipeline_output:
            br = self._evaluator.evaluate(case, pipeline_output)
            benchmark_result = br.to_dict()
            if not br.passed:
                errors.append(f"benchmark_below_threshold({br.weighted_average:.2f})")

        success = len(errors) == 0 and pipeline_output is not None

        return E2EVerificationResult(
            case_id=case.case_id,
            domain=case.domain,
            success=success,
            routing_passed=routing_passed,
            intervention_passed=intervention_passed,
            schema_passed=schema_passed,
            evaluation_score=evaluation_score,
            output_policy_passed=output_policy_passed,
            graph_valid=graph_valid,
            benchmark_result=benchmark_result,
            total_ms=latency_ms,
            errors=errors,
        )

    def build_report(self, results: list[E2EVerificationResult]) -> E2EReport:
        """Build a summary report from verification results."""
        passed = sum(1 for r in results if r.success)
        failed = len(results) - passed
        avg_score = (
            sum(r.evaluation_score for r in results) / len(results)
            if results else 0
        )
        avg_latency = (
            sum(r.total_ms for r in results) // len(results)
            if results else 0
        )

        return E2EReport(
            results=results,
            total_cases=len(results),
            passed=passed,
            failed=failed,
            avg_score=avg_score,
            avg_latency_ms=avg_latency,
        )

    @staticmethod
    def get_smoke_cases() -> list[BenchmarkCase]:
        """Return minimal smoke test cases (one per domain)."""
        smoke = []
        seen = set()
        for case in BENCHMARK_CASES:
            if case.domain not in seen:
                smoke.append(case)
                seen.add(case.domain)
        return smoke
