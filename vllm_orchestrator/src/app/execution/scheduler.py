"""
scheduler.py - 태스크 스케줄링 정책

CPU 환경: heavy/light 분류, 요청 간 쿨다운.

NOTE 2026-04-08 (T-tranche 2):
    - heavy/light cooldown 값은 더 이상 함수 내부 magic number 가 아니라
      ``UnifiedTimeoutPolicy`` 의 ``scheduler_cooldown_{heavy,light}_s`` 에서
      유도된다.
    - ``pre_execute()`` 는 ``WaitDecision`` 을 반환하고 ``self.last_wait_decision``
      에 저장한다. 이 값을 dispatcher 가 ``RetryDecision`` 에 합쳐서 telemetry
      로 노출한다.
    - 모든 cooldown 은 ``total_deadline_s`` 가 주어지면 ``clamp_wait_to_budget``
      을 거쳐 budget 안쪽으로 clamp 또는 skip 된다.
"""
from __future__ import annotations

import time
from typing import Optional

from ..core.contracts import TaskRequest
from .timeouts import (
    UnifiedTimeoutPolicy,
    WaitDecision,
    clamp_wait_to_budget,
    DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S,
    DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_SKIP_REASON_NONE,
    WAIT_SKIP_REASON_ZERO_CONFIGURED,
    WAIT_SKIP_REASON_ALREADY_ELAPSED,
)

# 도메인별 heavy/light 분류
# Single source of truth: TASK_REGISTRY.is_heavy field.
# Before this fix, HEAVY_TASKS was a hardcoded set that drifted from
# TASK_REGISTRY.is_heavy (the 3 context_query tasks were in HEAVY_TASKS
# but had is_heavy=False). Now HEAVY_TASKS is derived at import time
# from the registry so they can never drift again.
from ..domain.registry import TASK_REGISTRY as _TASK_REGISTRY

HEAVY_TASKS: set[str] = {
    tt for tt, spec in _TASK_REGISTRY.items() if spec.is_heavy
}

LIGHT_TASKS: set[str] = {
    tt for tt, spec in _TASK_REGISTRY.items() if spec.enabled and not spec.is_heavy
}


