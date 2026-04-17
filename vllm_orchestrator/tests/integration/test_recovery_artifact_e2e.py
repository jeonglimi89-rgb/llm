"""
test_recovery_artifact_e2e.py — artifact-level proof that a half_open
recovery success case is structurally identical to any ordinary DONE
case in ``export_run_report.json``, and that the scheduler baseline
anchors to the recovery case for the next steady-state dispatch.

Background
==========
T-tranche-15 proved the real ``closed → open`` transition at the
artifact level (circuit-open case lands in the JSON with
``retry_decision_reason == "circuit_open"``). T-tranche-16 proved the
``open → half_open → closed`` recovery at the dispatcher-object level
but stopped short of verifying the artifact shape — recovery cases
were never read back from disk JSON.

T-tranche-17 (2026-04-10) closes that artifact-level gap using a
phase-based harness enabled by a single minimal-invasive production
change: ``run_export`` now accepts an optional
``circuit_override: CircuitBreaker = None``. When omitted (the default
for every existing caller), behavior is identical — ``run_export``
still builds its own fresh ``CircuitBreaker()`` inside. When a test
passes a shared breaker, phase 1 can trip it and phase 2 can inherit
the tripped state after a short ``time.sleep``, so the full
``closed → open → half_open → closed → steady-state`` sequence can be
driven across two ``run_export`` calls while reading both artifacts
from disk.

Structure of a phase-based test
================================
  Phase 1 (trip):
    - cases = 3 failure cases
    - adapter = ``_FlakyAdapter(fail_count=3)`` (shared instance)
    - circuit = shared breaker with ``fail_threshold=3, reset_timeout_s=0.05``
    - Result: breaker state → "open", all 3 cases are parse-fails with
      ``retry_decision_reason == "transport_retry_exhausted"``.

  Sleep 0.07s past the reset window.

  Phase 2 (recovery + steady-state):
    - cases = 2 more cases (case 4 = recovery, case 5 = steady-state)
    - same shared adapter (calls 4 and 5 return valid JSON — calls 1-3
      already raised in phase 1)
    - same shared breaker (now lazy-flips to half_open on first read)
    - Result: case 4 is the half_open trial that succeeds → breaker
      flips back to "closed" via ``record_success()``; case 5 is a
      normal steady-state success. BOTH are structurally identical
      DONE cases in the artifact.

Crucial negative assertion
==========================
The artifact-level contract this tranche locks is that a recovery
case has **no** circuit-open markers and **no** fallback_retry entry
— recovery success is indistinguishable from an ordinary first
success. If a future refactor ever starts tagging recovery cases
specially (e.g. ``retry_decision_reason == "recovery_success"``), the
Block D negative-distinction test will fail and force an explicit
contract update.

Role separation
===============
- ``test_real_breaker_circuit_open.py`` (T-15): ``closed → open``
  transition + circuit-open artifact.
- ``test_circuit_breaker_half_open_recovery.py`` (T-16): state-machine
  correctness at the CircuitBreaker + Dispatcher object level
  (``open → half_open → {closed, open}``).
- ``test_recovery_artifact_e2e.py`` (T-17, this file): **artifact
  JSON proof** that recovery success = normal DONE across the full
  ``run_export → disk → json.loads`` chain, plus steady-state-after-
  recovery scheduler baseline behavior.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.timeouts import (
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_KIND_FALLBACK_RETRY,
)
from src.app.llm.client import (
    RETRY_REASON_INITIAL_SUCCESS,
    RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED,
    RETRY_REASON_CIRCUIT_OPEN,
)


# Load run_export as a module (same pattern as test_export_hardening.py).
_SPEC = importlib.util.spec_from_file_location(
    "ehr_recovery", str(_ROOT / "scripts" / "export_human_review.py"),
)
ehr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ehr)


# Timing: small but safe margin. Phase 2 starts after sleep elapses.
_RESET_TIMEOUT_S = 0.05
_SLEEP_PAST_RESET = 0.07


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------

class _FlakyAdapter:
    """Adapter that raises ``ConnectionError`` on the first ``fail_count``
    calls, then returns a clean strict-JSON payload on every subsequent
    call. The same instance is shared between phase 1 and phase 2 so
    the per-call counter keeps advancing across ``run_export`` calls.
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
                f"simulated transport failure #{self.calls}/{self.fail_count}"
            )
        return {
            "text": '{"intent": "recovered"}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


def _temp_outdir(name: str) -> Path:
    p = _ROOT / "runtime" / "_test_recovery_artifact_e2e" / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_report(out: Path) -> dict:
    return json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))


