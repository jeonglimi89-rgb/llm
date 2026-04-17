"""
test_scheduler_circuit_open_semantics.py — pin the "circuit-open is an
aborted execution (not a non-event)" invariant for the Scheduler state
machine.

Background
==========
The Scheduler has two "non-success" exit paths through the dispatcher,
and they differ in subtle ways that both need to be explicit and
tested:

  - **shed** (``Dispatcher.dispatch`` OverloadError branch): the
    request never reaches ``_execute``, so neither ``pre_execute`` nor
    ``post_execute`` is ever called. This is a **full non-event** for
    the Scheduler. ``last_wait_decision`` is preserved unchanged from
    the last real event. Pinned by T-tranche-13 via
    ``Scheduler.note_shed()`` + ``tests/unit/test_scheduler_shed_semantics.py``.

  - **circuit-open** (``Dispatcher._execute`` CircuitOpenError branch):
    ``pre_execute`` **did** run and likely slept some cooldown; then
    ``extract_slots`` raised ``CircuitOpenError`` and
    ``post_execute`` was never called. This is an **aborted execution**
    — real wall-clock was consumed (the pre_execute sleep), real
    telemetry was produced (``last_wait_decision`` was updated), but
    no LLM work was done so ``_last_finish`` / ``_last_was_heavy``
    must NOT advance. T-tranche-14 (2026-04-09) makes this invariant
    explicit with ``Scheduler.note_circuit_open()`` + this file.

The two methods are deliberately parallel but semantically distinct:

    after note_shed():          last_wait_decision == prior value
    after note_circuit_open():  last_wait_decision == the wait decision
                                emitted by pre_execute just before the
                                circuit-open abort (NOT prior)

Why this invariant matters
==========================
If a future refactor mistakenly calls ``scheduler.post_execute(...)``
on the circuit-open path (e.g., while "cleaning up" the dispatcher),
``_last_finish`` would advance to the circuit-open moment. The next
real execute would see ``elapsed_since_last = 0`` and force a full
cooldown sleep — double-cooldown — because the "last real finish" is
now wrong. Conversely, if the wait-decision write from pre_execute
were clobbered by note_circuit_open, telemetry would lose the
explanation for where the wall-clock time went on the aborted case.

Both failure modes are caught by tests in this file:
  - Block A pins the non-mutation contract on the method itself.
  - Block B pins the state-machine correctness across
    success → circuit-open → success sequences.
  - Block C pins the dispatcher wiring + legacy back-compat.
"""
from __future__ import annotations

import time

from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import (
    UnifiedTimeoutPolicy,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_SKIP_REASON_ALREADY_ELAPSED,
)
from src.app.core.contracts import TaskRequest


# Task types — chosen to exercise both HEAVY and LIGHT branches of
# scheduler.post_execute's _last_was_heavy update.
HEAVY_TASK_TYPE = "builder.requirement_parse"   # in HEAVY_TASKS
LIGHT_TASK_TYPE = "builder.patch_intent_parse"  # in LIGHT_TASKS, NOT heavy


def _heavy_req(label: str = "h") -> TaskRequest:
    return TaskRequest(domain="builder", task_name="requirement_parse", user_input=label)


def _light_req(label: str = "l") -> TaskRequest:
    return TaskRequest(domain="builder", task_name="patch_intent_parse", user_input=label)


def _fresh_scheduler(heavy_s: float = 0.1, light_s: float = 0.05) -> Scheduler:
    policy = UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=heavy_s,
        scheduler_cooldown_light_s=light_s,
        cooldown_source="test",
    )
    return Scheduler(policy=policy)


# ===========================================================================
# Block A — note_circuit_open() is a pure no-op for baseline state
# ===========================================================================

def test_note_circuit_open_leaves_last_finish_untouched():
    """On a pristine Scheduler, note_circuit_open must not populate
    ``_last_finish`` from 0.0."""
    print("  [A1] note_circuit_open does not populate _last_finish from zero")
    s = _fresh_scheduler()
    assert s._last_finish == 0.0
    s.note_circuit_open(_heavy_req())
    assert s._last_finish == 0.0, s._last_finish
    print("    OK")