class Scheduler:
    """CPU 보호 스케줄러. heavy 태스크 간 쿨다운 삽입.

    Construction
    ------------
    Two ways to set cooldown values:

    1. **Legacy positional / keyword args (back-compat)** ::

           Scheduler()                                    # 2.0 / 0.5 defaults
           Scheduler(cooldown_heavy_s=2.0, cooldown_light_s=0.5)

       In this mode the source string is ``"constructor_default"``.

    2. **Explicit policy (T-tranche 2 preferred path)** ::

           Scheduler(policy=unified_timeout_policy)

       The policy carries both the values *and* their source. ``policy`` is a
       keyword-only arg so the legacy positional path is not affected.

    Both forms can coexist; ``policy=`` takes precedence if provided.
    """

    def __init__(
        self,
        cooldown_heavy_s: float = DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S,
        cooldown_light_s: float = DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S,
        *,
        policy: Optional[UnifiedTimeoutPolicy] = None,
    ):
        if policy is not None:
            self._cooldown_heavy = float(policy.scheduler_cooldown_heavy_s)
            self._cooldown_light = float(policy.scheduler_cooldown_light_s)
            self._source = policy.cooldown_source or "policy"
        else:
            self._cooldown_heavy = float(cooldown_heavy_s)
            self._cooldown_light = float(cooldown_light_s)
            self._source = "constructor_default"
        self._last_finish: float = 0.0
        self._last_was_heavy: bool = False
        self.last_wait_decision: Optional[WaitDecision] = None

    @property
    def cooldown_source(self) -> str:
        return self._source

    @property
    def cooldown_heavy_s(self) -> float:
        return self._cooldown_heavy

    @property
    def cooldown_light_s(self) -> float:
        return self._cooldown_light

    def pre_execute(
        self,
        request: TaskRequest,
        *,
        total_deadline_s: Optional[float] = None,
        request_headroom_s: float = 0.0,
    ) -> WaitDecision:
        """실행 전 쿨다운 대기.

        Returns the ``WaitDecision`` describing what was actually slept (and
        why). The decision is also cached on ``self.last_wait_decision``.

        - When this is the first execute (no prior ``post_execute``), no
          cooldown is needed and a ``zero_configured`` skipped decision is
          returned.
        - Otherwise the cooldown is computed against ``time.time() -
          self._last_finish``. If the natural elapsed time already exceeds
          the configured cooldown, the wait is skipped with reason
          ``already_elapsed``.
        - Whatever is left is fed through ``clamp_wait_to_budget`` so the
          wait never escapes ``total_deadline_s - request_headroom_s``.
        """
        kind = WAIT_KIND_SCHEDULER_HEAVY if self._last_was_heavy else WAIT_KIND_SCHEDULER_LIGHT
        configured = self._cooldown_heavy if self._last_was_heavy else self._cooldown_light

        if self._last_finish == 0:
            # No prior execute → no cooldown needed.
            decision = WaitDecision(
                kind=kind,
                configured_s=float(configured),
                applied_s=0.0,
                skipped=True,
                skip_reason=WAIT_SKIP_REASON_ZERO_CONFIGURED,
                source=self._source,
            )
            self.last_wait_decision = decision
            return decision

        elapsed_since_last = time.time() - self._last_finish
        if elapsed_since_last >= configured:
            decision = WaitDecision(
                kind=kind,
                configured_s=float(configured),
                applied_s=0.0,
                skipped=True,
                skip_reason=WAIT_SKIP_REASON_ALREADY_ELAPSED,
                source=self._source,
            )
            self.last_wait_decision = decision
            return decision

        wait_needed = configured - elapsed_since_last
        decision = clamp_wait_to_budget(
            kind=kind,
            configured_s=wait_needed,
            total_deadline_s=total_deadline_s,
            elapsed_s=0.0,                  # scheduler runs at the start of this case
            headroom_s=request_headroom_s,
            source=self._source,
        )
        if decision.applied_s > 0:
            time.sleep(decision.applied_s)
        self.last_wait_decision = decision
        return decision

    def post_execute(self, request: TaskRequest) -> None:
        """실행 후 상태 기록"""
        self._last_finish = time.time()
        self._last_was_heavy = request.task_type in HEAVY_TASKS

    def note_circuit_open(self, request: TaskRequest) -> None:
        """Record that a request was aborted by a circuit-open condition
        after ``pre_execute`` had already run — intentional **no-op**
        for the baseline state machine.

        Contract (T-tranche-14, 2026-04-09)
        -----------------------------------
        The scheduler enforces cooldown between *successful executions*.
        A circuit-open case is different from a shed case in two ways:

          - ``pre_execute`` **did** run, so ``self.last_wait_decision``
            is populated with a real WaitDecision describing the cooldown
            that was actually slept. That real sleep consumed wall-clock
            time and must not be overwritten — it is legitimate
            explainability data for the aborted case and for telemetry
            consumers reading ``scheduler.last_wait_decision``.
          - But the LLM never actually ran (circuit was open), so no
            real CPU work was consumed. Therefore ``_last_finish`` and
            ``_last_was_heavy`` must NOT advance. The next successful
            execution's cooldown must still be measured against the
            **previous successful** ``post_execute`` anchor.

        This method pins both halves of that contract:

          1. ``_last_finish`` and ``_last_was_heavy`` are NOT mutated
             (same as ``note_shed``).
          2. ``last_wait_decision`` is explicitly **preserved** — we
             do NOT clear or overwrite it, because the pre_execute-
             populated decision is the correct "most recent scheduler
             event" for telemetry.

        Why this method exists
        ----------------------
        Before T-tranche-14 the circuit-open path in the dispatcher
        simply returned a TaskResult without any explicit signal to
        the scheduler. The intent — "the scheduler knows this case
        aborted, but its baseline state should stay anchored to the
        last success" — was invisible in source and easy to break in
        a future refactor (e.g., by introducing a
        ``scheduler.post_execute(...)`` call on the circuit-open path
        that would silently advance ``_last_finish``). This method
        gives the dispatcher an explicit hook to call so the contract
        is visible and enforced.

        Semantic distinction from ``note_shed``
        ---------------------------------------
        ``note_shed`` is called on queue overload before ``pre_execute``
        ever runs — a full non-event. ``note_circuit_open`` is called
        AFTER ``pre_execute`` ran its sleep but BEFORE ``post_execute``
        would have run, so ``last_wait_decision`` is already a real
        post-pre_execute value. Both methods leave ``_last_finish`` /
        ``_last_was_heavy`` untouched, but they differ in what
        ``last_wait_decision`` looks like afterwards:

          - after note_shed:         last_wait_decision == prior value
          - after note_circuit_open: last_wait_decision == the
                                     wait decision emitted by the
                                     circuit-open case's pre_execute

        Tests in ``tests/unit/test_scheduler_circuit_open_semantics.py``
        pin both halves: the baseline non-shift invariants AND the
        explicit preservation of ``last_wait_decision``.
        """
        # Deliberately empty. The contract is negative: this method
        # must NOT mutate _last_finish / _last_was_heavy /
        # last_wait_decision. Any future change that starts mutating
        # state inside note_circuit_open will be caught by the block A
        # invariant tests in test_scheduler_circuit_open_semantics.py.
        return

    def note_shed(self, request: TaskRequest) -> None:
        """Record that a request was shed (queue overload) without ever
        reaching ``_execute`` — intentional **no-op** for the state machine.

        Contract (T-tranche-13, 2026-04-09)
        -----------------------------------
        The scheduler enforces a cooldown between *successful executions*.
        A shed case never touches the LLM or the CPU, so it is NOT
        counted as a "prior execution" — ``_last_finish`` and
        ``_last_was_heavy`` must stay anchored to the most recent real
        ``post_execute`` call.

        Why this method exists
        ----------------------
        Before T-tranche-13 the dispatcher's shed branch simply skipped
        the scheduler entirely, which made the "shed is a non-event"
        invariant invisible in the source. A future refactor could have
        easily introduced a ``scheduler.post_execute(shed_request)``
        call that would silently advance ``_last_finish`` and corrupt
        cooldown accounting. This method gives the dispatcher an
        explicit hook to call on the shed path and **pins the no-op
        semantics in code**: any future change that starts mutating
        ``_last_finish`` or ``_last_was_heavy`` inside ``note_shed``
        will be caught by the invariant tests in
        ``tests/unit/test_scheduler_shed_semantics.py``.

        ``last_wait_decision`` is also left unchanged — the shed case
        did not compute a new cooldown decision, so reading
        ``last_wait_decision`` between shed cases would return the
        decision from the last pre_execute, which is the correct
        "most recent scheduler event" semantics for telemetry
        consumers.
        """
        # Deliberately empty. See docstring for the contract this method
        # pins. Tests in test_scheduler_shed_semantics.py enforce that
        # calling note_shed() does NOT mutate _last_finish /
        # _last_was_heavy / last_wait_decision.
        return

    def is_heavy(self, request: TaskRequest) -> bool:
        return request.task_type in HEAVY_TASKS
