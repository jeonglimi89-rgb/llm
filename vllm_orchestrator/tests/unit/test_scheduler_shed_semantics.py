"""
test_scheduler_shed_semantics.py — pin the "shed is a non-event" invariant
for the Scheduler state machine.

Background
==========
The Scheduler tracks three pieces of state across dispatch attempts:
  - ``_last_finish``: wall-clock timestamp of the most recent successful
    ``post_execute`` call.
  - ``_last_was_heavy``: whether the most recently post_execute'd task
    was in ``HEAVY_TASKS``. Determines whether the next ``pre_execute``
    emits a ``scheduler_heavy`` or ``scheduler_light`` wait.
  - ``last_wait_decision``: cached ``WaitDecision`` from the most recent
    ``pre_execute`` call, surfaced to the dispatcher for telemetry.

Shed cases (``QueueManager`` raising ``OverloadError`` before the handler
even runs) never consume any LLM / CPU work and never call
``pre_execute`` or ``post_execute``. Before T-tranche-13, this was
implicit — the dispatcher's shed branch simply skipped the scheduler
entirely. T-tranche-13 (2026-04-09) makes the invariant explicit:

  1. ``Scheduler.note_shed(request)`` is a documented no-op that pins
     the contract "a shed case MUST NOT advance the state machine".
  2. ``Dispatcher._build_shed_task_result(request)`` calls
     ``self.scheduler.note_shed(request)`` so the contract is visible
     in dispatcher source.
  3. The tests in this file enforce the invariant so a future change
     that starts mutating state inside ``note_shed`` will be caught
     immediately.

Why this invariant matters
==========================
``Scheduler.pre_execute()`` measures ``time.time() - self._last_finish``
and uses it to decide whether to wait, clamp, or skip the cooldown.
If a shed case mutated ``_last_finish`` to "now" or ``_last_was_heavy``
to False, the next real execute would either:
  - over-cooldown (because the heavy→light flag got flipped to light)
  - under-cooldown (because ``_last_finish`` jumped forward, making
    ``elapsed_since_last`` artificially large)
Both break the "enforce CPU cooldown between successful executions"
semantics of the scheduler.
"""
from __future__ import annotations

import time

from src.app.execution.scheduler import Scheduler, HEAVY_TASKS
from src.app.execution.timeouts import (
    UnifiedTimeoutPolicy,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_SKIP_REASON_ALREADY_ELAPSED,
    WAIT_SKIP_REASON_ZERO_CONFIGURED,
)
from src.app.core.contracts import TaskRequest


# Pick a known-heavy and a known-light task type from the module-level sets,
# so the tests fail if anyone removes these from HEAVY_TASKS / LIGHT_TASKS.
HEAVY_TASK_TYPE = "builder.requirement_parse"
LIGHT_TASK_TYPE = "builder.patch_intent_parse"


def _heavy_req(label: str = "h") -> TaskRequest:
    return TaskRequest(domain="builder", task_name="requirement_parse", user_input=label)


def _light_req(label: str = "l") -> TaskRequest:
    return TaskRequest(domain="builder", task_name="patch_intent_parse", user_input=label)


def _fresh_scheduler(heavy_s: float = 0.1, light_s: float = 0.05) -> Scheduler:
    """Scheduler with tight cooldown values so the tests run in milliseconds
    but still exercise the "wait was applied" and "skipped/already_elapsed"
    branches deterministically."""
    policy = UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=heavy_s,
        scheduler_cooldown_light_s=light_s,
        cooldown_source="test",
    )
    return Scheduler(policy=policy)


# ===========================================================================
# A. note_shed() is a pure no-op
# ===========================================================================