def test_note_circuit_open_does_not_overwrite_prior_last_finish():
    """With a prior successful post_execute, note_circuit_open must not
    shift ``_last_finish`` forward. The next pre_execute must continue
    to measure elapsed time from the original success."""
    print("  [A2] note_circuit_open does not overwrite _last_finish from a prior post_execute")
    s = _fresh_scheduler()
    s.post_execute(_heavy_req("prior"))
    original = s._last_finish
    assert original > 0.0
    time.sleep(0.01)
    s.note_circuit_open(_light_req("co"))
    assert s._last_finish == original, (
        f"note_circuit_open overwrote _last_finish: {s._last_finish} != {original}"
    )
    print("    OK")


def test_note_circuit_open_does_not_flip_last_was_heavy():
    """The circuit-open request's task_type must not affect
    ``_last_was_heavy`` because that flag belongs to the prior
    successful execute, not to aborted ones."""
    print("  [A3] note_circuit_open does not flip _last_was_heavy")
    s = _fresh_scheduler()
    s.post_execute(_heavy_req("prior_heavy"))
    assert s._last_was_heavy is True
    s.note_circuit_open(_light_req("co_light"))
    assert s._last_was_heavy is True

    s2 = _fresh_scheduler()
    s2.post_execute(_light_req("prior_light"))
    assert s2._last_was_heavy is False
    s2.note_circuit_open(_heavy_req("co_heavy"))
    assert s2._last_was_heavy is False
    print("    OK")


def test_note_circuit_open_preserves_pre_execute_wait_decision():
    """Critical difference from ``note_shed``: after the circuit-open
    case, ``last_wait_decision`` must still be the WaitDecision from
    that case's ``pre_execute`` call. It must NOT be reset or cleared
    by ``note_circuit_open``. The pre_execute sleep was real and its
    decision is legitimate telemetry for the aborted case.
    """
    print("  [A4] note_circuit_open preserves the pre_execute-populated last_wait_decision")
    s = _fresh_scheduler()
    # Anchor with a successful heavy case so the next pre_execute emits
    # a scheduler_heavy wait (non-trivial decision).
    s.pre_execute(_heavy_req("warm"))
    s.post_execute(_heavy_req("warm"))
    time.sleep(0.01)

    # Circuit-open case: pre_execute runs and writes last_wait_decision.
    circuit_open_decision = s.pre_execute(_heavy_req("co"))
    assert s.last_wait_decision is circuit_open_decision
    co_id = id(circuit_open_decision)
    assert circuit_open_decision.kind == WAIT_KIND_SCHEDULER_HEAVY

    # note_circuit_open must NOT clear or overwrite this decision.
    s.note_circuit_open(_heavy_req("co"))
    assert s.last_wait_decision is circuit_open_decision, (
        "note_circuit_open overwrote last_wait_decision; the pre_execute "
        "decision from the aborted case must be preserved for telemetry"
    )
    assert id(s.last_wait_decision) == co_id
    print(f"    OK: last_wait_decision still the circuit-open pre_execute decision")


def test_note_circuit_open_returns_none_for_any_task_type():
    """note_circuit_open returns None for heavy / light / unknown task
    types and does not raise."""
    print("  [A5] note_circuit_open returns None for heavy / light / unknown task types")
    s = _fresh_scheduler()
    assert s.note_circuit_open(_heavy_req()) is None
    assert s.note_circuit_open(_light_req()) is None
    unknown = TaskRequest(domain="minecraft", task_name="edit_parse", user_input="u")
    assert s.note_circuit_open(unknown) is None
    print("    OK")


# ===========================================================================
# Block B — state-machine correctness across success → circuit-open → success
# ===========================================================================

