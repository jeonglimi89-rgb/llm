"""
llm/adapter_policy.py — Adapter Activation Policy Engine.

Determines when and how to attach domain PEFT adapters based on:
- task_family, app_domain, creativity_tier, pipeline_phase
- base model compatibility
- multi-adapter conflict prevention
- fallback policy on attach failure

All decisions are logged to telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum

from .adapter_registry import AdapterRegistry, AdapterSpec, AdapterStatus
from .model_router import CORE_TEXT_32B, CORE_CODE_32B


class PipelinePhase(str, Enum):
    GENERATION = "generation"
    REVIEW = "review"
    REPAIR = "repair"
    CREATIVE_VARIANT = "creative_variant"
    VALIDATION = "validation"


@dataclass
class AdapterDecision:
    """Result of adapter activation policy evaluation."""
    should_attach: bool = False
    adapter_id: str = ""
    reason: str = ""
    fallback_to_base: bool = False
    phase: str = ""
    domain: str = ""
    blocked_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Per-domain adapter policy ──

@dataclass
class DomainAdapterPolicy:
    """Adapter attach rules for a domain."""
    domain: str
    adapter_id: str
    # Which phases allow adapter attachment
    attach_on_generation: bool = True
    attach_on_review: bool = True
    attach_on_repair: bool = True
    attach_on_creative: bool = True
    attach_on_validation: bool = False
    # Code tasks: never attach domain adapter by default
    attach_on_code_task: bool = False
    # Priority (lower = higher priority)
    priority: int = 0

    def allows_phase(self, phase: str) -> bool:
        return {
            PipelinePhase.GENERATION.value: self.attach_on_generation,
            PipelinePhase.REVIEW.value: self.attach_on_review,
            PipelinePhase.REPAIR.value: self.attach_on_repair,
            PipelinePhase.CREATIVE_VARIANT.value: self.attach_on_creative,
            PipelinePhase.VALIDATION.value: self.attach_on_validation,
        }.get(phase, False)


_DOMAIN_POLICIES: dict[str, DomainAdapterPolicy] = {
    "builder": DomainAdapterPolicy(
        domain="builder",
        adapter_id="builder_rules_adapter",
        attach_on_generation=True,
        attach_on_review=True,      # review benefits from rule knowledge
        attach_on_repair=True,
        attach_on_creative=True,
        attach_on_validation=False,
        attach_on_code_task=False,
    ),
    "cad": DomainAdapterPolicy(
        domain="cad",
        adapter_id="cad_constraints_adapter",
        attach_on_generation=True,
        attach_on_review=True,      # constraint reasoning during review
        attach_on_repair=True,
        attach_on_creative=False,   # creative variants use base model only
        attach_on_validation=False,
        attach_on_code_task=False,
    ),
    "minecraft": DomainAdapterPolicy(
        domain="minecraft",
        adapter_id="minecraft_style_adapter",
        attach_on_generation=True,
        attach_on_review=True,
        attach_on_repair=True,
        attach_on_creative=True,    # creative variants benefit from style knowledge
        attach_on_validation=False,
        attach_on_code_task=False,
    ),
    "animation": DomainAdapterPolicy(
        domain="animation",
        adapter_id="animation_direction_adapter",
        attach_on_generation=True,
        attach_on_review=True,      # camera/style review benefits from direction knowledge
        attach_on_repair=True,
        attach_on_creative=True,
        attach_on_validation=False,
        attach_on_code_task=False,
    ),
    "product_design": DomainAdapterPolicy(
        domain="product_design",
        adapter_id="cad_constraints_adapter",
        attach_on_generation=True,
        attach_on_review=True,
        attach_on_repair=True,
        attach_on_creative=False,
        attach_on_validation=False,
        attach_on_code_task=False,
    ),
}


class AdapterActivationPolicy:
    """Evaluates whether to attach an adapter for a given request context."""

    def __init__(
        self,
        adapter_registry: AdapterRegistry,
        policies: Optional[dict[str, DomainAdapterPolicy]] = None,
    ):
        self._registry = adapter_registry
        self._policies = policies or dict(_DOMAIN_POLICIES)

    def evaluate(
        self,
        domain: str,
        phase: str,
        logical_model_id: str = CORE_TEXT_32B,
        *,
        is_code_task: bool = False,
        force_adapter: Optional[str] = None,
    ) -> AdapterDecision:
        """Evaluate adapter attachment for a request.

        Args:
            domain: App domain
            phase: Pipeline phase (generation/review/repair/creative_variant)
            logical_model_id: Target model logical ID
            is_code_task: Whether this is a code task
            force_adapter: Force specific adapter (overrides policy)
        """
        # 1. Code model doesn't get domain adapters
        if logical_model_id == CORE_CODE_32B and not is_code_task:
            pass  # allow domain adapter on text model for code-adjacent tasks
        if is_code_task:
            policy = self._policies.get(domain)
            if policy and not policy.attach_on_code_task:
                return AdapterDecision(
                    should_attach=False,
                    reason="code task: domain adapter not attached per policy",
                    domain=domain,
                    phase=phase,
                )

        # 2. Force adapter override
        if force_adapter:
            spec = self._registry.get(force_adapter)
            if spec and spec.base_model_id != logical_model_id:
                return AdapterDecision(
                    should_attach=False,
                    blocked_reason=f"adapter '{force_adapter}' incompatible with model '{logical_model_id}'",
                    domain=domain, phase=phase,
                )
            result = self._registry.attach(force_adapter)
            return AdapterDecision(
                should_attach=result.attached,
                adapter_id=force_adapter,
                reason=result.reason,
                fallback_to_base=result.fallback_to_base,
                domain=domain, phase=phase,
            )

        # 3. Look up domain policy
        policy = self._policies.get(domain)
        if not policy:
            return AdapterDecision(
                should_attach=False,
                reason=f"no adapter policy for domain '{domain}'",
                domain=domain, phase=phase,
            )

        # 4. Check phase permission
        if not policy.allows_phase(phase):
            return AdapterDecision(
                should_attach=False,
                reason=f"adapter not allowed in phase '{phase}' for domain '{domain}'",
                domain=domain, phase=phase,
            )

        # 5. Check adapter compatibility
        spec = self._registry.get(policy.adapter_id)
        if spec and spec.base_model_id != logical_model_id:
            return AdapterDecision(
                should_attach=False,
                blocked_reason=f"adapter base model mismatch: {spec.base_model_id} != {logical_model_id}",
                domain=domain, phase=phase,
            )

        # 6. Attempt attach
        result = self._registry.attach(policy.adapter_id)
        return AdapterDecision(
            should_attach=result.attached,
            adapter_id=policy.adapter_id,
            reason=result.reason,
            fallback_to_base=result.fallback_to_base,
            domain=domain, phase=phase,
        )

    def get_policy(self, domain: str) -> Optional[DomainAdapterPolicy]:
        return self._policies.get(domain)

    def list_policies(self) -> list[DomainAdapterPolicy]:
        return list(self._policies.values())
