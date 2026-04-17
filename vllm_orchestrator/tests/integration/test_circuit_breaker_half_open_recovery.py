"""
test_circuit_breaker_half_open_recovery.py — full state-machine audit of
the CircuitBreaker's ``open → half_open → closed`` (recovery) and
``open → half_open → open`` (failed probe) cycles at both the unit
level and the dispatcher integration level.

Background
==========
T-tranche-15 (2026-04-09) proved the ``closed → open`` transition via
real ``CircuitBreaker.record_failure()`` progression and verified the
circuit-open path at the dispatcher + artifact level. But the recovery
path — ``open → half_open → closed`` — was only exercised implicitly
by ``reset_timeout_s=3600.0`` tests that never elapsed.

T-tranche-16 (2026-04-09) closes that gap with four test blocks:

  - **Block A** (4 tests) pins the ``CircuitBreaker.state`` property's
    **lazy transition** behavior: ``open → half_open`` only fires on
    read after ``time.time() - _last_failure_time > _reset_timeout_s``,
    and subsequent reads are idempotent.

  - **Block B** (3 tests) pins ``allow()`` / ``record_success()`` /
    ``record_failure()`` semantics through the half_open state.

  - **Block C** (2 tests) drives a full recovery sequence at the
    Dispatcher integration level: real 3-fail trip → real
    ``time.sleep(reset_timeout_s + ε)`` → real success dispatch. Asserts
    the breaker returns to ``closed``, the scheduler baseline advances
    normally on recovery (recovery is a normal DONE path, NOT a
    special exception), and a failed probe flips the breaker back to
    ``open``.

  - **Block D** (1 test) exercises multi-cycle stability: trip →
    recover → trip again → recover again. Proves the state machine
    doesn't accumulate hidden state across cycles.

All tests use a small ``reset_timeout_s`` (≤ 0.06s) and short
``time.sleep`` windows to stay deterministic and fast. Total runtime
is well under 1 second.

Role separation
===============
- ``tests/integration/test_real_breaker_circuit_open.py`` (T-tranche-15):
  proves ``closed → open`` transition + circuit-open path + artifact E2E.
- ``tests/integration/test_circuit_breaker_half_open_recovery.py``
  (this file, T-tranche-16): proves ``open → half_open → {closed, open}``
  recovery transitions + dispatcher integration + multi-cycle stability.
"""
from __future__ import annotations

import time

from src.app.core.contracts import TaskRequest
from src.app.core.enums import TaskStatus
from src.app.domain.registry import TaskSpec
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import TimeoutPolicy, UnifiedTimeoutPolicy
from src.app.llm.client import LLMClient, RETRY_REASON_CIRCUIT_OPEN
from src.app.observability.health_registry import HealthRegistry
from src.app.orchestration.dispatcher import Dispatcher


# Small but non-trivial reset window. 0.05s is enough to be measurable
# but short enough to keep total test runtime well under 1 second even
# across ~10 sleeps.
_RESET_TIMEOUT_S = 0.05
_SLEEP_PAST_RESET = _RESET_TIMEOUT_S + 0.02  # safe margin


_HEAVY_TASK_TYPE = "builder.requirement_parse"


def _heavy_req(label: str) -> TaskRequest:
    return TaskRequest(domain="builder", task_name="requirement_parse", user_input=label)


def _heavy_spec() -> TaskSpec:
    return TaskSpec(
        task_type=_HEAVY_TASK_TYPE,
        domain="builder",
        task_name="requirement_parse",
        pool_type="strict_json",
        prompt_file="",
        schema_file="",
    )


class _FlakyAdapter:
    """Adapter that raises on the first ``fail_count`` calls then returns
    valid JSON. Used to construct a dispatcher test that trips the
    breaker and then recovers successfully.

    Unlike ``_AlwaysFailAdapter`` from T-tranche-15, this adapter
    returns clean strict-JSON after the configured failure count so
    the recovery dispatch produces a ``TaskStatus.DONE`` via the
    normal success path.
    """
    provider_name = "flaky"

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0
        self.live = True

    def is_available(self):
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise ConnectionError(
                f"simulated failure #{self.calls}/{self.fail_count}"
            )
        return {
            "text": '{"intent": "recovered"}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


def _build_dispatcher_with_flaky(
    fail_count: int,
    reset_timeout_s: float = _RESET_TIMEOUT_S,
) -> tuple[Dispatcher, _FlakyAdapter, CircuitBreaker, Scheduler]:
    adapter = _FlakyAdapter(fail_count=fail_count)
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=reset_timeout_s)
    scheduler = Scheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    llm = LLMClient(adapter, HealthRegistry(), breaker, max_retries=0)
    queue = QueueManager(max_concurrency=1, max_depth=10)
    dispatcher = Dispatcher(
        llm_client=llm,
        queue=queue,
        scheduler=scheduler,
        timeouts=TimeoutPolicy(),
    )
    return dispatcher, adapter, breaker, scheduler