def test_heavy_success_then_circuit_open_then_heavy_baseline_holds():
    """Core sequence invariant: case1 (heavy, success) →
    case2 (heavy, circuit-open after pre_execute slept) →
    case3 (heavy, pre_execute). case3's cooldown must be anchored to
    case1's finish, not case2's circuit-open moment.

    With ``heavy_cooldown_s = 0.1`` and case2's pre_execute sleeping
    the full (0.1 - tiny) ≈ 0.1s, the wall clock at the start of
    case3 is well past T+0.1, so case3 must see ``already_elapsed``.
    """
    print("  [B1] heavy → circuit-open → heavy: case3 cooldown anchored to case1")
    s = _fresh_scheduler(heavy_s=0.1, light_s=0.05)

    # case1: heavy, successful post_execute
    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))
    t1 = s._last_finish
    assert t1 > 0.0

    # case2: pre_execute (heavy wait slept), then circuit-open →
    # note_circuit_open (no post_execute). Elapsed since case1 at the
    # start of case2.pre_execute ≈ 0, so case2 will sleep ~0.1s.
    time.sleep(0.005)
    c2_decision = s.pre_execute(_heavy_req("c2"))
    assert c2_decision.kind == WAIT_KIND_SCHEDULER_HEAVY
    # pre_execute should have slept a meaningful chunk (not all time,
    # just the complementary amount)
    assert c2_decision.applied_s > 0, c2_decision
    s.note_circuit_open(_heavy_req("c2"))
    # Invariants after circuit-open
    assert s._last_finish == t1, s._last_finish
    assert s._last_was_heavy is True

    # case3: heavy, immediate. Wall clock is now t1 + 0.005 + ~0.1 =
    # t1 + ~0.105, which is ≥ 0.1 heavy cooldown, so already_elapsed.
    decision = s.pre_execute(_heavy_req("c3"))
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY, decision
    assert decision.applied_s == 0.0, decision
    assert decision.skipped is True
    assert decision.skip_reason == WAIT_SKIP_REASON_ALREADY_ELAPSED
    print("    OK: case3 already_elapsed, baseline still at case1")


def test_heavy_success_then_light_request_circuit_open_preserves_heavy_flag():
    """case2 is a LIGHT request that circuit-opens. Even though case2's
    pre_execute will emit a scheduler_heavy wait (because case1 was
    heavy), note_circuit_open must NOT flip ``_last_was_heavy`` to
    False just because case2's request was light. case3 must still
    see the scheduler_heavy kind derived from case1's flag.
    """
    print("  [B2] heavy → light-request circuit-open → heavy: heavy flag survives")
    s = _fresh_scheduler(heavy_s=0.2, light_s=0.1)  # wider gap so case3 waits

    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))
    t1 = s._last_finish
    assert s._last_was_heavy is True

    # case2: LIGHT request + circuit-open. pre_execute sees
    # _last_was_heavy=True from case1, so emits scheduler_heavy.
    time.sleep(0.005)
    c2_decision = s.pre_execute(
        _light_req("c2"),
        total_deadline_s=5.0,
        request_headroom_s=0.5,
    )
    assert c2_decision.kind == WAIT_KIND_SCHEDULER_HEAVY, c2_decision
    s.note_circuit_open(_light_req("c2"))
    assert s._last_was_heavy is True   # not flipped
    assert s._last_finish == t1

    # case3: heavy immediately. Should also be scheduler_heavy kind
    # because the flag belongs to case1 (heavy success). Whether the
    # wait is still needed depends on elapsed; we mainly assert the kind.
    c3_decision = s.pre_execute(
        _heavy_req("c3"),
        total_deadline_s=5.0,
        request_headroom_s=0.5,
    )
    assert c3_decision.kind == WAIT_KIND_SCHEDULER_HEAVY, (
        f"case3 kind should still be scheduler_heavy after a light-request "
        f"circuit-open, got {c3_decision.kind}"
    )
    print("    OK: heavy flag preserved across light-request circuit-open")


def test_light_success_then_heavy_request_circuit_open_preserves_light_flag():
    """Converse of B2: case1 is LIGHT success, case2 is a HEAVY request
    that circuit-opens, case3 should still use the LIGHT-based kind
    (scheduler_light) because case1 was light."""
    print("  [B3] light → heavy-request circuit-open → * : light flag survives")
    s = _fresh_scheduler(heavy_s=0.2, light_s=0.1)

    s.pre_execute(_light_req("c1"))
    s.post_execute(_light_req("c1"))
    assert s._last_was_heavy is False

    time.sleep(0.005)
    c2_decision = s.pre_execute(
        _heavy_req("c2"),
        total_deadline_s=5.0,
        request_headroom_s=0.5,
    )
    # case2's pre_execute emits scheduler_light because _last_was_heavy
    # comes from case1 (light), not from case2's own task_type.
    assert c2_decision.kind == WAIT_KIND_SCHEDULER_LIGHT, c2_decision
    s.note_circuit_open(_heavy_req("c2"))
    assert s._last_was_heavy is False

    # case3 should still see scheduler_light kind.
    c3_decision = s.pre_execute(
        _heavy_req("c3"),
        total_deadline_s=5.0,
        request_headroom_s=0.5,
    )
    assert c3_decision.kind == WAIT_KIND_SCHEDULER_LIGHT, (
        f"case3 kind should still be scheduler_light, got {c3_decision.kind}"
    )
    print("    OK: light flag preserved across heavy-request circuit-open")