def _phase_heavy_cases(start_i: int, count: int) -> list[tuple[str, str, str]]:
    """Produce N heavy ``builder.requirement_parse`` cases starting at
    index ``start_i`` (1-based, for human-readable user_input labels)."""
    return [
        ("builder", "requirement_parse", f"phase-case-{i}")
        for i in range(start_i, start_i + count)
    ]


# ---------------------------------------------------------------------------
# Phase-based harness: ONE shared adapter + ONE shared breaker across two
# run_export calls. This is what the circuit_override hook enables.
# ---------------------------------------------------------------------------

def _run_two_phase_export(out_phase1: Path, out_phase2: Path):
    """Drive phase 1 (3 fails) → sleep → phase 2 (2 successes) sharing
    the same ``_FlakyAdapter(fail_count=3)`` and the same
    ``CircuitBreaker(fail_threshold=3, reset_timeout_s=0.05)`` across
    both ``run_export`` calls. Returns the shared breaker + adapter so
    tests can additionally assert on their post-phase2 state."""
    shared_adapter = _FlakyAdapter(fail_count=3)
    shared_breaker = CircuitBreaker(
        fail_threshold=3,
        reset_timeout_s=_RESET_TIMEOUT_S,
    )

    # Phase 1 — trip the breaker with 3 failures.
    ehr.run_export(
        cases=_phase_heavy_cases(start_i=1, count=3),
        base_url="http://test:1",
        base_url_source="test",
        api_key="x",
        model="m",
        allow_mock=False,
        out_dir=out_phase1,
        out_report=out_phase1 / "export_run_report.json",
        timeout_s=1.0,
        max_retries=0,                    # exactly 1 adapter call per case
        total_deadline_s=30.0,            # generous; not a budget-exhaust test
        transport_retry_cooldown_s=0.0,
        scheduler_cooldown_heavy_s=0.0,   # isolate phase 1 from scheduler waits
        scheduler_cooldown_light_s=0.0,
        fallback_retry_delay_s=0.13,
        cooldown_source="test",
        adapter_override=shared_adapter,
        used_mock_override=False,
        circuit_override=shared_breaker,  # T-tranche-17 hook
    )

    # Sleep past the reset window so the first state() read in phase 2
    # lazily flips to half_open.
    time.sleep(_SLEEP_PAST_RESET)

    # Phase 2 — case 4 is the half_open trial that succeeds (recovery);
    # case 5 is a normal steady-state success. Use a non-zero
    # scheduler_cooldown_heavy_s so case 5's scheduler wait entry is
    # observable in the artifact.
    ehr.run_export(
        cases=_phase_heavy_cases(start_i=4, count=2),
        base_url="http://test:1",
        base_url_source="test",
        api_key="x",
        model="m",
        allow_mock=False,
        out_dir=out_phase2,
        out_report=out_phase2 / "export_run_report.json",
        timeout_s=1.0,
        max_retries=0,
        total_deadline_s=30.0,
        transport_retry_cooldown_s=0.0,
        scheduler_cooldown_heavy_s=0.1,   # observable on case 5
        scheduler_cooldown_light_s=0.0,
        fallback_retry_delay_s=0.13,
        cooldown_source="test",
        adapter_override=shared_adapter,
        used_mock_override=False,
        circuit_override=shared_breaker,
    )
    return shared_breaker, shared_adapter


# ===========================================================================
# Test A — phase 1 artifact: 3 failures, all transport_retry_exhausted
# ===========================================================================

