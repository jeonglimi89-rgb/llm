"""
llm/routing_calibrator.py — Task-aware calibrated routing.

Upgrades static tier mapping to context-sensitive routing that considers:
- domain, task_family, pipeline_phase
- context length, code density, creativity/strictness demands
- adapter attach recommendation
- explainable routing reason

Output includes both model tier decision and adapter decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .model_router import ModelRouter, ModelRoutingDecision, ModelTier, CORE_TEXT_32B, CORE_CODE_32B
from .adapter_policy import AdapterActivationPolicy, AdapterDecision, PipelinePhase


@dataclass
class CalibratedRoutingResult:
    """Complete routing decision with model + adapter + reason."""
    model_decision: ModelRoutingDecision = field(default_factory=lambda: ModelRoutingDecision("", "", "", "", ""))
    adapter_decision: AdapterDecision = field(default_factory=AdapterDecision)
    calibration_factors: dict[str, Any] = field(default_factory=dict)
    explainable_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "model": self.model_decision.to_dict(),
            "adapter": self.adapter_decision.to_dict(),
            "calibration": self.calibration_factors,
            "reason": self.explainable_reason,
        }


class RoutingCalibrator:
    """Task-aware calibrated routing combining model tier + adapter decisions."""

    def __init__(
        self,
        model_router: ModelRouter,
        adapter_policy: AdapterActivationPolicy,
    ):
        self._model_router = model_router
        self._adapter_policy = adapter_policy

    def calibrate(
        self,
        task_type: str,
        pool_name: str,
        domain: str,
        phase: str = PipelinePhase.GENERATION.value,
        *,
        context_token_count: int = 0,
        code_density: float = 0.0,
        creativity_demand: float = 0.5,
        strictness_demand: float = 0.5,
        is_code_task: bool = False,
    ) -> CalibratedRoutingResult:
        """Produce a calibrated routing decision.

        Args:
            task_type: Full task type
            pool_name: Pool from task router
            domain: App domain
            phase: Pipeline phase
            context_token_count: Estimated input token count
            code_density: Fraction of code in the context (0-1)
            creativity_demand: How creative the output should be (0-1)
            strictness_demand: How strict validation should be (0-1)
            is_code_task: Explicit code task flag
        """
        # 1. Determine if long context
        is_long = context_token_count > 16384

        # 2. Determine if variant generation
        is_variant = phase == PipelinePhase.CREATIVE_VARIANT.value

        # 3. Code density override
        if code_density > 0.5 and not is_code_task:
            is_code_task = True

        # 4. Model routing
        model_decision = self._model_router.route(
            task_type=task_type,
            pool_name=pool_name,
            domain=domain,
            is_variant=is_variant,
            is_long_context=is_long,
            is_code_task=is_code_task,
        )

        # 5. Adapter decision
        adapter_decision = self._adapter_policy.evaluate(
            domain=domain,
            phase=phase,
            logical_model_id=model_decision.logical_id,
            is_code_task=is_code_task,
        )

        # 6. Build calibration factors
        factors = {
            "context_length": "long" if is_long else "standard",
            "code_density": round(code_density, 2),
            "creativity_demand": round(creativity_demand, 2),
            "strictness_demand": round(strictness_demand, 2),
            "phase": phase,
            "is_code_task": is_code_task,
        }

        # 7. Build explainable reason
        parts = [f"domain={domain}", f"phase={phase}", f"tier={model_decision.tier}"]
        if adapter_decision.should_attach:
            parts.append(f"adapter={adapter_decision.adapter_id}")
        elif adapter_decision.fallback_to_base:
            parts.append("adapter=fallback_to_base")
        if is_long:
            parts.append("long_context")
        if is_code_task:
            parts.append("code_task")

        return CalibratedRoutingResult(
            model_decision=model_decision,
            adapter_decision=adapter_decision,
            calibration_factors=factors,
            explainable_reason=", ".join(parts),
        )