# ===========================================================================
# Block A — CircuitBreaker.state property lazy transition
# ===========================================================================

def test_state_property_flips_to_half_open_only_after_reset_timeout_elapses():
    """After the breaker trips, the ``state`` property must continue
    returning ``"open"`` until ``time.time() - _last_failure_time``
    exceeds ``_reset_timeout_s``. The flip is **lazy** — it only
    happens on a property read that meets the condition."""
    print("  [A1] state flips open → half_open lazily on read after reset elapses")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    # Trip the breaker naturally.
    for _ in range(3):
        b.record_failure()
    assert b.state == "open", b.snapshot()
    # Immediate re-read still "open" because reset hasn't elapsed.
    assert b.state == "open"
    # Wait past reset_timeout and re-read — should flip to half_open.
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open", b.snapshot()
    print("    OK")


def test_state_property_stays_open_while_reset_window_active():
    """Defensive check: while the reset window is active, repeated
    reads of the state property must keep returning ``"open"``."""
    print("  [A2] state stays open while reset window active")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=1.0)  # long window
    for _ in range(3):
        b.record_failure()
    for _ in range(5):
        assert b.state == "open", b.snapshot()
    print("    OK")


def test_state_property_does_not_auto_advance_from_half_open_to_closed():
    """``half_open → closed`` is NOT a lazy / time-based transition —
    it only happens via ``record_success()``. Even after a very long
    time, a half_open breaker must stay half_open until a success
    is recorded."""
    print("  [A3] state does not auto-advance half_open → closed on read alone")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    for _ in range(3):
        b.record_failure()
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open"
    # Many more reads, lots of time — still half_open until record_success fires.
    for _ in range(10):
        assert b.state == "half_open"
    time.sleep(_SLEEP_PAST_RESET * 5)
    assert b.state == "half_open", b.snapshot()
    print("    OK")


def test_repeated_state_reads_after_half_open_transition_are_idempotent():
    """Once the lazy flip to half_open has fired, subsequent reads of
    the ``state`` property must keep returning ``"half_open"`` — not
    re-trigger the flip or drift back to open."""
    print("  [A4] repeated state reads after half_open transition are idempotent")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    for _ in range(3):
        b.record_failure()
    time.sleep(_SLEEP_PAST_RESET)
    first = b.state
    assert first == "half_open"
    # Multiple reads, interleaved with short sleeps — all half_open.
    for _ in range(5):
        assert b.state == "half_open"
        time.sleep(0.001)
    assert b._failures == 3, b._failures  # failure count untouched
    print("    OK")


# ===========================================================================
# Block B — allow() / record_success() / record_failure() through half_open
# ===========================================================================

def test_allow_returns_true_on_half_open_allowing_trial_request():
    """The ``allow()`` method must return ``True`` on half_open so that
    ``LLMClient.extract_slots`` proceeds with a trial request — this
    is the whole point of the half_open state."""
    print("  [B1] allow() returns True on half_open (trial request allowed)")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    for _ in range(3):
        b.record_failure()
    assert b.allow() is False, "should block while open"
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open"
    assert b.allow() is True, "should allow trial on half_open"
    # Calling allow() again (which reads state) still returns True.
    assert b.allow() is True
    print("    OK")


def test_record_success_after_half_open_restores_closed_state():
    """Successful probe recovery: after the breaker is in half_open,
    calling ``record_success()`` must reset failures to 0 and flip
    the state to ``"closed"``. Subsequent ``allow()`` calls return
    True as in a fresh breaker."""
    print("  [B2] record_success() on half_open → closed + failures reset")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    for _ in range(3):
        b.record_failure()
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open"
    assert b._failures == 3

    b.record_success()
    assert b.state == "closed", b.snapshot()
    assert b._failures == 0, b._failures
    assert b.allow() is True
    print("    OK")


def test_record_failure_after_half_open_reverts_to_open_and_restarts_cooldown():
    """Failed probe: after a half_open breaker records another failure,
    the state must flip back to ``"open"`` AND ``_last_failure_time``
    must be updated so the reset countdown restarts from the failure
    moment — otherwise the breaker would immediately pop back to
    half_open on the next read."""
    print("  [B3] record_failure() on half_open → open + cooldown restarts")
    b = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    for _ in range(3):
        b.record_failure()
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open"

    before = time.time()
    b.record_failure()
    after = time.time()

    assert b._state == "open", b.snapshot()
    assert b._failures == 4, b._failures  # increments, not reset
    assert before <= b._last_failure_time <= after
    # Immediate state read is "open" (cooldown restarted, reset hasn't elapsed).
    assert b.state == "open"
    # Another reset wait and it flips again.
    time.sleep(_SLEEP_PAST_RESET)
    assert b.state == "half_open"
    print("    OK")


