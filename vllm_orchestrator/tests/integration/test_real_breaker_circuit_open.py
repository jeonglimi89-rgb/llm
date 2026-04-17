"""
test_real_breaker_circuit_open.py — integration proof that the
``CircuitOpenError`` path in ``Dispatcher._execute`` is exercised by a
**real** ``CircuitBreaker`` state transition, not just a synthetic
``_CircuitOpenLLM`` stub.

Background
==========
T-tranche-14 (2026-04-09) wired ``Scheduler.note_circuit_open()`` into
``Dispatcher._execute`` and pinned the scheduler-state invariants with
12 unit tests using a synthetic LLM that always raises
``CircuitOpenError``. The remaining risk was that those tests never
exercised the actual ``CircuitBreaker`` state transitions
(closed → open via natural ``record_failure()`` calls), so a regression
in the breaker-to-LLMClient-to-Dispatcher wiring would not be caught.

T-tranche-15 (2026-04-09) closes that risk with **real breaker-trip
integration tests**:

  - **Test A** drives 3 failing dispatches via ``AlwaysFailAdapter`` +
    ``max_retries=0`` so the ``CircuitBreaker`` (``fail_threshold=3``)
    transitions to ``open`` *by natural state progression*, then
    asserts the 4th dispatch genuinely hits the dispatcher's
    ``CircuitOpenError`` branch (adapter never called on case 4).

  - **Test B** wraps the ``Scheduler`` with a recording subclass and
    proves ``note_circuit_open`` fires **exactly once** on the 4th
    (real circuit-open) dispatch, and never on the 3 leading parse-fail
    dispatches.

  - **Test C** captures ``_last_finish`` / ``_last_was_heavy`` before
    and after the 4th dispatch and asserts the baseline stays anchored
    to the 3rd dispatch's ``post_execute`` (the parse-fail branch's
    `post_execute` call is the most recent state-advancing event).

  - **Test D** drives ``run_export`` with 4 HR cases against
    ``AlwaysFailAdapter`` + ``max_retries=0`` so the default
    ``CircuitBreaker`` inside ``run_export`` naturally trips on case 3,
    and the 4th case hits circuit-open. Asserts the on-disk
    ``export_run_report.json`` carries the circuit-open case's
    semantic fields (scheduler entry in ``cooldown_decisions``,
    ``retry_decision_reason == "circuit_open"``, etc.).

Role separation
===============
- ``tests/unit/test_scheduler_circuit_open_semantics.py``: **synthetic**
  ``_CircuitOpenLLM`` stub; pins ``note_circuit_open`` invariants and
  dispatcher wiring at the method level.
- ``tests/integration/test_real_breaker_circuit_open.py`` (this file):
  **real** ``CircuitBreaker`` state transitions; integration proof
  that the full breaker-to-LLMClient-to-Dispatcher-to-Scheduler-to-
  artifact chain actually works end-to-end.
"""
from __future__ import annotations

import json
import shutil
import sys
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.core.contracts import TaskRequest
from src.app.core.enums import TaskStatus
from src.app.domain.registry import TaskSpec
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import (
    TimeoutPolicy,
    UnifiedTimeoutPolicy,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_KIND_FALLBACK_RETRY,
)
from src.app.llm.client import LLMClient, RETRY_REASON_CIRCUIT_OPEN
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.observability.health_registry import HealthRegistry
from src.app.orchestration.dispatcher import Dispatcher


# Load the export script as a module (same pattern as test_export_hardening.py).
_SPEC = importlib.util.spec_from_file_location(
    "ehr_real_breaker", str(_ROOT / "scripts" / "export_human_review.py"),
)
ehr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ehr)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _AlwaysFailAdapter:
    """Adapter that raises ConnectionError on every generate call.

    With ``max_retries=0``, each ``extract_slots`` call exhausts in a
    single failed attempt and calls ``circuit.record_failure()`` exactly
    once. Three such calls naturally trip a ``CircuitBreaker`` with
    ``fail_threshold=3`` into the ``open`` state.
    """
    provider_name = "always-fail"

    def __init__(self):
        self.calls = 0
        self.live = True

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        raise ConnectionError("simulated transport failure (T-tranche-15)")


