"""
orchestration/llm_variant_generator.py — LLM-backed variant generation.

Generates creative variants by re-invoking the LLM with strategy-specific
prompt perturbations. Each variant gets a different system prompt suffix
that guides the LLM to explore a specific creative direction.

This replaces the shallow_perturb fallback with actual LLM calls.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Optional

from ..core.contracts import TaskRequest, TaskResult
from ..domain.registry import TaskSpec
from ..orchestration.variant_planner import _STRATEGY_PROMPTS
from ..observability.logger import get_logger

_log = get_logger("variant_gen")


class LLMVariantGenerator:
    """Generates variant slots by re-dispatching to the LLM with perturbation prompts."""

    def __init__(self, dispatcher, router):
        """
        Args:
            dispatcher: Dispatcher instance for LLM calls
            router: Router instance for resolving task specs
        """
        self._dispatcher = dispatcher
        self._router = router

    def generate_variant(
        self,
        base_slots: dict[str, Any],
        strategy: str,
        prompt_suffix: str,
        *,
        domain: str = "",
        task_name: str = "",
        user_input: str = "",
        context: Optional[dict] = None,
        profile_reasoning: str = "",
    ) -> Optional[dict[str, Any]]:
        """Generate a single variant via LLM re-dispatch.

        Args:
            base_slots: Baseline output to perturb from
            strategy: Strategy name (e.g., "style_shifted", "engineering_alternative")
            prompt_suffix: Strategy-specific prompt to append
            domain: Target domain
            task_name: Task to re-invoke
            user_input: Original user input
            context: Request context
            profile_reasoning: Domain reasoning template

        Returns:
            Variant slots dict, or None on failure
        """
        if not prompt_suffix:
            return None

        # Build the variant generation prompt
        variant_prompt = self._build_variant_prompt(
            base_slots, strategy, prompt_suffix, profile_reasoning,
        )

        # Create a task request for the variant
        request = TaskRequest(
            domain=domain,
            task_name=task_name,
            user_input=user_input,
            context=context or {},
            metadata={"variant_strategy": strategy, "orchestrated": True},
        )

        try:
            spec = self._router.resolve(request)
            result = self._dispatcher.dispatch(
                request, spec,
                system_prompt_override=variant_prompt,
            )
            if result.slots:
                return result.slots
        except Exception as e:
            _log.warning(f"Variant generation failed for strategy={strategy}: {e}")

        return None

    def create_generator_fn(
        self,
        domain: str,
        task_name: str,
        user_input: str,
        context: Optional[dict] = None,
        profile_reasoning: str = "",
    ):
        """Create a callable suitable for VariantPlanner.plan_variants(variant_generator_fn=...).

        Returns a function with signature: (base_slots, strategy, prompt_suffix) -> Optional[dict]
        """
        def generator_fn(
            base_slots: dict,
            strategy: str,
            prompt_suffix: str,
        ) -> Optional[dict]:
            return self.generate_variant(
                base_slots=base_slots,
                strategy=strategy,
                prompt_suffix=prompt_suffix,
                domain=domain,
                task_name=task_name,
                user_input=user_input,
                context=context,
                profile_reasoning=profile_reasoning,
            )

        return generator_fn

    def _build_variant_prompt(
        self,
        base_slots: dict,
        strategy: str,
        prompt_suffix: str,
        profile_reasoning: str,
    ) -> str:
        """Build the system prompt for variant generation."""
        parts = []

        if profile_reasoning:
            parts.append(profile_reasoning)

        parts.append(
            "You are generating a VARIANT of an existing design. "
            "The baseline output is provided below. "
            "Your task is to produce a meaningfully different alternative "
            "that maintains all hard constraints but explores the specified creative direction."
        )

        parts.append(f"\n--- BASELINE OUTPUT ---\n{json.dumps(base_slots, ensure_ascii=False, indent=2)}\n---")

        parts.append(prompt_suffix)

        parts.append(
            "\nOutput ONLY valid JSON matching the same schema as the baseline. "
            "No explanatory text."
        )

        return "\n\n".join(parts)