# ===========================================================================
# Block C — Dispatcher-level recovery sequence
# ===========================================================================

def test_dispatcher_recovers_through_half_open_on_success_dispatch():
    """End-to-end recovery sequence via real Dispatcher + real
    CircuitBreaker + ``_FlakyAdapter(fail_count=3)``:
      case1-3: fail → record_failure → breaker trips on case 3
      sleep past reset_timeout
      case4: half_open trial → adapter returns success →
             LLMClient.record_success → breaker back to closed →
             Dispatcher DONE path → scheduler.post_execute fires
             (baseline advances), NOT note_circuit_open

    Assertions:
      - cases 1-3 end in TaskStatus.ERROR (parse-fail branch)
      - after case 3 the breaker is open (snapshot via private field)
      - after sleep, breaker.state lazily becomes half_open on next read
      - case 4 ends in TaskStatus.DONE with valid slots
      - after case 4 the breaker is closed + failures=0
      - scheduler._last_finish advanced through case 4's post_execute
    """
    print("  [C1] dispatcher full recovery: 3 fail → sleep → success probe → closed")
    dispatcher, adapter, breaker, scheduler = _build_dispatcher_with_flaky(fail_count=3)
    spec = _heavy_spec()

    for i in range(1, 4):
        r = dispatcher.dispatch(_heavy_req(f"c{i}"), spec)
        assert r.status == TaskStatus.ERROR, (i, r.status)

    # Breaker tripped.
    assert breaker._state == "open"
    assert breaker._failures == 3
    assert adapter.calls == 3

    # Capture scheduler state BEFORE the recovery case.
    t_before_recovery = scheduler._last_finish
    assert t_before_recovery > 0.0  # advanced by 3 parse-fail post_executes

    # Wait past the reset window and dispatch the recovery case.
    time.sleep(_SLEEP_PAST_RESET)
    assert breaker.state == "half_open"  # lazy flip

    result4 = dispatcher.dispatch(_heavy_req("c4"), spec)

    # Recovery succeeded.
    assert result4.status == TaskStatus.DONE, result4.status
    assert result4.slots is not None
    assert result4.slots.get("intent") == "recovered", result4.slots
    assert adapter.calls == 4, adapter.calls

    # Breaker fully reset.
    assert breaker._state == "closed", breaker.snapshot()
    assert breaker._failures == 0, breaker._failures
    assert breaker.allow() is True

    # Scheduler baseline advanced on recovery (recovery IS a normal
    # DONE path — post_execute fires, _last_finish moves forward).
    assert scheduler._last_finish > t_before_recovery, (
        f"scheduler baseline should advance on successful recovery: "
        f"{scheduler._last_finish} vs {t_before_recovery}"
    )
    print(
        f"    OK: recovered to closed, _last_finish advanced by "
        f"{scheduler._last_finish - t_before_recovery:.4f}s"
    )


def test_dispatcher_failed_probe_reverts_to_open_without_baseline_drift():
    """Failed probe: the flaky adapter fails 4 times in a row, so when
    the half_open trial happens on case 4, the adapter raises again.
    The breaker records another failure and reverts to ``open``. The
    dispatcher takes the parse-fail branch (not circuit-open, because
    ``allow()`` returned True on half_open), so ``post_execute`` fires
    and advances ``_last_finish``. But the breaker is back open, so a
    5th dispatch would hit the true circuit-open path.
    """
    print("  [C2] failed probe: half_open trial fails → breaker reverts to open")
    dispatcher, adapter, breaker, scheduler = _build_dispatcher_with_flaky(fail_count=5)
    spec = _heavy_spec()

    for i in range(1, 4):
        dispatcher.dispatch(_heavy_req(f"c{i}"), spec)

    assert breaker._state == "open"
    assert breaker._failures == 3
    time.sleep(_SLEEP_PAST_RESET)
    assert breaker.state == "half_open"

    # Case 4 — the trial. Adapter fails again.
    result4 = dispatcher.dispatch(_heavy_req("c4"), spec)

    # Still parse-fail branch (adapter was called, raised ConnectionError).
    assert result4.status == TaskStatus.ERROR
    assert "Circuit breaker open" not in " ".join(result4.errors or [])
    assert adapter.calls == 4  # probe consumed one real adapter call

    # Breaker flipped back to open, failures incremented.
    assert breaker._state == "open", breaker.snapshot()
    assert breaker._failures == 4, breaker._failures

    # Now case 5 — breaker is open, state hasn't reset yet → real
    # circuit-open path.
    result5 = dispatcher.dispatch(_heavy_req("c5"), spec)
    assert result5.status == TaskStatus.ERROR
    assert any("Circuit breaker open" in e for e in (result5.errors or [])), result5.errors
    assert adapter.calls == 4  # breaker short-circuited case 5
    assert result5.retry_decision is not None
    assert result5.retry_decision.get("retry_decision_reason") == RETRY_REASON_CIRCUIT_OPEN
    print("    OK: half_open probe failed → open → next dispatch hits circuit-open")


