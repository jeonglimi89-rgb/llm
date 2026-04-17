"""
llm/prompt_budget.py — Prompt/Context budget management.

Manages prompt packing by splitting the context budget into:
- system_budget: base system prompt + domain reasoning template
- context_budget: conversation history, prior outputs, templates
- user_budget: current user input
- tool_budget: tool results, heuristic packs, schema definitions

Ensures total stays within max_context. Trims low-value context
when overflow is detected.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class BudgetAllocation:
    """Token budget allocation across prompt sections."""
    system_tokens: int = 0
    context_tokens: int = 0
    user_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    max_tokens: int = 32768
    overflow: bool = False
    trimmed_sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.total_tokens)

    @property
    def utilization(self) -> float:
        return self.total_tokens / self.max_tokens if self.max_tokens > 0 else 0


# Approximate tokens per character (Qwen2.5 tokenizer ≈ 1.5 chars/token for Korean)
_CHARS_PER_TOKEN = 1.5


def _estimate_tokens(text: str) -> int:
    """Rough token estimate. Accurate counting requires the actual tokenizer."""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


@dataclass
class PromptSection:
    """A section of the prompt with priority for trimming."""
    name: str
    content: str
    priority: int = 5          # 0=highest (never trim), 10=lowest (trim first)
    category: str = "context"  # "system"|"context"|"user"|"tool"
    tokens: int = 0
    trimmed: bool = False

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = _estimate_tokens(self.content)


class PromptBudgetManager:
    """Manages prompt packing within context budget."""

    def __init__(self, max_context: int = 32768, output_reserve: int = 512):
        self._max_context = max_context
        self._output_reserve = output_reserve

    @property
    def available_input_tokens(self) -> int:
        return self._max_context - self._output_reserve

    def pack(
        self,
        sections: list[PromptSection],
        *,
        compact_mode: bool = False,
    ) -> tuple[list[PromptSection], BudgetAllocation]:
        """Pack prompt sections within budget.

        Args:
            sections: Prompt sections with priority
            compact_mode: If True, aggressively trim low-priority sections

        Returns:
            (packed_sections, allocation)
        """
        budget = self.available_input_tokens
        if compact_mode:
            budget = int(budget * 0.7)  # 30% reduction for compact mode

        # Sort by priority (lower = keep)
        sorted_sections = sorted(sections, key=lambda s: s.priority)

        total = sum(s.tokens for s in sorted_sections)
        trimmed_names = []

        if total <= budget:
            # Everything fits
            allocation = self._compute_allocation(sorted_sections, self._max_context)
            return sorted_sections, allocation

        # Need to trim: remove lowest-priority sections first
        packed = []
        running = 0
        for s in sorted_sections:
            if running + s.tokens <= budget:
                packed.append(s)
                running += s.tokens
            elif s.priority <= 2:
                # Critical section: keep but truncate
                remaining = budget - running
                if remaining > 100:
                    truncated = PromptSection(
                        name=s.name,
                        content=s.content[:int(remaining * _CHARS_PER_TOKEN)],
                        priority=s.priority,
                        category=s.category,
                        tokens=remaining,
                    )
                    packed.append(truncated)
                    running += remaining
                    trimmed_names.append(f"{s.name}(truncated)")
            else:
                # Non-critical: drop entirely
                s.trimmed = True
                trimmed_names.append(s.name)

        allocation = self._compute_allocation(packed, self._max_context)
        allocation.trimmed_sections = trimmed_names
        allocation.overflow = total > self.available_input_tokens
        return packed, allocation

    def _compute_allocation(
        self, sections: list[PromptSection], max_tokens: int,
    ) -> BudgetAllocation:
        system = sum(s.tokens for s in sections if s.category == "system")
        context = sum(s.tokens for s in sections if s.category == "context")
        user = sum(s.tokens for s in sections if s.category == "user")
        tool = sum(s.tokens for s in sections if s.category == "tool")
        total = system + context + user + tool
        return BudgetAllocation(
            system_tokens=system,
            context_tokens=context,
            user_tokens=user,
            tool_tokens=tool,
            total_tokens=total,
            max_tokens=max_tokens,
        )