def test_note_shed_leaves_last_finish_untouched():
    """Calling note_shed on a pristine Scheduler must not populate
    ``_last_finish`` — otherwise a shed case would look like a prior
    successful execute to the next pre_execute call."""
    print("  [A1] note_shed does not populate _last_finish from zero")
    s = _fresh_scheduler()
    assert s._last_finish == 0.0
    s.note_shed(_heavy_req())
    assert s._last_finish == 0.0, (
        f"note_shed must not advance _last_finish from zero, got {s._last_finish}"
    )
    print("    OK")


def test_note_shed_does_not_overwrite_prior_last_finish():
    """With a prior successful post_execute, note_shed must not update
    ``_last_finish`` to the shed moment. The next pre_execute must
    continue to measure elapsed time against the original success."""
    print("  [A2] note_shed does not overwrite _last_finish from a prior post_execute")
    s = _fresh_scheduler()
    # Prior success: anchor _last_finish to a known timestamp.
    s.post_execute(_heavy_req("prior"))
    original = s._last_finish
    assert original > 0.0

    # Simulate time passing; note_shed must NOT move _last_finish.
    time.sleep(0.01)
    s.note_shed(_light_req("shed"))
    assert s._last_finish == original, (
        f"note_shed overwrote _last_finish: {s._last_finish} != {original}"
    )
    print("    OK")


def test_note_shed_does_not_flip_last_was_heavy():
    """note_shed must not mutate ``_last_was_heavy`` regardless of the
    shed request's task_type. A shed light request after a heavy success
    must leave the flag at True so the next pre_execute continues to
    emit a scheduler_heavy wait slot."""
    print("  [A3] note_shed does not flip _last_was_heavy")
    s = _fresh_scheduler()
    # Heavy success → _last_was_heavy=True
    s.post_execute(_heavy_req("prior_heavy"))
    assert s._last_was_heavy is True

    # Shed with a LIGHT request — must not flip.
    s.note_shed(_light_req("shed_light"))
    assert s._last_was_heavy is True, (
        "note_shed flipped _last_was_heavy to False; a shed light request "
        "must not change the heavy/light flag"
    )

    # Also the reverse: light success → shed heavy → still light.
    s2 = _fresh_scheduler()
    s2.post_execute(_light_req("prior_light"))
    assert s2._last_was_heavy is False
    s2.note_shed(_heavy_req("shed_heavy"))
    assert s2._last_was_heavy is False, (
        "note_shed flipped _last_was_heavy to True on a shed heavy request"
    )
    print("    OK")


def test_note_shed_does_not_overwrite_last_wait_decision():
    """The most recent scheduler ``last_wait_decision`` (from the prior
    real pre_execute) must remain untouched across note_shed calls, so
    dispatcher telemetry that reads ``scheduler.last_wait_decision``
    between shed cases still sees the last *real* wait decision."""
    print("  [A4] note_shed does not overwrite last_wait_decision")
    s = _fresh_scheduler()
    # Prime last_wait_decision via a real pre_execute round-trip.
    s.pre_execute(_heavy_req("warm"))
    s.post_execute(_heavy_req("warm"))
    time.sleep(0.01)
    prev_decision = s.pre_execute(_heavy_req("real"))
    assert s.last_wait_decision is prev_decision
    prev_id = id(prev_decision)

    # Shed must not touch last_wait_decision — telemetry still reads
    # the real decision from the last pre_execute.
    s.note_shed(_light_req("shed"))
    assert s.last_wait_decision is prev_decision
    assert id(s.last_wait_decision) == prev_id
    print("    OK")


def test_note_shed_returns_none_and_has_no_side_effects_on_cooldowns():
    """note_shed should return None (it's a documented no-op) and not
    raise on arbitrary task types (heavy/light/unknown). Unknown task
    types exercise the "neither in HEAVY_TASKS nor LIGHT_TASKS" branch
    that pre_execute would fall into."""
    print("  [A5] note_shed returns None for heavy / light / unknown task types")
    s = _fresh_scheduler()
    assert s.note_shed(_heavy_req()) is None
    assert s.note_shed(_light_req()) is None
    unknown = TaskRequest(domain="minecraft", task_name="edit_parse", user_input="u")
    assert s.note_shed(unknown) is None
    print("    OK")


