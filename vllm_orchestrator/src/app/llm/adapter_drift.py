"""
llm/adapter_drift.py — Adapter drift detection.

Monitors whether a domain adapter degrades general capabilities
while improving domain-specific performance. Tracks:
- Generic reasoning regression
- Over-domain bias (adapter biasing unrelated outputs)
- Hallucination increase
- Style overfit
- Instruction-following degradation
- Creative collapse or excessive rigidity

Quantifies domain specialization gain vs general stability tradeoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class DriftDimension:
    """Single drift measurement dimension."""
    name: str
    base_score: float = 0.0
    adapter_score: float = 0.0
    delta: float = 0.0
    threshold: float = -0.15       # degradation > 15% triggers warning
    warning: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def compute(self) -> None:
        self.delta = self.adapter_score - self.base_score
        self.warning = self.delta < self.threshold


@dataclass
class DriftReport:
    """Complete drift analysis for an adapter."""
    adapter_id: str
    domain: str
    dimensions: list[DriftDimension] = field(default_factory=list)
    domain_gain: float = 0.0           # positive = adapter helped domain tasks
    general_loss: float = 0.0          # negative = adapter hurt general tasks
    net_tradeoff: float = 0.0
    acceptable: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "domain": self.domain,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "domain_gain": self.domain_gain,
            "general_loss": self.general_loss,
            "net_tradeoff": self.net_tradeoff,
            "acceptable": self.acceptable,
            "warnings": self.warnings,
        }


# ── Drift check dimensions ──

_DRIFT_DIMENSIONS = [
    "generic_reasoning",
    "instruction_following",
    "hallucination_rate",
    "over_domain_bias",
    "style_overfit",
    "creative_range",
]


class AdapterDriftChecker:
    """Compares adapter-on vs adapter-off to detect drift."""

    def __init__(self, degradation_threshold: float = -0.15):
        self._threshold = degradation_threshold

    def analyze(
        self,
        adapter_id: str,
        domain: str,
        base_scores: dict[str, float],
        adapter_scores: dict[str, float],
        domain_benchmark_gain: float = 0.0,
    ) -> DriftReport:
        """Analyze drift between base and adapter outputs.

        Args:
            adapter_id: Adapter being tested
            domain: Domain the adapter specializes
            base_scores: Scores from base model (dimension → 0-1)
            adapter_scores: Scores from adapter model (dimension → 0-1)
            domain_benchmark_gain: Improvement on domain benchmarks (0-1)
        """
        dimensions = []
        warnings = []
        total_loss = 0.0

        for dim_name in _DRIFT_DIMENSIONS:
            base = base_scores.get(dim_name, 0.5)
            adapter = adapter_scores.get(dim_name, 0.5)
            dd = DriftDimension(
                name=dim_name,
                base_score=base,
                adapter_score=adapter,
                threshold=self._threshold,
            )
            dd.compute()
            dimensions.append(dd)

            if dd.warning:
                warnings.append(f"{dim_name}: degraded by {abs(dd.delta):.2f}")
                total_loss += abs(dd.delta)

        net = domain_benchmark_gain - total_loss
        acceptable = total_loss < 0.3 and len(warnings) <= 2

        return DriftReport(
            adapter_id=adapter_id,
            domain=domain,
            dimensions=dimensions,
            domain_gain=round(domain_benchmark_gain, 3),
            general_loss=round(-total_loss, 3),
            net_tradeoff=round(net, 3),
            acceptable=acceptable,
            warnings=warnings,
        )