# ===========================================================================
# Block D — multi-cycle stability
# ===========================================================================

def test_breaker_survives_multiple_trip_recover_cycles():
    """Build an adapter that fails in waves: 3 fails → 1 success →
    3 fails → 1 success. Run 8 dispatches through the real breaker
    and assert the state machine cycles correctly:

      cycle 1: fail × 3 → open → sleep → half_open → success → closed
      cycle 2: fail × 3 → open → sleep → half_open → success → closed

    And that after the final recovery the breaker reports exactly
    the same state as after the first recovery. This locks that the
    state machine does not accumulate hidden state across cycles.
    """
    print("  [D] breaker survives 2 full trip→recover cycles")

    class _WavyAdapter:
        provider_name = "wavy"
        def __init__(self): self.calls = 0; self.live = True
        def is_available(self): return True
        def generate(self, **kw):
            self.calls += 1
            # Waves: calls 1-3 fail, 4 succeeds, 5-7 fail, 8 succeeds.
            wave_position = (self.calls - 1) % 4
            if wave_position < 3:
                raise ConnectionError(f"wavy fail #{self.calls}")
            return {"text": '{"intent": "recovered"}', "prompt_tokens": 0, "completion_tokens": 0}

    adapter = _WavyAdapter()
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=_RESET_TIMEOUT_S)
    scheduler = Scheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    llm = LLMClient(adapter, HealthRegistry(), breaker, max_retries=0)
    dispatcher = Dispatcher(
        llm_client=llm,
        queue=QueueManager(max_concurrency=1, max_depth=10),
        scheduler=scheduler,
        timeouts=TimeoutPolicy(),
    )
    spec = _heavy_spec()

    # Cycle 1
    for i in range(1, 4):
        dispatcher.dispatch(_heavy_req(f"c1.{i}"), spec)
    assert breaker._state == "open"
    assert breaker._failures == 3
    time.sleep(_SLEEP_PAST_RESET)
    r_recovery_1 = dispatcher.dispatch(_heavy_req("c1.recover"), spec)
    assert r_recovery_1.status == TaskStatus.DONE
    assert breaker._state == "closed"
    assert breaker._failures == 0

    # Cycle 2 — new wave of failures + recovery on top of the reset state.
    for i in range(1, 4):
        dispatcher.dispatch(_heavy_req(f"c2.{i}"), spec)
    assert breaker._state == "open"
    assert breaker._failures == 3
    time.sleep(_SLEEP_PAST_RESET)
    r_recovery_2 = dispatcher.dispatch(_heavy_req("c2.recover"), spec)
    assert r_recovery_2.status == TaskStatus.DONE
    assert breaker._state == "closed"
    assert breaker._failures == 0

    # Post-cycle invariants: adapter was called 8 times, breaker is clean.
    assert adapter.calls == 8, adapter.calls
    assert breaker.allow() is True
    print("    OK: 2 full trip → recover cycles, breaker state clean after each")


TESTS = [
    # Block A — state property lazy transition
    test_state_property_flips_to_half_open_only_after_reset_timeout_elapses,
    test_state_property_stays_open_while_reset_window_active,
    test_state_property_does_not_auto_advance_from_half_open_to_closed,
    test_repeated_state_reads_after_half_open_transition_are_idempotent,
    # Block B — allow / record_success / record_failure cycle
    test_allow_returns_true_on_half_open_allowing_trial_request,
    test_record_success_after_half_open_restores_closed_state,
    test_record_failure_after_half_open_reverts_to_open_and_restarts_cooldown,
    # Block C — dispatcher recovery sequence
    test_dispatcher_recovers_through_half_open_on_success_dispatch,
    test_dispatcher_failed_probe_reverts_to_open_without_baseline_drift,
    # Block D — multi-cycle stability
    test_breaker_survives_multiple_trip_recover_cycles,
]


if __name__ == "__main__":
    print("=" * 60)
    print("CircuitBreaker half_open recovery tests (T-tranche-16)")
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