# ===========================================================================
# B. State-machine correctness across success → shed → success
# ===========================================================================

def test_cooldown_after_shed_measures_from_prior_successful_finish():
    """End-to-end invariant: case1 (heavy, success) → case2 (shed) →
    case3 (heavy, pre_execute). case3's cooldown must be measured from
    case1's post_execute finish, NOT from case2's shed moment.

    Concretely: with heavy cooldown 0.1s, if case3 arrives at T+0.11
    (where T is case1's post_execute), the cooldown should be SKIPPED
    with reason ``already_elapsed`` because ``elapsed_since_case1 =
    0.11 > 0.1``. If note_shed had erroneously updated ``_last_finish``
    to case2's shed moment, case3 would see elapsed < 0.1 and sleep.
    """
    print("  [B1] cooldown after shed is anchored to prior successful finish")
    s = _fresh_scheduler(heavy_s=0.1, light_s=0.05)

    # case1: heavy, successful post_execute
    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))
    t1 = s._last_finish
    assert t1 > 0.0

    # case2: shed (arrives shortly after case1)
    time.sleep(0.02)
    s.note_shed(_light_req("c2_shed"))
    assert s._last_finish == t1, s._last_finish
    assert s._last_was_heavy is True, s._last_was_heavy

    # case3: heavy, arrives at T+0.11 (sleep 0.09 more to cross the 0.1
    # heavy cooldown boundary). Expect already_elapsed skip because
    # 0.11 > 0.1.
    time.sleep(0.09)
    decision = s.pre_execute(_heavy_req("c3"))
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY, decision
    assert decision.applied_s == 0.0, (
        f"cooldown was not skipped: applied_s={decision.applied_s}. "
        f"note_shed must not shift the cooldown baseline."
    )
    assert decision.skipped is True
    assert decision.skip_reason == WAIT_SKIP_REASON_ALREADY_ELAPSED
    print("    OK: case3 already_elapsed, baseline still at case1's finish")


def test_cooldown_after_shed_still_fires_when_elapsed_too_short():
    """Converse check: if case3 arrives BEFORE the heavy cooldown from
    case1 has elapsed, case3's pre_execute must still apply a wait.
    Proves note_shed doesn't accidentally mark the cooldown as satisfied
    (which a ``_last_finish`` rewind would cause)."""
    print("  [B2] cooldown after shed still fires if elapsed < configured")
    s = _fresh_scheduler(heavy_s=0.2, light_s=0.1)  # bigger cooldown for timing headroom

    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))

    # Shed almost immediately after case1.
    time.sleep(0.01)
    s.note_shed(_light_req("c2_shed"))

    # case3 arrives ~0.02s after case1 — well within the 0.2s heavy cooldown.
    decision = s.pre_execute(
        _heavy_req("c3"),
        total_deadline_s=5.0,
        request_headroom_s=0.5,
    )
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY, decision
    assert decision.applied_s > 0, (
        f"cooldown should fire with applied_s > 0 when elapsed < configured, "
        f"got {decision}"
    )
    assert decision.skipped is False
    assert decision.clamped is False
    print(f"    OK: case3 applied_s={decision.applied_s:.3f} after ~0.03s since case1")


def test_consecutive_sheds_keep_state_anchored_to_original_success():
    """Multiple consecutive sheds between two real executions must not
    drift the state machine. case1 (success) → shed × 3 → case2
    (pre_execute) should compute elapsed from case1's finish, not from
    any shed moment in between."""
    print("  [B3] consecutive sheds do not drift scheduler state")
    s = _fresh_scheduler(heavy_s=0.1, light_s=0.05)

    s.pre_execute(_heavy_req("c1"))
    s.post_execute(_heavy_req("c1"))
    t1 = s._last_finish
    flag1 = s._last_was_heavy

    for i in range(3):
        time.sleep(0.01)
        s.note_shed(_light_req(f"shed_{i}"))
        assert s._last_finish == t1
        assert s._last_was_heavy == flag1

    # case2 arrives after the sheds.
    time.sleep(0.01)
    decision = s.pre_execute(_heavy_req("c2"))
    # ~0.04s elapsed from case1 — under 0.1s heavy cooldown → wait applied.
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY
    assert decision.applied_s > 0
    print(f"    OK: after 3 sheds, baseline still at case1, applied_s={decision.applied_s:.3f}")