def test_phase1_three_failures_land_as_transport_retry_exhausted():
    """Phase 1 drives 3 failing cases through ``run_export`` with a
    shared breaker. All 3 cases reach the LLMClient's ``except
    Exception`` branch on their single attempt (``max_retries=0``), so
    each case ends with
    ``retry_decision_reason == "transport_retry_exhausted"``.

    No case in phase 1 is circuit-open because the breaker only flips
    to ``open`` AFTER case 3's ``record_failure`` fires, and by that
    point case 3's ``extract_slots`` has already returned (the
    short-circuit check runs at the TOP of the next call).

    This test runs phase 1 **in isolation** (no phase 2) so the
    assertions on the breaker's mid-trip state are valid — phase 2's
    recovery would reset the breaker to ``closed`` and erase the
    tripped state.
    """
    print("  [A] phase 1: 3 failures → transport_retry_exhausted, breaker tripped")
    out_phase1 = _temp_outdir("test_a_phase1")
    try:
        shared_adapter = _FlakyAdapter(fail_count=3)
        shared_breaker = CircuitBreaker(
            fail_threshold=3,
            reset_timeout_s=_RESET_TIMEOUT_S,
        )

        # Run phase 1 ONLY — no phase 2, so the breaker stays tripped
        # and the assertions below reflect true mid-trip state.
        ehr.run_export(
            cases=_phase_heavy_cases(start_i=1, count=3),
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out_phase1,
            out_report=out_phase1 / "export_run_report.json",
            timeout_s=1.0,
            max_retries=0,
            total_deadline_s=30.0,
            transport_retry_cooldown_s=0.0,
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            fallback_retry_delay_s=0.13,
            cooldown_source="test",
            adapter_override=shared_adapter,
            used_mock_override=False,
            circuit_override=shared_breaker,
        )

        # Phase 1 artifact — all 3 cases transport-exhausted.
        rr1 = _read_report(out_phase1)
        assert len(rr1["cases"]) == 3, len(rr1["cases"])
        for i, case in enumerate(rr1["cases"], start=1):
            assert case["final_status"] == "failed", (i, case["final_status"])
            assert case["retry_decision_reason"] == RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED, (
                f"phase 1 case {i}: expected transport_retry_exhausted, "
                f"got {case['retry_decision_reason']!r}"
            )
            assert case["retry_decision_reason"] != RETRY_REASON_CIRCUIT_OPEN, (
                f"phase 1 case {i} should NOT be circuit-open"
            )

        # Mid-trip state: breaker is "open" (no sleep yet — still within
        # reset window), failures=3, adapter called exactly 3 times.
        assert shared_breaker._state == "open", shared_breaker.snapshot()
        assert shared_breaker._failures == 3, shared_breaker.snapshot()
        assert shared_adapter.calls == 3, shared_adapter.calls
        # And allow() reflects the open state until sleep elapses.
        assert shared_breaker.allow() is False
        print(
            f"    OK: phase 1 all 3 transport_exhausted, "
            f"breaker._state={shared_breaker._state!r}, "
            f"_failures={shared_breaker._failures}, "
            f"adapter.calls={shared_adapter.calls}"
        )
    finally:
        shutil.rmtree(out_phase1, ignore_errors=True)


# ===========================================================================
# Test B — phase 2 case 4: recovery success has normal DONE artifact shape
# ===========================================================================

