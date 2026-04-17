"""
degraded_modes.py - 장애 시 강등 경로

full → short → cached → mock → reject

NOTE 2026-04-08 (T-tranche 2):
    - DegradedModeHandler 는 LLM 호출 실패 시 즉시 (no-sleep) cached / mock /
      reject 응답을 만들기 때문에 현재 구현상 실제로는 wait 가 발생하지 않는다.
    - 다만 운영자가 artifact 한 줄로 "fallback 의 retry-delay 설정값이 어디서
      왔나?" 를 답할 수 있어야 한다는 invariant 를 만족시키기 위해, 정책 객체
      를 받아 ``self.fallback_retry_delay_s`` / ``self.fallback_retry_delay_source``
      를 expose 하고, 강등 응답마다 ``self.last_wait_decision`` 에 ``WaitDecision``
      을 남긴다 (configured_s, applied_s=0, skipped=True, source=...).
    - 실제 sleep 동작은 추가하지 않는다 (silent fallback / ambiguous success
      위험 회피). cached / mock 으로 떨어지는 것은 여전히 fail-loud telemetry
      에 같이 노출되어야 한다.
"""
from __future__ import annotations

from typing import Optional

from ..core.contracts import TaskRequest, TaskResult
from ..core.enums import TaskStatus, FallbackMode
from ..execution.timeouts import (
    UnifiedTimeoutPolicy,
    WaitDecision,
    DEFAULT_FALLBACK_RETRY_DELAY_S,
    WAIT_KIND_FALLBACK_RETRY,
    WAIT_SKIP_REASON_ZERO_CONFIGURED,
)
from ..observability.logger import get_logger, log_event

_log = get_logger("fallback")


class DegradedModeHandler:
    """LLM 실패/circuit open 시 강등 응답 생성"""

    def __init__(
        self,
        enable_cached: bool = True,
        enable_mock: bool = True,
        *,
        policy: Optional[UnifiedTimeoutPolicy] = None,
        fallback_retry_delay_s: float = DEFAULT_FALLBACK_RETRY_DELAY_S,
        fallback_retry_delay_source: str = "default",
    ):
        self._enable_cached = enable_cached
        self._enable_mock = enable_mock
        self._cache: dict[str, dict] = {}  # task_type → last good result
        # T-tranche 2: policy-derived fallback retry delay metadata.
        if policy is not None:
            self.fallback_retry_delay_s: float = float(policy.fallback_retry_delay_s)
            self.fallback_retry_delay_source: str = policy.cooldown_source or "policy"
        else:
            self.fallback_retry_delay_s = float(fallback_retry_delay_s)
            self.fallback_retry_delay_source = fallback_retry_delay_source
        # Last fallback wait decision (always populated on handle_failure call).
        self.last_wait_decision: Optional[WaitDecision] = None

    def cache_good_result(self, task_type: str, slots: dict) -> None:
        """성공한 결과를 캐시 (동일 태스크 유형별 마지막 1건)"""
        self._cache[task_type] = slots

    def record_wait_decision(self) -> WaitDecision:
        """Emit a canonical fallback ``WaitDecision`` and cache it on
        ``self.last_wait_decision``.

        This is a **pure record** entry point: it does not touch cache,
        mock, or reject logic. Callers that need explainability without
        committing to a degraded response (e.g. the dispatcher's failure
        exit sites, T-tranche-10) can consult this method to record the
        fallback wait slot *and then* keep whatever TaskResult they were
        already returning.

        The values produced here match the T-tranche-9 JSON-shape
        contract exactly: ``applied_s=0.0, skipped=True,
        skip_reason="zero_configured"`` because the current handler does
        not actually sleep (silent-fallback risk avoidance). The
        ``configured_s`` / ``source`` fields carry the policy values
        from this handler's constructor (policy-driven or legacy
        back-compat) so artifact readers can answer "where did the
        fallback delay come from?" with a single string label.

        Returns the ``WaitDecision`` for direct inspection by the caller;
        callers that just want the cached copy can read
        ``self.last_wait_decision`` instead.
        """
        decision = WaitDecision(
            kind=WAIT_KIND_FALLBACK_RETRY,
            configured_s=self.fallback_retry_delay_s,
            applied_s=0.0,
            skipped=True,
            skip_reason=WAIT_SKIP_REASON_ZERO_CONFIGURED,
            source=self.fallback_retry_delay_source,
        )
        self.last_wait_decision = decision
        return decision

    def handle_failure(self, request: TaskRequest, errors: list[str]) -> TaskResult:
        """강등 경로: cached → mock → error.

        T-tranche 2: 매 호출마다 ``last_wait_decision`` 을 ``WaitDecision`` 으로
        남긴다. 현재 구현은 fallback 직전에 sleep 하지 않으므로 ``applied_s=0``,
        ``skipped=True``, ``skip_reason=zero_configured`` 가 default 값이다.
        configured_s 값과 source 는 그대로 telemetry 에 carry 된다.

        T-tranche-10 (2026-04-09) refactor: the first block (WaitDecision
        emit) has been extracted into ``record_wait_decision()`` so the
        dispatcher can call it independently for pure explainability
        without triggering the cache/mock/reject tree. The behaviour of
        ``handle_failure`` itself is unchanged — it still records the
        decision first, then runs the strategy tree.
        """
        self.record_wait_decision()
        # 1. cached
        if self._enable_cached and request.task_type in self._cache:
            log_event(_log, "fallback_cached", task_type=request.task_type)
            return TaskResult(
                request_id=request.request_id,
                task_id=request.task_id,
                task_type=request.task_type,
                status=TaskStatus.DEGRADED,
                fallback_mode=FallbackMode.CACHED,
                slots=self._cache[request.task_type],
                errors=["using cached result"] + errors,
            )

        # 2. mock
        if self._enable_mock:
            log_event(_log, "fallback_mock", task_type=request.task_type)
            return TaskResult(
                request_id=request.request_id,
                task_id=request.task_id,
                task_type=request.task_type,
                status=TaskStatus.DEGRADED,
                fallback_mode=FallbackMode.MOCK,
                slots={"mock": True, "task_type": request.task_type},
                errors=["mock fallback"] + errors,
            )

        # 3. reject
        return TaskResult(
            request_id=request.request_id,
            task_id=request.task_id,
            task_type=request.task_type,
            status=TaskStatus.ERROR,
            fallback_mode=FallbackMode.REJECT,
            errors=errors,
        )