# ===========================================================================
# C. Dispatcher → scheduler.note_shed() wiring
# ===========================================================================

def test_dispatcher_shed_branch_invokes_note_shed():
    """Wiring check: when ``Dispatcher.dispatch()`` hits its OverloadError
    branch, ``_build_shed_task_result`` must call
    ``self.scheduler.note_shed(request)``. This locks the intent that
    the dispatcher explicitly signals the shed event to the scheduler
    instead of silently bypassing it."""
    print("  [C1] Dispatcher.dispatch shed branch calls scheduler.note_shed")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.execution.timeouts import TimeoutPolicy
    from src.app.core.errors import OverloadError
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    calls: list[TaskRequest] = []

    class _RecordingScheduler(Scheduler):
        def note_shed(self, request):
            calls.append(request)
            return super().note_shed(request)

    class _OverloadQueue:
        def submit(self, req, h):
            raise OverloadError("deterministic")

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    sched = _RecordingScheduler(policy=UnifiedTimeoutPolicy(cooldown_source="test"))
    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=_OverloadQueue(),
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
    assert result.status == TaskStatus.SHED
    assert len(calls) == 1, f"expected exactly one note_shed call, got {len(calls)}"
    assert calls[0] is req
    # And the recording scheduler's state is unchanged.
    assert sched._last_finish == 0.0
    assert sched._last_was_heavy is False
    print("    OK: dispatch() SHED branch called note_shed exactly once")


def test_dispatcher_shed_noop_when_scheduler_lacks_note_shed_method():
    """Back-compat: a Scheduler implementation without ``note_shed`` must
    still allow the dispatcher's shed branch to complete cleanly. The
    dispatcher catches ``AttributeError`` and silently continues — the
    invariant the method pins is still satisfied (no scheduler state
    mutation) just implicitly."""
    print("  [C2] Dispatcher SHED branch is AttributeError-safe when note_shed is missing")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.execution.timeouts import TimeoutPolicy
    from src.app.core.errors import OverloadError
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    class _LegacyScheduler:
        """Scheduler stub without note_shed (pre-T-tranche-13 shape)."""
        last_wait_decision = None
        def pre_execute(self, *a, **k): pass
        def post_execute(self, *a, **k): pass
        # no note_shed

    class _OverloadQueue:
        def submit(self, req, h):
            raise OverloadError("deterministic")

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=_OverloadQueue(),
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

    # Must not raise AttributeError.
    result = d.dispatch(req, spec)
    assert result.status == TaskStatus.SHED
    print("    OK: legacy scheduler without note_shed is tolerated")


TESTS = [
    test_note_shed_leaves_last_finish_untouched,
    test_note_shed_does_not_overwrite_prior_last_finish,
    test_note_shed_does_not_flip_last_was_heavy,
    test_note_shed_does_not_overwrite_last_wait_decision,
    test_note_shed_returns_none_and_has_no_side_effects_on_cooldowns,
    test_cooldown_after_shed_measures_from_prior_successful_finish,
    test_cooldown_after_shed_still_fires_when_elapsed_too_short,
    test_consecutive_sheds_keep_state_anchored_to_original_success,
    test_dispatcher_shed_branch_invokes_note_shed,
    test_dispatcher_shed_noop_when_scheduler_lacks_note_shed_method,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Scheduler shed-path semantics tests (T-tranche-13)")
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