def test_phase2_recovery_case_has_normal_done_artifact_shape():
    """The half_open recovery case (phase 2 case 4) must appear in
    ``export_run_report.json`` as a **normal DONE success** — same
    shape as any ordinary first-attempt success. Specifically:

      - ``final_status == "success"``
      - ``retry_decision_reason == "initial_success"`` (NOT any
        special "recovery" marker, NOT "circuit_open")
      - ``attempts_used == 1``
      - ``budget_exhausted is False``
      - ``health_failure_reason is None``
      - ``parsed_slots`` is populated (from the adapter's success
        response)
      - and in the ``review_data.json`` payload, ``auto_status`` /
        ``auto_validated`` are set (strict gate downstream depends
        on layered judgment, but the dispatcher-level status must
        be "done").

    Recovery is a **first-class success** — no special flag, no
    residue from the prior breaker trip.
    """
    print("  [B] phase 2 case 4: recovery has normal DONE artifact shape")
    out_phase1 = _temp_outdir("test_b_phase1")
    out_phase2 = _temp_outdir("test_b_phase2")
    try:
        shared_breaker, _ = _run_two_phase_export(out_phase1, out_phase2)

        rr2 = _read_report(out_phase2)
        assert len(rr2["cases"]) == 2, len(rr2["cases"])
        case4 = rr2["cases"][0]  # phase 2 case 1 = overall case 4

        # Core DONE contract
        assert case4["final_status"] == "success", case4["final_status"]
        assert case4["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS, (
            f"recovery case should be initial_success, got "
            f"{case4['retry_decision_reason']!r}"
        )
        assert case4["attempts_used"] == 1, case4["attempts_used"]
        assert case4["budget_exhausted"] is False, case4["budget_exhausted"]
        assert case4["health_failure_reason"] is None, case4["health_failure_reason"]
        assert case4["transport_retry_count"] == 0, case4["transport_retry_count"]

        # Review data carries the parsed slots from the recovery adapter.
        rd2 = json.loads((out_phase2 / "review_data.json").read_text(encoding="utf-8"))
        assert len(rd2) == 2
        case4_review = rd2[0]
        assert case4_review["auto_status"] == "done", case4_review["auto_status"]
        assert case4_review["parsed_slots"] == {"intent": "recovered"}, (
            case4_review["parsed_slots"]
        )

        # After phase 2, shared_breaker is back to "closed" via
        # record_success on the recovery case.
        assert shared_breaker._state == "closed", shared_breaker.snapshot()
        assert shared_breaker._failures == 0, shared_breaker._failures
        print(
            f"    OK: recovery case 4 is normal DONE "
            f"(reason={case4['retry_decision_reason']!r}, attempts=1), "
            f"breaker reset to closed/failures=0"
        )
    finally:
        shutil.rmtree(out_phase1, ignore_errors=True)
        shutil.rmtree(out_phase2, ignore_errors=True)


# ===========================================================================
# Test C — steady-state case 5 uses recovery-case baseline for scheduler wait
# ===========================================================================

