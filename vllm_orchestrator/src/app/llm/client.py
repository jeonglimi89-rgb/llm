"""
client.py - LLM 상위 인터페이스

adapter를 감싸서 retry, health 추적, response parsing을 통합.

NOTE 2026-04-07 (T-tranche):
    - retry 는 ``max_retries`` count 만이 아니라 **total deadline budget** 에도
      구속된다. 매 attempt 직전에 남은 budget 을 확인해서, 다음 attempt 의
      timeout 보다 budget 이 작으면 budget exhausted 로 즉시 fail-loud.
    - 매 호출 결과는 ``self.last_retry_decision`` 에 ``RetryDecision`` 으로
      저장된다. dispatcher / telemetry 가 이 객체를 읽어 artifact 에 노출.
    - ``extract_slots`` 의 반환 시그니처는 그대로 ``(parsed, raw_text,
      latency_ms)`` 3-tuple 이다 (back-compat). 새 정보는 instance attribute.

NOTE 2026-04-08 (T-tranche 2):
    - transport-fail cooldown 의 magic number ``2.0`` 이 제거됐다.
      이제는 ``self.transport_retry_cooldown_s`` (생성자에서 설정 가능, default
      ``DEFAULT_TRANSPORT_RETRY_COOLDOWN_S``) 와 ``clamp_wait_to_budget`` 을
      통해 wall-clock budget 안쪽으로 clamp / skip 된다.
    - 모든 cooldown 결정은 ``RetryDecision.cooldown_decisions`` 에 ``WaitDecision``
      list 로 누적되고, ``total_cooldown_ms`` 가 합계로 노출된다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .response_parser import parse_llm_output
from .token_budget import get_output_budget, get_temperature, trim_prompt
from ..core.errors import LLMError, CircuitOpenError
from ..execution.circuit_breaker import CircuitBreaker
from ..execution.timeouts import (
    DEFAULT_TRANSPORT_RETRY_COOLDOWN_S,
    WaitDecision,
    clamp_wait_to_budget,
    WAIT_KIND_TRANSPORT_RETRY,
)
from ..observability.health_registry import HealthRegistry
from ..observability.logger import get_logger, log_event

_log = get_logger("llm")


# ---------------------------------------------------------------------------
# Retry decision — captured per extract_slots call
# ---------------------------------------------------------------------------

# Normalized retry decision reasons (lower_snake).
RETRY_REASON_INITIAL_SUCCESS                 = "initial_success"
RETRY_REASON_RETRY_SUCCESS                   = "retry_success"
RETRY_REASON_PARSE_RETRY_EXHAUSTED           = "parse_retry_exhausted"
RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED       = "transport_retry_exhausted"
RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_INITIAL = "budget_exhausted_before_initial"
RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_RETRY   = "budget_exhausted_before_retry"
RETRY_REASON_CIRCUIT_OPEN                    = "circuit_open"


@dataclass
class RetryDecision:
    """Per-call summary of how the retry budget was consumed.

    T-tranche 2 (2026-04-08): now also captures the cooldown decisions made
    inside the retry loop. ``cooldown_decisions`` is a list of ``WaitDecision``
    dicts (one per retry cooldown slot). ``total_cooldown_ms`` is the
    aggregate of those (only what was actually slept). Operators read this
    via ``TaskResult.retry_decision`` to answer "why was this case slow".
    """
    attempts_used: int = 0
    transport_failures: int = 0
    parse_failures: int = 0
    budget_exhausted: bool = False
    total_elapsed_ms: int = 0
    effective_request_timeout_s: float = 0.0
    total_deadline_s: Optional[float] = None
    retry_decision_reason: str = ""
    # T-tranche 2 additive
    transport_retry_cooldown_s: float = 0.0      # configured value carried from policy
    transport_retry_cooldown_source: str = "default"
    cooldown_decisions: list[dict] = field(default_factory=list)
    total_cooldown_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class LLMClient:
    """LLM adapter + circuit breaker + health + parsing 통합.

    Parameters
    ----------
    adapter      : an LLM adapter exposing ``.generate(messages, max_tokens, temperature, timeout_s) -> dict``
    health       : HealthRegistry instance
    circuit      : CircuitBreaker instance
    max_retries  : retry count budget. ``max_retries=0`` means a single attempt.
                   This is the *count* lever; the deadline lever is below.
    """

    def __init__(
        self,
        adapter,
        health: HealthRegistry,
        circuit: CircuitBreaker,
        max_retries: int = 1,
        *,
        transport_retry_cooldown_s: float = DEFAULT_TRANSPORT_RETRY_COOLDOWN_S,
        transport_retry_cooldown_source: str = "default",
    ):
        self.adapter = adapter
        self.health = health
        self.circuit = circuit
        self.max_retries = max(0, int(max_retries))
        # T-tranche 2: cooldown is a constructor field, not a function-local
        # magic number. ``run_export`` sets this from the unified policy.
        self.transport_retry_cooldown_s: float = max(0.0, float(transport_retry_cooldown_s))
        self.transport_retry_cooldown_source: str = transport_retry_cooldown_source
        self.health.register("llm")
        # Most-recent retry decision (set on every extract_slots call).
        self.last_retry_decision: Optional[RetryDecision] = None

    def extract_slots(
        self,
        system_prompt: str,
        user_input: str,
        pool_type: str = "strict_json",
        timeout_s: float = 120.0,
        *,
        total_deadline_s: Optional[float] = None,
        guided_json_schema: Optional[dict] = None,
    ) -> tuple[dict | None, str, int]:
        """슬롯 추출. 반환: (parsed_dict, raw_text, latency_ms).

        Parameters
        ----------
        timeout_s        : per-attempt request timeout in seconds (float allowed).
        total_deadline_s : optional wall-clock budget for ALL attempts combined.
                           If set, retries that cannot complete within the
                           remaining budget are skipped (budget exhausted).

        Side effect: ``self.last_retry_decision`` is overwritten with a
        ``RetryDecision`` describing this call.
        """
        timeout_s = float(timeout_s)
        rd = RetryDecision(
            attempts_used=0,
            transport_failures=0,
            parse_failures=0,
            budget_exhausted=False,
            total_elapsed_ms=0,
            effective_request_timeout_s=timeout_s,
            total_deadline_s=total_deadline_s,
            retry_decision_reason="",
            transport_retry_cooldown_s=self.transport_retry_cooldown_s,
            transport_retry_cooldown_source=self.transport_retry_cooldown_source,
            cooldown_decisions=[],
            total_cooldown_ms=0,
        )
        self.last_retry_decision = rd

        if not self.circuit.allow():
            rd.retry_decision_reason = RETRY_REASON_CIRCUIT_OPEN
            raise CircuitOpenError()

        system_prompt = trim_prompt(system_prompt, pool_type)
        max_tokens = get_output_budget(pool_type)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        call_started = time.time()
        last_raw_text = ""

        # Bound the total wall-clock budget by total_deadline_s if provided.
        # Each attempt is allowed to run up to ``timeout_s`` seconds; if the
        # *remaining* budget at the start of an attempt is below ``timeout_s``,
        # we refuse to start that attempt and exit with budget_exhausted.
        for attempt in range(1 + self.max_retries):
            # Pre-attempt deadline check (skipped on the very first attempt
            # only if budget is meaningfully positive — see below).
            if total_deadline_s is not None:
                elapsed = time.time() - call_started
                remaining = total_deadline_s - elapsed
                if attempt == 0:
                    # First attempt always runs as long as the deadline is at
                    # least a tiny positive number; we don't want a deadline of
                    # 0 to silently skip everything.
                    if remaining <= 0:
                        rd.budget_exhausted = True
                        rd.retry_decision_reason = RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_INITIAL
                        rd.total_elapsed_ms = int(elapsed * 1000)
                        log_event(
                            _log, "llm_budget_exhausted",
                            attempt=attempt, remaining_s=remaining,
                            timeout_s=timeout_s, total_deadline_s=total_deadline_s,
                        )
                        return None, "", rd.total_elapsed_ms
                else:
                    # Retry attempt: must have at least one full timeout window
                    # of headroom. Otherwise the retry is doomed and we just
                    # waste wall-clock.
                    if remaining < timeout_s:
                        rd.budget_exhausted = True
                        rd.retry_decision_reason = RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_RETRY
                        rd.total_elapsed_ms = int(elapsed * 1000)
                        log_event(
                            _log, "llm_budget_exhausted",
                            attempt=attempt, remaining_s=remaining,
                            timeout_s=timeout_s, total_deadline_s=total_deadline_s,
                        )
                        # Tell circuit / health about the failure.
                        self.circuit.record_failure()
                        self.health.record_failure("llm", "budget_exhausted")
                        return None, last_raw_text, rd.total_elapsed_ms

            attempt_start = time.time()
            rd.attempts_used = attempt + 1
            try:
                # Pass response_format="json_object" for strict_json/creative_json pools
                # Pass guided_json schema when available (vLLM structured decoding)
                extra_kwargs = {}
                if pool_type in ("strict_json", "creative_json"):
                    extra_kwargs["response_format"] = "json_object"
                if guided_json_schema is not None:
                    extra_kwargs["guided_json"] = guided_json_schema
                try:
                    result = self.adapter.generate(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=get_temperature(pool_type),
                        timeout_s=timeout_s,
                        **extra_kwargs,
                    )
                except TypeError:
                    # Older adapters (MockLLM) don't accept response_format / guided_json
                    result = self.adapter.generate(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=get_temperature(pool_type),
                        timeout_s=timeout_s,
                    )
                latency_ms = int((time.time() - attempt_start) * 1000)
                raw_text = result["text"]
                last_raw_text = raw_text

                parsed, repairs = parse_llm_output(raw_text)
                if parsed is not None:
                    self.circuit.record_success()
                    self.health.record_success("llm", latency_ms)
                    if repairs:
                        log_event(_log, "llm_repaired", repairs=repairs, attempt=attempt)
                    rd.retry_decision_reason = (
                        RETRY_REASON_INITIAL_SUCCESS if attempt == 0
                        else RETRY_REASON_RETRY_SUCCESS
                    )
                    rd.total_elapsed_ms = int((time.time() - call_started) * 1000)
                    return parsed, raw_text, latency_ms

                # parse 실패 → retry (budget 허용 시)
                rd.parse_failures += 1
                log_event(_log, "llm_parse_fail", repairs=repairs, attempt=attempt)
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": raw_text[:300]})
                    messages.append({"role": "user", "content": "Fix: return only valid JSON."})
                    continue

                self.circuit.record_failure()
                self.health.record_failure("llm", "parse_fail")
                rd.retry_decision_reason = RETRY_REASON_PARSE_RETRY_EXHAUSTED
                rd.total_elapsed_ms = int((time.time() - call_started) * 1000)
                return None, raw_text, latency_ms

            except Exception as e:
                latency_ms = int((time.time() - attempt_start) * 1000)
                rd.transport_failures += 1
                log_event(_log, "llm_error", error=str(e), attempt=attempt, latency_ms=latency_ms)
                self.circuit.record_failure()
                self.health.record_failure("llm", str(e))
                if attempt >= self.max_retries:
                    rd.retry_decision_reason = RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED
                    rd.total_elapsed_ms = int((time.time() - call_started) * 1000)
                    return None, "", latency_ms
                # Pre-retry cooldown — value derived from policy, sleep clamped
                # to remaining budget so the cooldown can never push us over
                # the deadline (T-tranche 2 2026-04-08).
                wait_decision = clamp_wait_to_budget(
                    kind=WAIT_KIND_TRANSPORT_RETRY,
                    configured_s=self.transport_retry_cooldown_s,
                    total_deadline_s=total_deadline_s,
                    elapsed_s=time.time() - call_started,
                    headroom_s=timeout_s,    # leave at least one full attempt window
                    source=self.transport_retry_cooldown_source,
                )
                rd.cooldown_decisions.append(wait_decision.to_dict())
                rd.total_cooldown_ms += int(wait_decision.applied_s * 1000)
                if wait_decision.applied_s > 0:
                    time.sleep(wait_decision.applied_s)

        rd.total_elapsed_ms = int((time.time() - call_started) * 1000)
        if not rd.retry_decision_reason:
            rd.retry_decision_reason = RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED
        return None, last_raw_text, 0

    def is_available(self) -> bool:
        return self.adapter.is_available() and self.circuit.allow()
