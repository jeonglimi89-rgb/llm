"""
domain/output_policy.py — Output type classification and policy enforcement.

The orchestrator's allowed output types are strictly limited to:
1. executable_command_graph — single validated output
2. executable_command_graph_with_variants — multi-variant validated output
3. clarification_required — input is ambiguous, needs user clarification
4. fail_loud_with_reasons — pipeline failed with explicit reasons

Banned outputs:
- Generic descriptive text without structured commands
- Unverified creative idea lists
- Improvised steps not in the task registry
- Creative variants that failed professional floor validation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..core.enums import OutputType


# Banned patterns in structured output
_BANNED_PATTERNS = [
    re.compile(r"일반적으로|보통은|통상적으로", re.IGNORECASE),
    re.compile(r"(?:try|consider|maybe|perhaps)\b", re.IGNORECASE),
    re.compile(r"추천합니다|권장합니다|제안합니다", re.IGNORECASE),
]

# Generic filler that indicates non-actionable output
_GENERIC_FILLER = [
    re.compile(r"(?:as\s+(?:a|an)\s+(?:general|common)\s+(?:rule|practice))", re.IGNORECASE),
    re.compile(r"다양한\s+방법이\s+있", re.IGNORECASE),
    re.compile(r"여러\s+가지\s+(?:방법|옵션|선택)", re.IGNORECASE),
]


@dataclass
class OutputPolicyResult:
    """Result of output type classification and policy validation."""

    output_type: str = OutputType.EXECUTABLE_COMMAND_GRAPH.value
    violations: list[str] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class OutputPolicyEnforcer:
    """Classifies output type and checks for banned patterns."""

    def classify_and_validate(
        self,
        slots: Optional[dict[str, Any]],
        variant_plan: Optional[Any] = None,
        fail_loud: bool = False,
        fail_loud_reason: str = "",
    ) -> OutputPolicyResult:
        """Classify the output type and check for policy violations.

        Args:
            slots: The structured output from the pipeline
            variant_plan: Optional VariantPlan (if multi-variant)
            fail_loud: Whether the pipeline already decided to fail-loud
            fail_loud_reason: The reason for fail-loud

        Returns:
            OutputPolicyResult with type classification and any violations
        """
        # Fail-loud overrides everything
        if fail_loud:
            return OutputPolicyResult(
                output_type=OutputType.FAIL_LOUD_WITH_REASONS.value,
                violations=[fail_loud_reason] if fail_loud_reason else [],
                passed=True,  # fail-loud is a valid output type
            )

        # No output = clarification required
        if slots is None:
            return OutputPolicyResult(
                output_type=OutputType.CLARIFICATION_REQUIRED.value,
                violations=["null output — input may need clarification"],
                passed=False,
            )

        # Determine output type
        has_variants = (
            variant_plan is not None
            and hasattr(variant_plan, "variants")
            and len(variant_plan.variants) > 1
        )
        out_type = (
            OutputType.EXECUTABLE_COMMAND_GRAPH_WITH_VARIANTS.value
            if has_variants
            else OutputType.EXECUTABLE_COMMAND_GRAPH.value
        )

        # Check for banned patterns in output
        violations = self._check_banned_patterns(slots)

        # Check for unverified creative variants
        if has_variants:
            for v in variant_plan.variants:
                if not v.accepted and v.family != "safe_baseline":
                    violations.append(
                        f"rejected variant '{v.family}' must not appear in final output"
                    )

        return OutputPolicyResult(
            output_type=out_type,
            violations=violations,
            passed=len(violations) == 0,
        )

    def _check_banned_patterns(self, slots: dict) -> list[str]:
        """Check for banned patterns in structured output."""
        flat = _flatten_to_string(slots)
        violations = []

        for pattern in _BANNED_PATTERNS:
            match = pattern.search(flat)
            if match:
                violations.append(f"banned_pattern: '{match.group()}'")

        for pattern in _GENERIC_FILLER:
            match = pattern.search(flat)
            if match:
                violations.append(f"generic_filler: '{match.group()}'")

        return violations


def _flatten_to_string(d: Any) -> str:
    """Recursively flatten dict/list to a single string for pattern matching."""
    if isinstance(d, dict):
        parts = []
        for k, v in d.items():
            parts.append(str(k))
            parts.append(_flatten_to_string(v))
        return " ".join(parts)
    elif isinstance(d, list):
        return " ".join(_flatten_to_string(item) for item in d)
    else:
        return str(d) if d is not None else ""