def test_steady_state_case_after_recovery_uses_recovery_baseline():
    """Phase 2 case 5 is the steady-state success immediately after the
    recovery case. Its scheduler wait entry must be computed from case
    4's ``post_execute`` baseline — i.e. the "just-recovered" case is
    what anchors cooldown for the next dispatch.

    Because ``scheduler_cooldown_heavy_s=0.1`` and case 5 runs
    microseconds after case 4's post_execute, case 5's ``pre_execute``
    will see ``elapsed_since_last ≪ 0.1`` and either fully sleep the
    remaining cooldown (fully honored) or clamp if the deadline tips
    over. Either way, the artifact must contain a ``scheduler_heavy``
    entry for case 5 with ``configured_s == 0.1`` and the operator
    ``source`` label.
    """
    print("  [C] phase 2 case 5: scheduler wait anchored to recovery baseline")
    out_phase1 = _temp_outdir("test_c_phase1")
    out_phase2 = _temp_outdir("test_c_phase2")
    try:
        _run_two_phase_export(out_phase1, out_phase2)
        rr2 = _read_report(out_phase2)
        case5 = rr2["cases"][1]  # phase 2 case 2 = overall case 5

        # Core DONE contract (same as case 4)
        assert case5["final_status"] == "success"
        assert case5["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS

        # Scheduler wait entry derived from case 4's baseline.
        cds = case5["cooldown_decisions"]
        scheduler_entries = [
            cd for cd in cds
            if cd.get("kind") in (WAIT_KIND_SCHEDULER_HEAVY, WAIT_KIND_SCHEDULER_LIGHT)
        ]
        assert len(scheduler_entries) >= 1, (
            f"case 5 must carry a scheduler wait entry; got kinds="
            f"{[cd.get('kind') for cd in cds]}"
        )
        sched = scheduler_entries[0]
        assert sched["kind"] == WAIT_KIND_SCHEDULER_HEAVY, sched
        assert sched["source"] == "test", sched
        # Canonical 7-key shape
        assert set(sched.keys()) == {
            "kind", "configured_s", "applied_s",
            "clamped", "skipped", "skip_reason", "source",
        }, sched

        # Policy value vs per-wait wait_needed distinction: the
        # WaitDecision's ``configured_s`` field stores the
        # scheduler's ``wait_needed`` at pre_execute time (which is
        # ``policy_value - elapsed_since_last``), NOT the raw policy
        # value. Because case 5 arrived nearly immediately after case
        # 4's post_execute, ``elapsed_since_last`` is tiny (a few ms
        # at most) so ``wait_needed`` is very close to 0.1 but
        # strictly less. Lock that the wait was almost-full.
        assert 0.09 < sched["configured_s"] < 0.1, (
            f"per-wait configured_s should be ~0.1 - small elapsed, got {sched['configured_s']}"
        )
        # And the RUN-level aggregate carries the raw policy value.
        assert rr2["configured_scheduler_cooldown_heavy_s"] == 0.1, (
            f"run-level cooldown policy value should be 0.1, got "
            f"{rr2['configured_scheduler_cooldown_heavy_s']}"
        )
        # Internal consistency: skipped → applied_s == 0;
        # non-skipped → applied_s ≤ configured_s.
        if sched["skipped"]:
            assert sched["applied_s"] == 0.0, sched
        else:
            assert sched["applied_s"] <= sched["configured_s"] + 1e-9, sched
            # In the non-skipped / fully-honored path, the scheduler
            # actually slept — so applied_s is positive.
            assert sched["applied_s"] > 0.0, sched

        # total_cooldown_ms aggregate is consistent with the sum of
        # applied_s entries (at minimum, non-negative).
        assert case5["total_cooldown_ms"] >= 0
        print(
            f"    OK: case 5 scheduler_heavy entry per-wait configured_s="
            f"{sched['configured_s']:.4f} (policy 0.1), "
            f"applied_s={sched['applied_s']:.4f}, skipped={sched['skipped']}"
        )
    finally:
        shutil.rmtree(out_phase1, ignore_errors=True)
        shutil.rmtree(out_phase2, ignore_errors=True)


# ===========================================================================
# Test D — negative distinction: recovery case artifact ≠ circuit-open artifact
# ===========================================================================

def test_recovery_case_is_not_confused_with_circuit_open_artifact():
    """Recovery success cases must NOT carry any circuit-open artifact
    markers. Specifically:

      - ``retry_decision_reason != "circuit_open"``
      - no ``fallback_retry`` entry in ``cooldown_decisions`` (the
        dispatcher's T-tranche-10 fallback merge only fires on FAILURE
        exit sites; the DONE path does NOT fire it)
      - no ``errors`` entries mentioning "Circuit breaker open"

    This is the negative half of the artifact-level distinction:
    circuit-open cases HAVE fallback_retry entries and
    ``retry_decision_reason == "circuit_open"``; recovery success cases
    do NOT. Any future refactor that starts marking recovery cases
    specially — e.g., by copying the fallback merge into the DONE
    path — will fail this test.

    Both case 4 (recovery) and case 5 (steady-state) are checked
    because they're the only two DONE cases in phase 2 and both must
    satisfy the negative distinction.
    """
    print("  [D] recovery case has no circuit-open / fallback_retry artifact markers")
    out_phase1 = _temp_outdir("test_d_phase1")
    out_phase2 = _temp_outdir("test_d_phase2")
    try:
        _run_two_phase_export(out_phase1, out_phase2)
        rr2 = _read_report(out_phase2)

        for idx, case in enumerate(rr2["cases"], start=4):  # overall case 4 and 5
            # No circuit-open reason on a recovery/steady-state success.
            assert case["retry_decision_reason"] != RETRY_REASON_CIRCUIT_OPEN, (
                f"case {idx} (success) carries circuit_open reason — artifact "
                f"distinction broken"
            )
            assert case["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS, (
                f"case {idx}: expected initial_success, got "
                f"{case['retry_decision_reason']!r}"
            )

            # No fallback_retry entry (the dispatcher only adds this on
            # failure exit sites).
            cds = case["cooldown_decisions"]
            fallback_entries = [
                cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY
            ]
            assert len(fallback_entries) == 0, (
                f"case {idx} (success) carries {len(fallback_entries)} "
                f"fallback_retry entries — DONE path should not fire the "
                f"fallback merge. cooldown_decisions={cds}"
            )

        # And the review_data.json side carries no "Circuit breaker open"
        # errors either.
        rd2 = json.loads((out_phase2 / "review_data.json").read_text(encoding="utf-8"))
        for idx, entry in enumerate(rd2, start=4):
            # review_data entries don't have an explicit errors list,
            # but the raw_llm_output / auto_status must not carry circuit
            # artifacts — success cases have auto_status="done".
            assert entry["auto_status"] == "done", (idx, entry["auto_status"])

        # Run-level aggregates also reflect the distinction:
        # total_budget_exhausted should be 0 for phase 2 (no cases
        # budget-exhausted). health_failure_reason is None (no health
        # probe failure staged).
        assert rr2["total_budget_exhausted"] == 0, rr2["total_budget_exhausted"]
        assert rr2["health_failure_reason"] is None, rr2["health_failure_reason"]
        print("    OK: both phase 2 success cases are distinct from circuit-open shape")
    finally:
        shutil.rmtree(out_phase1, ignore_errors=True)
        shutil.rmtree(out_phase2, ignore_errors=True)


# ===========================================================================
# Test E — circuit_override hook is a no-op when omitted (back-compat)
# ===========================================================================

def test_circuit_override_default_none_preserves_fresh_breaker_behavior():
    """The T-tranche-17 ``circuit_override`` hook must be a strict
    additive optional. When omitted (the default for every existing
    caller), ``run_export`` must build its own fresh
    ``CircuitBreaker()`` exactly as before. This test drives a
    single-phase run WITHOUT ``circuit_override`` and verifies the
    artifact has the same shape it had before the hook was added.
    """
    print("  [E] circuit_override=None preserves fresh-breaker back-compat")
    out = _temp_outdir("test_e_back_compat")
    try:
        adapter = _FlakyAdapter(fail_count=999)  # always fail
        ehr.run_export(
            cases=_phase_heavy_cases(start_i=1, count=2),
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=0,
            total_deadline_s=30.0,
            transport_retry_cooldown_s=0.0,
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            fallback_retry_delay_s=0.13,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
            # circuit_override NOT passed — default None path
        )
        rr = _read_report(out)
        assert len(rr["cases"]) == 2
        # Both cases are parse-fails (fresh breaker threshold=3 not reached).
        for c in rr["cases"]:
            assert c["retry_decision_reason"] == RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED
            assert c["final_status"] == "failed"
        # With 2 failures under default threshold=3, NO case should be
        # circuit_open — proves the default breaker was actually used
        # (not some overridden instance that was already tripped).
        reasons = [c["retry_decision_reason"] for c in rr["cases"]]
        assert RETRY_REASON_CIRCUIT_OPEN not in reasons, reasons
        print("    OK: default path (no circuit_override) unchanged")
    finally:
        shutil.rmtree(out, ignore_errors=True)


TESTS = [
    test_phase1_three_failures_land_as_transport_retry_exhausted,
    test_phase2_recovery_case_has_normal_done_artifact_shape,
    test_steady_state_case_after_recovery_uses_recovery_baseline,
    test_recovery_case_is_not_confused_with_circuit_open_artifact,
    test_circuit_override_default_none_preserves_fresh_breaker_behavior,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Recovery artifact E2E tests (T-tranche-17)")
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