def test_consecutive_circuit_opens_keep_baseline_anchored():
    """Multiple consecutive circuit-opens must not drift the state
    machine. case1 (heavy, success) → circuit-open × 3 → case5
    (heavy, pre_execute): case5 must still see ``_last_finish == t1``
    and ``_last_was_heavy == True``.

    This also exercises the wait-still-fires path: repeated circuit-
    opens happen within the cooldown window, so each case2..4 sleeps
    the remaining gap; by the time case5 arrives the total elapsed is
    well past the heavy cooldown.
    """
    print("  [B4] consecutive circuit-opens keep baseline anchored to case1")
    s = _fresh_scheduler(heavy_s=0.1, light_s=0.05)

    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))
    t1 = s._last_finish
    flag1 = s._last_was_heavy

    for i in range(3):
        # Each iteration pre_executes (which sleeps or already_elapsed)
        # and then calls note_circuit_open (the abort signal).
        s.pre_execute(_heavy_req(f"co_{i}"))
        s.note_circuit_open(_heavy_req(f"co_{i}"))
        assert s._last_finish == t1, f"iter {i}: _last_finish drifted to {s._last_finish}"
        assert s._last_was_heavy == flag1

    # case5: the baseline is still case1. Wall-clock has advanced by
    # the 3 pre_execute sleeps + inter-iter gaps, so already_elapsed.
    decision = s.pre_execute(_heavy_req("c5"))
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY
    assert s._last_finish == t1
    print("    OK: baseline still at case1 after 3 circuit-opens")


def test_circuit_open_path_differs_from_shed_in_last_wait_decision():
    """Regression test that documents the explicit semantic difference
    between ``note_shed`` (pre_execute NEVER ran) and
    ``note_circuit_open`` (pre_execute DID run).

    Both leave ``_last_finish`` and ``_last_was_heavy`` anchored to
    the prior success. But:

      - after note_shed: ``last_wait_decision`` is whatever it was
        BEFORE the shed case (the prior real event)
      - after note_circuit_open: ``last_wait_decision`` is the
        WaitDecision emitted by the circuit-open case's pre_execute
        (a NEW real event)
    """
    print("  [B5] shed and circuit-open differ in last_wait_decision semantics")
    # shed scenario
    s_shed = _fresh_scheduler()
    s_shed.pre_execute(_heavy_req("warm"))
    s_shed.post_execute(_heavy_req("warm"))
    time.sleep(0.01)
    prior_real_decision = s_shed.pre_execute(_heavy_req("real"))
    s_shed.note_shed(_light_req("shed"))
    assert s_shed.last_wait_decision is prior_real_decision, (
        "after note_shed, last_wait_decision must be the prior real decision"
    )

    # circuit-open scenario
    s_co = _fresh_scheduler()
    s_co.pre_execute(_heavy_req("warm"))
    s_co.post_execute(_heavy_req("warm"))
    time.sleep(0.01)
    prior_real_decision_co = s_co.pre_execute(_heavy_req("prior_real"))
    # Now the aborted case runs its pre_execute, producing a NEW decision.
    new_decision = s_co.pre_execute(_heavy_req("co_case"))
    s_co.note_circuit_open(_heavy_req("co_case"))
    assert s_co.last_wait_decision is new_decision, (
        "after note_circuit_open, last_wait_decision must be the circuit-open "
        "case's pre_execute decision, NOT the prior real decision"
    )
    assert s_co.last_wait_decision is not prior_real_decision_co
    print("    OK: shed preserves prior, circuit-open preserves own pre_execute decision")


# ===========================================================================
# Block C — Dispatcher wiring + back-compat
# ===========================================================================

