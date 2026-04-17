"""
training/data_collector.py — LoRA training data collection pipeline.

Collects high-quality input/output pairs from production pipeline runs
for domain-specific LoRA adapter training.

Data format per domain:
- system prompt (domain reasoning template + heuristic context)
- user input (structured request)
- assistant output (validated pipeline output)
- quality metadata (evaluation scores, benchmark results)

Only passes quality gate data is collected (no failed outputs).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class TrainingSample:
    """Single training sample for LoRA fine-tuning."""
    sample_id: str
    domain: str
    task_family: str
    system_prompt: str
    user_input: str
    assistant_output: str               # validated JSON output as string
    # Quality metadata
    evaluation_score: float = 0.0
    benchmark_score: float = 0.0
    passed_all_gates: bool = False
    adapter_target: str = ""            # which adapter this trains
    # Context
    creative_profile: Optional[dict] = None
    constraints: list[str] = field(default_factory=list)
    timestamp: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_chat_format(self) -> list[dict]:
        """Convert to chat format for training."""
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_input},
            {"role": "assistant", "content": self.assistant_output},
        ]

    def to_jsonl_line(self) -> str:
        """Convert to JSONL format for training datasets."""
        return json.dumps({
            "messages": self.to_chat_format(),
            "metadata": {
                "domain": self.domain,
                "task_family": self.task_family,
                "quality_score": self.evaluation_score,
                "adapter": self.adapter_target,
            },
        }, ensure_ascii=False)


@dataclass
class CollectionStats:
    """Statistics for a data collection run."""
    domain: str
    total_processed: int = 0
    quality_passed: int = 0
    quality_failed: int = 0
    samples_written: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# Quality gates for training data
_QUALITY_THRESHOLDS: dict[str, float] = {
    "minecraft": 0.6,
    "builder": 0.7,
    "animation": 0.65,
    "cad": 0.7,
    "product_design": 0.7,
}

# Adapter target mapping
_ADAPTER_TARGETS: dict[str, str] = {
    "minecraft": "minecraft_style_adapter",
    "builder": "builder_rules_adapter",
    "animation": "animation_direction_adapter",
    "cad": "cad_constraints_adapter",
    "product_design": "cad_constraints_adapter",
}


class TrainingDataCollector:
    """Collects and validates training data from pipeline runs."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        quality_thresholds: Optional[dict[str, float]] = None,
    ):
        if output_dir:
            self._output_dir = output_dir
        else:
            try:
                from ..storage.paths import training_data_dir
                self._output_dir = training_data_dir()
            except Exception:
                self._output_dir = Path("./training_data")
        self._thresholds = quality_thresholds or dict(_QUALITY_THRESHOLDS)
        self._stats: dict[str, CollectionStats] = {}

    def collect(
        self,
        domain: str,
        task_family: str,
        system_prompt: str,
        user_input: str,
        output_slots: dict[str, Any],
        *,
        evaluation_score: float = 0.0,
        benchmark_score: float = 0.0,
        creative_profile: Optional[dict] = None,
        constraints: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> Optional[TrainingSample]:
        """Collect a training sample if it passes quality gates.

        Returns the sample if collected, None if rejected.
        """
        # Initialize stats
        if domain not in self._stats:
            self._stats[domain] = CollectionStats(domain=domain)
        stats = self._stats[domain]
        stats.total_processed += 1

        # Quality gate
        threshold = self._thresholds.get(domain, 0.6)
        if evaluation_score < threshold:
            stats.quality_failed += 1
            return None
        stats.quality_passed += 1

        # Build sample
        sample = TrainingSample(
            sample_id=f"{domain}_{int(time.time())}_{stats.samples_written}",
            domain=domain,
            task_family=task_family,
            system_prompt=system_prompt,
            user_input=user_input,
            assistant_output=json.dumps(output_slots, ensure_ascii=False),
            evaluation_score=evaluation_score,
            benchmark_score=benchmark_score,
            passed_all_gates=True,
            adapter_target=_ADAPTER_TARGETS.get(domain, ""),
            creative_profile=creative_profile,
            constraints=constraints or [],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            tags=tags or [],
        )

        stats.samples_written += 1
        return sample

    def write_sample(self, sample: TrainingSample) -> Path:
        """Write a single sample to the domain-specific JSONL file."""
        domain_dir = self._output_dir / sample.domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        filepath = domain_dir / f"{sample.adapter_target}.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(sample.to_jsonl_line() + "\n")
        return filepath

    def get_stats(self, domain: str = "") -> dict:
        if domain:
            s = self._stats.get(domain)
            return s.to_dict() if s else {}
        return {d: s.to_dict() for d, s in self._stats.items()}

    def get_dataset_info(self, domain: str) -> dict:
        """Get info about the collected dataset for a domain."""
        domain_dir = self._output_dir / domain
        if not domain_dir.exists():
            return {"domain": domain, "files": [], "total_samples": 0}

        files = []
        total = 0
        for f in domain_dir.glob("*.jsonl"):
            line_count = sum(1 for _ in open(f, encoding="utf-8"))
            files.append({"file": f.name, "samples": line_count})
            total += line_count

        return {"domain": domain, "files": files, "total_samples": total}