class _RecordingScheduler(Scheduler):
    """Scheduler subclass that records note_circuit_open / note_shed /
    post_execute / pre_execute invocations so tests can assert which
    dispatcher branches actually fired."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.circuit_open_calls: list[TaskRequest] = []
        self.shed_calls: list[TaskRequest] = []
        self.post_execute_calls: list[TaskRequest] = []

    def note_circuit_open(self, request):
        self.circuit_open_calls.append(request)
        return super().note_circuit_open(request)

    def note_shed(self, request):
        self.shed_calls.append(request)
        return super().note_shed(request)

    def post_execute(self, request):
        self.post_execute_calls.append(request)
        return super().post_execute(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEAVY_TASK_TYPE = "builder.requirement_parse"   # in HEAVY_TASKS


def _heavy_request(label: str) -> TaskRequest:
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


def _build_dispatcher(
    breaker: CircuitBreaker,
    scheduler: Scheduler,
    fallback: DegradedModeHandler | None = None,
) -> tuple[Dispatcher, _AlwaysFailAdapter, LLMClient]:
    adapter = _AlwaysFailAdapter()
    health = HealthRegistry()
    llm = LLMClient(adapter, health, breaker, max_retries=0)
    queue = QueueManager(max_concurrency=1, max_depth=10)
    dispatcher = Dispatcher(
        llm_client=llm,
        queue=queue,
        scheduler=scheduler,
        timeouts=TimeoutPolicy(),
        fallback=fallback,
    )
    return dispatcher, adapter, llm


def _temp_outdir(name: str) -> Path:
    p = _ROOT / "runtime" / "_test_real_breaker_circuit_open" / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ===========================================================================
# Test A — real breaker trips from closed → open via 3 natural failures
# ===========================================================================

def test_three_failing_dispatches_trip_breaker_and_fourth_hits_circuit_open_path():
    """Run 3 dispatches against ``AlwaysFailAdapter`` with
    ``max_retries=0``. The ``CircuitBreaker`` must transition
    ``closed → open`` naturally via its own ``record_failure`` path on
    the 3rd call. The 4th dispatch must then enter the dispatcher's
    ``CircuitOpenError`` branch — asserted via:
      - ``circuit.state == "open"`` after case 3
      - ``adapter.calls == 3`` after case 4 (NOT 4 — the 4th dispatch
        must never touch the adapter because the breaker short-circuits
        inside ``LLMClient.extract_slots``)
      - case 4's ``TaskResult.errors`` mentions "Circuit breaker open"
      - case 4's ``retry_decision.retry_decision_reason == "circuit_open"``
    """
    print("  [A] 3 natural failures trip breaker, 4th dispatch hits circuit-open path")
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=3600.0)
    # Zero scheduler cooldowns → tests run in milliseconds.
    sched = Scheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    dispatcher, adapter, _ = _build_dispatcher(breaker, sched)
    spec = _heavy_spec()

    # Cases 1-3: each dispatch fails, breaker records failure, transitions
    # to open only after the 3rd.
    for i in range(1, 4):
        result = dispatcher.dispatch(_heavy_request(f"c{i}"), spec)
        assert result.status == TaskStatus.ERROR, (i, result.status)
        # Parse-fail branch — NOT circuit-open, because the adapter did
        # get called and raised ConnectionError. Verify the error message
        # does NOT say "Circuit breaker open" on cases 1-3.
        errors_joined = " ".join(result.errors or [])
        assert "Circuit breaker open" not in errors_joined, (
            f"case {i} hit circuit-open branch prematurely; errors={result.errors}"
        )

    # After 3 failures, breaker state is "open".
    assert breaker.state == "open", breaker.snapshot()
    # Adapter was called exactly 3 times (once per dispatch, no retries).
    assert adapter.calls == 3, adapter.calls

    # Case 4: circuit is now open. LLMClient's allow() check raises
    # CircuitOpenError BEFORE the adapter is touched.
    result4 = dispatcher.dispatch(_heavy_request("c4"), spec)

    # Adapter must NOT have been called again — the hallmark of the
    # real circuit-open path (vs just another parse-fail).
    assert adapter.calls == 3, (
        f"breaker failed to short-circuit: adapter was called {adapter.calls - 3} "
        f"extra times on case 4"
    )
    assert result4.status == TaskStatus.ERROR
    assert any("Circuit breaker open" in e for e in (result4.errors or [])), result4.errors

    # The LLMClient tagged the per-call retry_decision with the
    # canonical circuit_open reason. This flows into result4.retry_decision
    # via the dispatcher's rd_dict merge.
    rd = result4.retry_decision
    assert rd is not None
    assert rd.get("retry_decision_reason") == RETRY_REASON_CIRCUIT_OPEN, rd
    print(
        f"    OK: breaker state={breaker.state}, failures={breaker._failures}, "
        f"adapter.calls={adapter.calls}, reason={rd.get('retry_decision_reason')!r}"
    )


# ===========================================================================
# Test B — note_circuit_open fires exactly once on the real-trip case
# ===========================================================================

def test_note_circuit_open_fires_exactly_once_on_real_breaker_trip():
    """Using a ``_RecordingScheduler`` subclass, verify that
    ``note_circuit_open`` is NOT called on cases 1-3 (they're parse-fail
    paths that call ``post_execute`` instead) and IS called exactly
    once on case 4 (the real circuit-open path)."""
    print("  [B] note_circuit_open fires exactly once on case 4, zero times on cases 1-3")
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=3600.0)
    sched = _RecordingScheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    dispatcher, _, _ = _build_dispatcher(breaker, sched)
    spec = _heavy_spec()

    for i in range(1, 4):
        dispatcher.dispatch(_heavy_request(f"c{i}"), spec)
        # Cases 1-3 all go through parse-fail → post_execute fires each
        # time, note_circuit_open does NOT fire.
        assert len(sched.circuit_open_calls) == 0, (
            f"after case {i}, note_circuit_open unexpectedly called: "
            f"{sched.circuit_open_calls}"
        )
        assert len(sched.post_execute_calls) == i, sched.post_execute_calls
        assert len(sched.shed_calls) == 0, sched.shed_calls

    assert breaker.state == "open"

    # Case 4 — the real circuit-open path.
    req4 = _heavy_request("c4")
    dispatcher.dispatch(req4, spec)

    assert len(sched.circuit_open_calls) == 1, sched.circuit_open_calls
    assert sched.circuit_open_calls[0] is req4
    # post_execute must NOT have fired on case 4 (the whole point of the
    # circuit-open path is that it skips post_execute).
    assert len(sched.post_execute_calls) == 3, (
        f"post_execute fired on circuit-open case 4 — baseline would drift! "
        f"calls={len(sched.post_execute_calls)}"
    )
    # And note_shed was never called (this is not the shed path).
    assert len(sched.shed_calls) == 0
    print(
        f"    OK: circuit_open_calls=1, post_execute_calls=3 (cases 1-3 only), "
        f"shed_calls=0"
    )


# ===========================================================================
# Test C — scheduler baseline stays anchored across the real breaker trip
# ===========================================================================

def test_scheduler_baseline_anchored_across_real_circuit_open():
    """Capture ``_last_finish`` and ``_last_was_heavy`` after case 3's
    parse-fail ``post_execute``. The real circuit-open case 4 must not
    advance either field — ``_last_finish == t3`` and
    ``_last_was_heavy == True`` after case 4."""
    print("  [C] _last_finish / _last_was_heavy anchored through real circuit-open")
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=3600.0)
    sched = Scheduler(policy=UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        cooldown_source="test",
    ))
    dispatcher, _, _ = _build_dispatcher(breaker, sched)
    spec = _heavy_spec()

    for i in range(1, 4):
        dispatcher.dispatch(_heavy_request(f"c{i}"), spec)

    # After 3 parse-fails, scheduler has advanced _last_finish each time
    # (via post_execute in the parse-fail branch). Capture the baseline.
    t3 = sched._last_finish
    flag3 = sched._last_was_heavy
    assert t3 > 0.0, sched._last_finish
    assert flag3 is True, sched._last_was_heavy
    assert breaker.state == "open"

    # Case 4: real circuit-open.
    dispatcher.dispatch(_heavy_request("c4"), spec)

    # Invariant: baseline must be unchanged.
    assert sched._last_finish == t3, (
        f"baseline drifted through circuit-open: {sched._last_finish} != {t3}"
    )
    assert sched._last_was_heavy is True, (
        "heavy flag flipped across circuit-open — should stay anchored"
    )
    # And last_wait_decision IS updated — it's the case 4 pre_execute
    # decision (even though pre_execute probably skipped because scheduler
    # cooldowns are 0.0 in this test, it still writes a WaitDecision).
    assert sched.last_wait_decision is not None
    print(
        f"    OK: _last_finish anchored at {t3:.6f}, "
        f"last_wait_decision.kind={sched.last_wait_decision.kind}"
    )


# ===========================================================================
# Test D — run_export E2E: circuit-open case 4 surfaces scheduler + reason
#          fields in export_run_report.json
# ===========================================================================

def test_run_export_circuit_open_case_surfaces_scheduler_wait_in_artifact():
    """Drive ``run_export`` with 4 HR cases against ``AlwaysFailAdapter``
    and ``max_retries=0``. The default ``CircuitBreaker`` constructed
    inside ``run_export`` trips on case 3; case 4 enters the real
    circuit-open path. Read the on-disk ``export_run_report.json`` and
    assert the 4th case's ``cooldown_decisions`` carries:

      - a scheduler entry (kind ∈ {scheduler_heavy, scheduler_light})
        with the canonical 7-key WaitDecision shape
      - the operator-supplied ``source`` label
      - ``retry_decision_reason == "circuit_open"`` at the case level
      - a fallback_retry entry (from the dispatcher's T-tranche-10
        wiring in the CircuitOpenError branch)

    This is the **artifact-level** counterpart to tests A-C; it proves
    the full chain
    `real breaker → LLMClient → Dispatcher → Scheduler → fallback →
    CaseTelemetry → RunTelemetry → write_run_report → disk → json.loads`
    works end-to-end on the circuit-open path.
    """
    print("  [D] run_export circuit-open case 4 carries scheduler + reason in artifact")
    out = _temp_outdir("run_export_real_breaker")
    try:
        # 4 cases — cases 1..3 fail and trip the breaker; case 4 hits
        # the circuit-open path inside ``_execute``.
        cases = [
            ("builder", "requirement_parse", "case-1"),
            ("builder", "requirement_parse", "case-2"),
            ("builder", "requirement_parse", "case-3"),
            ("builder", "requirement_parse", "case-4"),
        ]
        adapter = _AlwaysFailAdapter()
        ehr.run_export(
            cases=cases,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=0,                    # each dispatch = exactly 1 adapter call
            total_deadline_s=30.0,            # generous: no budget exhaustion
            transport_retry_cooldown_s=0.0,   # isolate transport retries
            scheduler_cooldown_heavy_s=0.05,  # small but > 0 → visible scheduler wait
            scheduler_cooldown_light_s=0.0,
            fallback_retry_delay_s=0.11,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert len(rr["cases"]) == 4, len(rr["cases"])

        # Case 4 must be the circuit-open case. Identifying it:
        # - cases 1-3 are parse-fail → retry_decision_reason ==
        #   "transport_retry_exhausted" (because max_retries=0 + adapter
        #   raised, so LLMClient exited via the transport branch)
        # - case 4 is circuit-open → retry_decision_reason == "circuit_open"
        case4 = rr["cases"][3]
        assert case4["retry_decision_reason"] == RETRY_REASON_CIRCUIT_OPEN, (
            f"case 4 should be circuit-open; got reason="
            f"{case4['retry_decision_reason']!r}"
        )
        assert case4["final_status"] == "failed", case4

        # And cases 1-3 are NOT circuit-open — they're transport exhaustions.
        for i in range(3):
            c = rr["cases"][i]
            assert c["retry_decision_reason"] != RETRY_REASON_CIRCUIT_OPEN, (
                f"case {i + 1} should NOT be circuit-open; got reason="
                f"{c['retry_decision_reason']!r}"
            )

        # Case 4's cooldown_decisions must contain a scheduler entry
        # (scheduler_heavy because cases 1-3 were heavy tasks, leaving
        # _last_was_heavy=True for case 4's pre_execute).
        cds = case4["cooldown_decisions"]
        assert isinstance(cds, list) and len(cds) >= 1, cds

        scheduler_entries = [
            cd for cd in cds
            if cd.get("kind") in (WAIT_KIND_SCHEDULER_HEAVY, WAIT_KIND_SCHEDULER_LIGHT)
        ]
        assert len(scheduler_entries) >= 1, (
            f"case 4 must carry a scheduler entry in cooldown_decisions; "
            f"got kinds={[cd.get('kind') for cd in cds]}"
        )
        sched_entry = scheduler_entries[0]
        # Canonical 7-key shape
        assert set(sched_entry.keys()) == {
            "kind", "configured_s", "applied_s",
            "clamped", "skipped", "skip_reason", "source",
        }, sched_entry
        assert sched_entry["kind"] == WAIT_KIND_SCHEDULER_HEAVY, sched_entry
        assert sched_entry["source"] == "test", sched_entry
        # case4 followed 3 heavy cases with scheduler_cooldown_heavy_s=0.05;
        # the elapsed time is most likely > 0.05 because of adapter call
        # overhead, so the entry is either "already_elapsed" skipped or
        # a small fully-honored wait. Either is acceptable — we assert
        # internal consistency: skipped → applied_s==0, applied_s>=0.
        assert sched_entry["applied_s"] >= 0.0
        if sched_entry["skipped"]:
            assert sched_entry["applied_s"] == 0.0

        # And the dispatcher's T-tranche-10 fallback merge on the
        # CircuitOpenError branch deposited a fallback_retry entry too.
        fallback_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY]
        assert len(fallback_entries) == 1, (
            f"expected 1 fallback_retry entry on circuit-open case, got "
            f"{[cd.get('kind') for cd in cds]}"
        )
        fb = fallback_entries[0]
        assert fb["configured_s"] == 0.11, fb
        assert fb["applied_s"] == 0.0, fb
        assert fb["skipped"] is True, fb
        assert fb["skip_reason"] == "zero_configured", fb
        assert fb["source"] == "test", fb

        print(
            f"    OK: case4 reason=circuit_open, scheduler kind={sched_entry['kind']} "
            f"skipped={sched_entry['skipped']}, fallback configured_s=0.11"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


TESTS = [
    test_three_failing_dispatches_trip_breaker_and_fourth_hits_circuit_open_path,
    test_note_circuit_open_fires_exactly_once_on_real_breaker_trip,
    test_scheduler_baseline_anchored_across_real_circuit_open,
    test_run_export_circuit_open_case_surfaces_scheduler_wait_in_artifact,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Real CircuitBreaker circuit-open integration tests (T-tranche-15)")
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