def test_dispatcher_circuit_open_branch_invokes_note_circuit_open():
    """When ``_execute`` raises ``CircuitOpenError`` (because the
    circuit is already open), the dispatcher's except branch must
    call ``self.scheduler.note_circuit_open(request)`` exactly once.
    Verified via a recording Scheduler subclass."""
    print("  [C1] _execute CircuitOpenError branch calls scheduler.note_circuit_open")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.execution.timeouts import TimeoutPolicy
    from src.app.execution.circuit_breaker import CircuitBreaker
    from src.app.execution.queue_manager import QueueManager
    from src.app.core.errors import CircuitOpenError
    from src.app.core.enums import TaskStatus
    from src.app.core.contracts import TaskRequest
    from src.app.domain.registry import TaskSpec

    calls: list[TaskRequest] = []

    class _RecordingScheduler(Scheduler):
        def note_circuit_open(self, request):
            calls.append(request)
            return super().note_circuit_open(request)

    class _CircuitOpenLLM:
        last_retry_decision = None
        adapter = None
        def extract_slots(self, **kw):
            raise CircuitOpenError()

    sched = _RecordingScheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    d = Dispatcher(
        llm_client=_CircuitOpenLLM(),
        queue=QueueManager(max_concurrency=1, max_depth=10),
        scheduler=sched,
        timeouts=TimeoutPolicy(),
    )
    spec = TaskSpec(
        task_type="builder.requirement_parse",
        domain="builder",
        task_name="requirement_parse",
        pool_type="strict_json",
        prompt_file="",
        schema_file="",
    )
    req = TaskRequest(domain="builder", task_name="requirement_parse", user_input="x")

    result = d.dispatch(req, spec)
    assert result.status == TaskStatus.ERROR, result.status
    assert any("circuit" in e.lower() for e in (result.errors or [])), result.errors
    assert len(calls) == 1, f"expected exactly 1 note_circuit_open call, got {len(calls)}"
    assert calls[0] is req
    # And baseline state is still pristine (no prior success, no mutation).
    assert sched._last_finish == 0.0
    assert sched._last_was_heavy is False
    print("    OK: circuit-open branch called note_circuit_open exactly once")


def test_dispatcher_circuit_open_tolerates_legacy_scheduler_without_method():
    """Back-compat: a Scheduler implementation without
    ``note_circuit_open`` must still allow the dispatcher's
    CircuitOpenError branch to complete cleanly. The dispatcher's
    ``try/except AttributeError`` swallows the AttributeError so the
    returned TaskResult is still ``status=ERROR`` with the usual
    circuit-open payload."""
    print("  [C2] Dispatcher CircuitOpen branch is AttributeError-safe on legacy scheduler")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.execution.timeouts import TimeoutPolicy
    from src.app.execution.queue_manager import QueueManager
    from src.app.core.errors import CircuitOpenError
    from src.app.core.enums import TaskStatus
    from src.app.core.contracts import TaskRequest
    from src.app.domain.registry import TaskSpec

    class _LegacyScheduler:
        last_wait_decision = None
        def pre_execute(self, *a, **k): pass
        def post_execute(self, *a, **k): pass
        # no note_circuit_open, no note_shed

    class _CircuitOpenLLM:
        last_retry_decision = None
        adapter = None
        def extract_slots(self, **kw):
            raise CircuitOpenError()

    d = Dispatcher(
        llm_client=_CircuitOpenLLM(),
        queue=QueueManager(max_concurrency=1, max_depth=10),
        scheduler=_LegacyScheduler(),
        timeouts=TimeoutPolicy(),
    )
    spec = TaskSpec(
        task_type="builder.requirement_parse",
        domain="builder",
        task_name="requirement_parse",
        pool_type="strict_json",
        prompt_file="",
        schema_file="",
    )
    req = TaskRequest(domain="builder", task_name="requirement_parse", user_input="x")
    result = d.dispatch(req, spec)
    assert result.status == TaskStatus.ERROR
    print("    OK: legacy scheduler without note_circuit_open is tolerated")


TESTS = [
    # Block A — no-op invariants
    test_note_circuit_open_leaves_last_finish_untouched,
    test_note_circuit_open_does_not_overwrite_prior_last_finish,
    test_note_circuit_open_does_not_flip_last_was_heavy,
    test_note_circuit_open_preserves_pre_execute_wait_decision,
    test_note_circuit_open_returns_none_for_any_task_type,
    # Block B — sequence correctness
    test_heavy_success_then_circuit_open_then_heavy_baseline_holds,
    test_heavy_success_then_light_request_circuit_open_preserves_heavy_flag,
    test_light_success_then_heavy_request_circuit_open_preserves_light_flag,
    test_consecutive_circuit_opens_keep_baseline_anchored,
    test_circuit_open_path_differs_from_shed_in_last_wait_decision,
    # Block C — dispatcher wiring
    test_dispatcher_circuit_open_branch_invokes_note_circuit_open,
    test_dispatcher_circuit_open_tolerates_legacy_scheduler_without_method,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Scheduler circuit-open semantics tests (T-tranche-14)")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\nResults: {passed}/{len(TESTS)} passed")
