"""
benchmarks/rubric_scorer.py — Human rubric scoring system.

Provides interface for manual quality scoring of pipeline outputs
against benchmark rubric dimensions. Stores scores for correlation
analysis with heuristic auto-scores.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .golden_benchmarks import BenchmarkCase, RubricDimension


@dataclass
class HumanScore:
    """A single human-assigned rubric score."""
    case_id: str
    dimension: str
    score: float                        # 1-5 scale
    scorer_id: str = "default"
    notes: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HumanScoreSheet:
    """Complete human scoring for one benchmark case."""
    case_id: str
    domain: str
    scores: list[HumanScore] = field(default_factory=list)
    weighted_average: float = 0.0
    auto_score_average: float = 0.0     # for correlation
    correlation: float = 0.0            # human vs auto correlation

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "scores": [s.to_dict() for s in self.scores],
            "weighted_average": self.weighted_average,
            "auto_score_average": self.auto_score_average,
            "correlation": self.correlation,
        }


@dataclass
class CorrelationReport:
    """Correlation analysis between human and auto scores."""
    domain: str
    sample_count: int = 0
    mean_human: float = 0.0
    mean_auto: float = 0.0
    correlation: float = 0.0           # Pearson correlation
    dimensions_with_gap: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RubricScorer:
    """Manages human rubric scoring and correlation analysis."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir:
            self._storage = storage_dir
        else:
            try:
                from ..storage.paths import benchmark_dir
                self._storage = benchmark_dir()
            except Exception:
                self._storage = Path("./benchmark_scores")
        self._sheets: dict[str, HumanScoreSheet] = {}

    def score(
        self,
        case: BenchmarkCase,
        dimension_scores: dict[str, float],
        *,
        scorer_id: str = "default",
        notes: Optional[dict[str, str]] = None,
        auto_scores: Optional[dict[str, float]] = None,
    ) -> HumanScoreSheet:
        """Record human scores for a benchmark case.

        Args:
            case: The benchmark case being scored
            dimension_scores: {dimension_name: score (1-5)}
            scorer_id: Who is scoring
            notes: Optional notes per dimension
            auto_scores: Auto-generated scores for correlation
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        scores = []
        for dim in case.rubric:
            val = dimension_scores.get(dim.name, 0)
            scores.append(HumanScore(
                case_id=case.case_id,
                dimension=dim.name,
                score=max(1.0, min(5.0, val)),
                scorer_id=scorer_id,
                notes=(notes or {}).get(dim.name, ""),
                timestamp=now,
            ))

        # Weighted average
        total_weight = sum(d.weight for d in case.rubric)
        weighted = sum(
            dimension_scores.get(d.name, 0) * d.weight
            for d in case.rubric
        ) / total_weight if total_weight > 0 else 0

        # Auto-score average for correlation
        auto_avg = 0.0
        if auto_scores:
            auto_avg = sum(auto_scores.values()) / len(auto_scores) if auto_scores else 0

        sheet = HumanScoreSheet(
            case_id=case.case_id,
            domain=case.domain,
            scores=scores,
            weighted_average=round(weighted, 2),
            auto_score_average=round(auto_avg, 2),
        )

        self._sheets[case.case_id] = sheet
        return sheet

    def save(self, sheet: HumanScoreSheet) -> Path:
        """Save a score sheet to storage."""
        self._storage.mkdir(parents=True, exist_ok=True)
        filepath = self._storage / f"{sheet.case_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(sheet.to_dict(), f, ensure_ascii=False, indent=2)
        return filepath

    def compute_correlation(
        self,
        domain: str,
        sheets: Optional[list[HumanScoreSheet]] = None,
    ) -> CorrelationReport:
        """Compute correlation between human and auto scores for a domain."""
        domain_sheets = sheets or [
            s for s in self._sheets.values() if s.domain == domain
        ]

        if not domain_sheets:
            return CorrelationReport(domain=domain, summary="no data")

        human_scores = [s.weighted_average for s in domain_sheets]
        auto_scores = [s.auto_score_average for s in domain_sheets]

        n = len(human_scores)
        mean_h = sum(human_scores) / n
        mean_a = sum(auto_scores) / n

        # Simple Pearson correlation
        corr = 0.0
        if n >= 2:
            num = sum((h - mean_h) * (a - mean_a) for h, a in zip(human_scores, auto_scores))
            den_h = sum((h - mean_h) ** 2 for h in human_scores) ** 0.5
            den_a = sum((a - mean_a) ** 2 for a in auto_scores) ** 0.5
            if den_h > 0 and den_a > 0:
                corr = num / (den_h * den_a)

        return CorrelationReport(
            domain=domain,
            sample_count=n,
            mean_human=round(mean_h, 2),
            mean_auto=round(mean_a, 2),
            correlation=round(corr, 3),
            summary=f"n={n}, r={corr:.3f}",
        )
