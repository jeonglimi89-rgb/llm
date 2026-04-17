"""
training/adapter_trainer.py — LoRA adapter training configuration.

Defines training configurations for each domain adapter.
Actual training runs on the vLLM/HuggingFace PEFT stack.
This module provides the config and launch helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class LoRATrainingConfig:
    """Configuration for a single LoRA adapter training run."""
    adapter_id: str
    domain: str
    base_model: str = "Qwen/Qwen2.5-32B-Instruct"
    # LoRA hyperparameters
    rank: int = 16
    alpha: float = 32.0
    dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj"])
    # Training parameters
    learning_rate: float = 2e-4
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    num_epochs: int = 3
    warmup_ratio: float = 0.1
    max_seq_length: int = 4096
    # Data
    train_data_path: str = ""
    eval_data_path: str = ""
    min_samples: int = 100             # minimum samples before training is allowed
    # Output
    output_dir: str = ""
    # Quality gate
    eval_threshold: float = 0.0        # minimum eval score to accept adapter

    def to_dict(self) -> dict:
        return asdict(self)

    def to_peft_config(self) -> dict:
        """Generate PEFT LoraConfig compatible dict."""
        return {
            "r": self.rank,
            "lora_alpha": self.alpha,
            "lora_dropout": self.dropout,
            "target_modules": self.target_modules,
            "task_type": "CAUSAL_LM",
            "bias": "none",
        }

    def to_training_args(self) -> dict:
        """Generate HuggingFace TrainingArguments compatible dict."""
        return {
            "output_dir": self.output_dir,
            "num_train_epochs": self.num_epochs,
            "per_device_train_batch_size": self.batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "learning_rate": self.learning_rate,
            "warmup_ratio": self.warmup_ratio,
            "logging_steps": 10,
            "save_strategy": "epoch",
            "evaluation_strategy": "epoch",
            "fp16": True,
            "dataloader_num_workers": 0,
        }


# ── Domain training configs ──

_TRAINING_CONFIGS: dict[str, LoRATrainingConfig] = {
    "builder_rules_adapter": LoRATrainingConfig(
        adapter_id="builder_rules_adapter",
        domain="builder",
        rank=16, alpha=32.0,
        learning_rate=2e-4,
        num_epochs=3,
        max_seq_length=4096,
        min_samples=200,
        eval_threshold=0.7,
    ),
    "cad_constraints_adapter": LoRATrainingConfig(
        adapter_id="cad_constraints_adapter",
        domain="cad",
        rank=16, alpha=32.0,
        learning_rate=1.5e-4,
        num_epochs=4,
        max_seq_length=4096,
        min_samples=200,
        eval_threshold=0.7,
    ),
    "minecraft_style_adapter": LoRATrainingConfig(
        adapter_id="minecraft_style_adapter",
        domain="minecraft",
        rank=32, alpha=64.0,       # higher rank for creative domain
        learning_rate=2e-4,
        num_epochs=3,
        max_seq_length=4096,
        min_samples=150,
        eval_threshold=0.6,
    ),
    "animation_direction_adapter": LoRATrainingConfig(
        adapter_id="animation_direction_adapter",
        domain="animation",
        rank=16, alpha=32.0,
        learning_rate=2e-4,
        num_epochs=3,
        max_seq_length=4096,
        min_samples=150,
        eval_threshold=0.65,
    ),
}


def get_training_config(adapter_id: str) -> Optional[LoRATrainingConfig]:
    return _TRAINING_CONFIGS.get(adapter_id)


def list_training_configs() -> list[LoRATrainingConfig]:
    return list(_TRAINING_CONFIGS.values())


class AdapterTrainingManager:
    """Manages LoRA adapter training lifecycle."""

    def __init__(self, data_dir: Optional[Path] = None, output_dir: Optional[Path] = None):
        try:
            from ..storage.paths import training_data_dir as _tdd, adapters_dir as _add
            self._data_dir = data_dir or _tdd()
            self._output_dir = output_dir or _add()
        except Exception:
            self._data_dir = data_dir or Path("./training_data")
            self._output_dir = output_dir or Path("./adapters")

    def check_readiness(self, adapter_id: str) -> dict:
        """Check if an adapter has enough training data."""
        config = get_training_config(adapter_id)
        if not config:
            return {"ready": False, "reason": f"No config for {adapter_id}"}

        data_path = self._data_dir / config.domain / f"{adapter_id}.jsonl"
        if not data_path.exists():
            return {"ready": False, "reason": "No training data file", "samples": 0, "required": config.min_samples}

        sample_count = sum(1 for _ in open(data_path, encoding="utf-8"))
        ready = sample_count >= config.min_samples

        return {
            "ready": ready,
            "adapter_id": adapter_id,
            "domain": config.domain,
            "samples": sample_count,
            "required": config.min_samples,
            "reason": "sufficient data" if ready else f"need {config.min_samples - sample_count} more samples",
        }

    def generate_training_script(self, adapter_id: str) -> Optional[str]:
        """Generate a training launch script for an adapter."""
        config = get_training_config(adapter_id)
        if not config:
            return None

        data_path = self._data_dir / config.domain / f"{adapter_id}.jsonl"
        output_path = self._output_dir / adapter_id

        config.train_data_path = str(data_path)
        config.output_dir = str(output_path)

        return f"""#!/bin/bash
# LoRA Training Script for {adapter_id}
# Domain: {config.domain}
# Base model: {config.base_model}

python -m peft.train \\
  --base_model "{config.base_model}" \\
  --train_data "{config.train_data_path}" \\
  --output_dir "{config.output_dir}" \\
  --lora_r {config.rank} \\
  --lora_alpha {config.alpha} \\
  --lora_dropout {config.dropout} \\
  --target_modules {' '.join(config.target_modules)} \\
  --learning_rate {config.learning_rate} \\
  --batch_size {config.batch_size} \\
  --gradient_accumulation {config.gradient_accumulation_steps} \\
  --epochs {config.num_epochs} \\
  --max_seq_length {config.max_seq_length} \\
  --warmup_ratio {config.warmup_ratio} \\
  --fp16

echo "Training complete. Adapter saved to {config.output_dir}"
"""
