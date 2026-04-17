"""
llm/runtime_hardening.py — Runtime stability and failure classification.

Classifies LLM-layer failures into specific types for proper handling.
No silent fallback: every degraded-mode entry is explicitly logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum


class LLMFailureClass(str, Enum):
    NONE = "none"
    ADAPTER_NOT_FOUND = "adapter_not_found"
    ADAPTER_ATTACH_FAILED = "adapter_attach_failed"
    MODEL_ENDPOINT_UNAVAILABLE = "model_endpoint_unavailable"
    CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"
    ROUTING_POLICY_ERROR = "routing_policy_error"
    MODEL_RESPONSE_INCOMPATIBLE = "model_response_incompatible"
    DEGRADED_MODE_FALLBACK = "degraded_mode_fallback"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class RuntimeEvent:
    """A runtime stability event for telemetry."""
    event_type: str                     # LLMFailureClass value
    severity: str = "info"              # "info"|"warning"|"error"|"critical"
    message: str = ""
    endpoint_id: str = ""
    adapter_id: str = ""
    model_tier: str = ""
    latency_ms: int = 0
    retryable: bool = False
    degraded: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_RETRY_POLICIES: dict[str, dict] = {
    LLMFailureClass.TIMEOUT.value: {"max_retries": 1, "backoff_ms": 2000},
    LLMFailureClass.MODEL_ENDPOINT_UNAVAILABLE.value: {"max_retries": 2, "backoff_ms": 5000},
    LLMFailureClass.ADAPTER_ATTACH_FAILED.value: {"max_retries": 0, "backoff_ms": 0, "fallback": "base_model"},
    LLMFailureClass.CONTEXT_LIMIT_EXCEEDED.value: {"max_retries": 0, "backoff_ms": 0, "action": "trim_or_fail"},
    LLMFailureClass.ROUTING_POLICY_ERROR.value: {"max_retries": 0, "backoff_ms": 0, "action": "fail_loud"},
    LLMFailureClass.MODEL_RESPONSE_INCOMPATIBLE.value: {"max_retries": 1, "backoff_ms": 1000},
}


class RuntimeHardening:
    """Classifies and handles LLM runtime failures."""

    def classify_failure(
        self,
        error: Exception,
        *,
        endpoint_id: str = "",
        adapter_id: str = "",
        model_tier: str = "",
    ) -> RuntimeEvent:
        """Classify an exception into a failure type."""
        msg = str(error).lower()

        if "timeout" in msg or "timed out" in msg:
            fc = LLMFailureClass.TIMEOUT
            retryable = True
        elif "connection" in msg or "refused" in msg or "unavailable" in msg:
            fc = LLMFailureClass.MODEL_ENDPOINT_UNAVAILABLE
            retryable = True
        elif "adapter" in msg and "not found" in msg:
            fc = LLMFailureClass.ADAPTER_NOT_FOUND
            retryable = False
        elif "adapter" in msg and ("failed" in msg or "error" in msg):
            fc = LLMFailureClass.ADAPTER_ATTACH_FAILED
            retryable = False
        elif "context" in msg or "too long" in msg or "max_length" in msg:
            fc = LLMFailureClass.CONTEXT_LIMIT_EXCEEDED
            retryable = False
        elif "routing" in msg or "policy" in msg:
            fc = LLMFailureClass.ROUTING_POLICY_ERROR
            retryable = False
        elif "incompatible" in msg or "schema" in msg:
            fc = LLMFailureClass.MODEL_RESPONSE_INCOMPATIBLE
            retryable = True
        else:
            fc = LLMFailureClass.UNKNOWN
            retryable = False

        return RuntimeEvent(
            event_type=fc.value,
            severity="error",
            message=str(error),
            endpoint_id=endpoint_id,
            adapter_id=adapter_id,
            model_tier=model_tier,
            retryable=retryable,
        )

    def get_retry_policy(self, failure_class: str) -> dict:
        """Get retry policy for a failure class."""
        return _RETRY_POLICIES.get(failure_class, {"max_retries": 0})

    def should_retry(self, event: RuntimeEvent) -> bool:
        """Determine if a failure should be retried."""
        policy = self.get_retry_policy(event.event_type)
        return event.retryable and policy.get("max_retries", 0) > 0

    def create_degraded_event(
        self,
        original_tier: str,
        fallback_tier: str,
        reason: str,
    ) -> RuntimeEvent:
        """Create an explicit degraded-mode event. No silent fallback."""
        return RuntimeEvent(
            event_type=LLMFailureClass.DEGRADED_MODE_FALLBACK.value,
            severity="warning",
            message=f"Degraded: {original_tier} -> {fallback_tier}. Reason: {reason}",
            model_tier=fallback_tier,
            degraded=True,
        )
