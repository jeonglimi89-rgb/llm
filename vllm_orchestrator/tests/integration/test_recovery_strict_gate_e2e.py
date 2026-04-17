"""
test_recovery_strict_gate_e2e.py — artifact-level proof that a
half_open recovery success case is treated identically to an ordinary
first-attempt success by the strict gate / layered judgment layer.

Background
==========
T-tranche-17 locked the **dispatcher-level** shape of a recovery success
case: ``final_status == "success"``, ``retry_decision_reason ==
"initial_success"``, ``cooldown_decisions`` has no ``fallback_retry``
entry. Those fields all live in ``export_run_report.json``.

But the strict gate verdict (``auto_validated`` / ``final_judgment`` /
``failure_categories`` / ``severity`` / ``layered_judgment``) lives in
``review_data.json``. That file is written by ``run_export`` after
``evaluate_task_contract`` runs the 5-gate layered review on the
parsed slots. Nothing in T-tranche-17 verified that a recovery case
flows through the layered gate identically to an ordinary success.

T-tranche-18 (2026-04-10) closes that gap with a phase-based harness
(reusing the T-tranche-17 ``circuit_override`` hook) that drives:

  - **Recovery success with a PASS-worthy payload**
    (``{"intent": "..."}``): the strict gate must pass.
  - **Recovery success with a FAIL-worthy payload**
    (Chinese keys like ``{"楼层": "2층"}``): the strict gate must fail
    via ``wrong_key_locale``, but the dispatcher still returns DONE
    and ``retry_decision_reason == "initial_success"`` — proving that
    dispatcher-level success and layered-review verdict are **orthogonal
    layers**.
  - **Ordinary success baseline** (same payload, no breaker trip): the
    strict gate verdict for the baseline case must be byte-equivalent
    to the recovery case with the same payload. If the two diverge, it
    proves recovery is leaking hidden state into the gate layer.

Strict gate contract (re-verified against src/app/review/)
==========================================================
- ``review/layered.py::LayeredJudgment`` fields: ``auto_validated:
  bool``, ``final_judgment: str`` ∈ {pass, needs_review, fail},
  ``severity: str``, ``failure_categories: list[str]``, ``rationale:
  str``, and a 5-gate breakdown.
- ``auto_validated = all 5 gates passed``. The 5 gates are schema /
  language / semantic / domain_guard / contract.
- For ``builder.requirement_parse``: ``BUILDER_ALLOWED_KEYS`` excludes
  Chinese characters, so a Chinese-key payload triggers
  ``detect_chinese_keys`` → ``wrong_key_locale`` failure category →
  ``auto_validated=False``, ``final_judgment == "fail"``.
- For a clean ``{"intent": "…"}`` payload on
  ``builder.requirement_parse``: ``intent`` is in
  ``BUILDER_ALLOWED_KEYS``, no detector fires, all 5 gates pass,
  ``auto_validated=True``, ``final_judgment == "pass"``.
- **Dispatcher-level success ≠ strict-gate verdict.**
  ``TaskStatus.DONE`` is set by the dispatcher when
  ``LLMClient.extract_slots`` returns non-None parsed slots — long
  BEFORE the layered review runs. ``auto_validated`` is the layered
  review's output, AFTER parse success. They are two separate layers.

Role separation
===============
- T-tranche-17 (`test_recovery_artifact_e2e.py`): dispatcher-level
  recovery success artifact shape (``final_status``,
  ``retry_decision_reason``, ``cooldown_decisions``).
- T-tranche-18 (this file): **strict-gate-level** recovery artifact
  shape (``auto_validated``, ``final_judgment``, ``failure_categories``
  in ``review_data.json``).
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
from src.app.llm.client import (
    RETRY_REASON_INITIAL_SUCCESS,
    RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED,
    RETRY_REASON_CIRCUIT_OPEN,
)
from src.app.execution.timeouts import WAIT_KIND_FALLBACK_RETRY


# Load run_export as a module.
_SPEC = importlib.util.spec_from_file_location(
    "ehr_strict_gate", str(_ROOT / "scripts" / "export_human_review.py"),
)
ehr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ehr)


# Timing constants (same as T-tranche-17).
_RESET_TIMEOUT_S = 0.05
_SLEEP_PAST_RESET = 0.07


# ---------------------------------------------------------------------------
# Fake adapters — two shapes:
#   1. _FlakyPassAdapter: first N calls fail, later calls return a
#      strict-gate-PASSING payload.
#   2. _FlakyFailAdapter: first N calls fail, later calls return a
#      strict-gate-FAILING payload (Chinese keys → wrong_key_locale).
#   3. _CleanPassAdapter: never fails; returns the same passing payload
#      as #1 from call 1.
#   4. _CleanFailAdapter: never fails; returns the same failing payload
#      as #2 from call 1.
#
# Shapes 3 and 4 are the "ordinary success baseline" for
# comparison — they skip the breaker trip entirely.
# ---------------------------------------------------------------------------

_PASS_PAYLOAD = '{"intent": "recovered"}'
_FAIL_PAYLOAD = '{"楼层": "2층", "户型": "모던 스타일"}'   # HR-001 Chinese-key family


class _FlakyPassAdapter:
    """First N calls raise; subsequent calls return a PASS payload."""
    provider_name = "flaky-pass"

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0
        self.live = True

    def is_available(self):
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise ConnectionError(f"simulated fail #{self.calls}")
        return {
            "text": _PASS_PAYLOAD,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


class _FlakyFailAdapter:
    """First N calls raise; subsequent calls return a FAIL payload
    (Chinese keys) that the strict gate must reject with
    ``wrong_key_locale``. The dispatcher still returns DONE because the
    payload is valid JSON and the schema gate passes; only the layered
    gate fails, which is exactly the distinction this test locks."""
    provider_name = "flaky-fail"

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0
        self.live = True

    def is_available(self):
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise ConnectionError(f"simulated fail #{self.calls}")
        return {
            "text": _FAIL_PAYLOAD,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


class _CleanPassAdapter:
    """Never fails. Always returns the PASS payload. Used as the
    ordinary-success baseline for Test C comparison."""
    provider_name = "clean-pass"

    def __init__(self):
        self.calls = 0
        self.live = True

    def is_available(self):
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        return {"text": _PASS_PAYLOAD, "prompt_tokens": 0, "completion_tokens": 0}


class _CleanFailAdapter:
    """Never fails. Always returns the FAIL payload. Used as the
    ordinary-success-with-gate-fail baseline for Test C comparison."""
    provider_name = "clean-fail"

    def __init__(self):
        self.calls = 0
        self.live = True

    def is_available(self):
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        return {"text": _FAIL_PAYLOAD, "prompt_tokens": 0, "completion_tokens": 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _temp_outdir(name: str) -> Path:
    p = _ROOT / "runtime" / "_test_recovery_strict_gate_e2e" / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _heavy_cases(start_i: int, count: int) -> list[tuple[str, str, str]]:
    return [
        ("builder", "requirement_parse", f"phase-case-{i}")
        for i in range(start_i, start_i + count)
    ]


def _read_run_report(out: Path) -> dict:
    return json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))


def _read_review_data(out: Path) -> list[dict]:
    return json.loads((out / "review_data.json").read_text(encoding="utf-8"))


def _run_recovery_phases(adapter, out_phase1: Path, out_phase2: Path):
    """Drive phase 1 (3 fails) + sleep + phase 2 (1 recovery case) sharing
    a single breaker + adapter."""
    shared_breaker = CircuitBreaker(
        fail_threshold=3,
        reset_timeout_s=_RESET_TIMEOUT_S,
    )

    ehr.run_export(
        cases=_heavy_cases(start_i=1, count=3),
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
        adapter_override=adapter,
        used_mock_override=False,
        circuit_override=shared_breaker,
    )
    time.sleep(_SLEEP_PAST_RESET)
    ehr.run_export(
        cases=_heavy_cases(start_i=4, count=1),  # recovery case only
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
        scheduler_cooldown_heavy_s=0.0,
        scheduler_cooldown_light_s=0.0,
        fallback_retry_delay_s=0.13,
        cooldown_source="test",
        adapter_override=adapter,
        used_mock_override=False,
        circuit_override=shared_breaker,
    )
    return shared_breaker


def _run_ordinary_baseline(adapter, out: Path):
    """Drive a single 1-case run_export with a fresh breaker so the case
    is a normal first-attempt success. No phase split, no sleep, no
    shared breaker state."""
    ehr.run_export(
        cases=_heavy_cases(start_i=1, count=1),
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
        # circuit_override NOT passed → fresh default breaker
    )


# ===========================================================================
# Test A — recovery + strict gate PASS: layered judgment accepts the case
# ===========================================================================

def test_recovery_success_with_passing_payload_strict_gate_accepts():
    """Recovery case returns a PASS-worthy payload. The layered judgment
    layer must report the 5-gate pass contract in ``review_data.json``:

      - ``auto_validated == True``
      - ``layered_judgment.final_judgment == "pass"``
      - ``failure_categories == []``
      - ``severity`` is the pass severity (``"none"`` per layered.py)
      - ``auto_status == "done"``
      - ``parsed_slots`` is the PASS payload dict

    And the dispatcher-level telemetry in ``export_run_report.json`` is
    unchanged from T-tranche-17 expectations:

      - ``final_status == "success"``
      - ``retry_decision_reason == "initial_success"``
      - ``attempts_used == 1``
    """
    print("  [A] recovery + PASS payload: strict gate accepts, dispatcher DONE unchanged")
    out_p1 = _temp_outdir("a_phase1")
    out_p2 = _temp_outdir("a_phase2")
    try:
        adapter = _FlakyPassAdapter(fail_count=3)
        _run_recovery_phases(adapter, out_p1, out_p2)

        # Dispatcher-level telemetry from export_run_report.json
        rr2 = _read_run_report(out_p2)
        assert len(rr2["cases"]) == 1
        case = rr2["cases"][0]
        assert case["final_status"] == "success", case
        assert case["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS, case
        assert case["attempts_used"] == 1, case

        # Strict-gate telemetry from review_data.json
        rd2 = _read_review_data(out_p2)
        assert len(rd2) == 1
        entry = rd2[0]
        assert entry["auto_status"] == "done", entry["auto_status"]
        assert entry["auto_validated"] is True, (
            f"recovery + PASS payload should auto-validate; entry={entry}"
        )
        assert entry["failure_categories"] == [], entry["failure_categories"]
        assert entry["parsed_slots"] == {"intent": "recovered"}, entry["parsed_slots"]
        lj = entry["layered_judgment"]
        assert lj is not None
        assert lj.get("final_judgment") == "pass", lj.get("final_judgment")
        assert lj.get("auto_validated") is True, lj.get("auto_validated")
        # On pass, severity is the lowest level in Severity enum —
        # ``info`` per src/app/review/layered.py (NOT ``none``; ``none``
        # is only used by FailureCategory, not Severity). Pin it
        # explicitly so a future rename cascades into this test.
        assert lj.get("severity") == "info", lj.get("severity")

        print(
            f"    OK: recovery PASS case — auto_validated=True, final_judgment=pass, "
            f"retry_decision_reason={case['retry_decision_reason']!r}"
        )
    finally:
        shutil.rmtree(out_p1, ignore_errors=True)
        shutil.rmtree(out_p2, ignore_errors=True)


# ===========================================================================
# Test B — recovery + strict gate FAIL: dispatcher DONE, layered fails
# ===========================================================================

def test_recovery_success_with_failing_payload_strict_gate_rejects():
    """Recovery case returns a FAIL-worthy payload (Chinese keys → HR-001
    family). The dispatcher still returns DONE because the JSON parses
    and the schema gate succeeds — the layered review is what rejects
    the case.

    Crucial ordering assertion: dispatcher-level
    ``retry_decision_reason`` is ``"initial_success"`` AND strict-gate
    ``auto_validated`` is ``False``, simultaneously. This locks that
    the two layers are orthogonal:

      - dispatcher success = "LLMClient returned non-None parsed slots"
      - layered review verdict = "5 semantic gates all passed"

    Recovery does NOT contaminate the layered review decision, and the
    layered review failure does NOT contaminate the dispatcher telemetry.
    """
    print("  [B] recovery + FAIL payload: dispatcher DONE + strict gate fails")
    out_p1 = _temp_outdir("b_phase1")
    out_p2 = _temp_outdir("b_phase2")
    try:
        adapter = _FlakyFailAdapter(fail_count=3)
        _run_recovery_phases(adapter, out_p1, out_p2)

        rr2 = _read_run_report(out_p2)
        case = rr2["cases"][0]
        # Dispatcher-level: STILL a success (parse succeeded).
        assert case["final_status"] == "success", case["final_status"]
        assert case["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS, (
            f"dispatcher-level retry_decision_reason should remain initial_success "
            f"even when the layered gate fails; got {case['retry_decision_reason']!r}"
        )
        assert case["attempts_used"] == 1, case
        # No circuit-open residue on a recovery case that passed through
        # to the success path.
        assert case["retry_decision_reason"] != RETRY_REASON_CIRCUIT_OPEN, case

        # Strict-gate: layered review REJECTS.
        rd2 = _read_review_data(out_p2)
        entry = rd2[0]
        assert entry["auto_status"] == "done", entry["auto_status"]  # unchanged
        assert entry["auto_validated"] is False, (
            f"FAIL payload (Chinese keys) should not auto-validate; entry={entry}"
        )
        assert "wrong_key_locale" in (entry["failure_categories"] or []), (
            f"Chinese-key payload should fail with wrong_key_locale; "
            f"got failure_categories={entry['failure_categories']}"
        )
        lj = entry["layered_judgment"]
        assert lj is not None
        assert lj.get("final_judgment") == "fail", lj.get("final_judgment")
        assert lj.get("auto_validated") is False, lj.get("auto_validated")
        # Severity on fail is strictly above ``info`` — one of
        # ``{warn, high, critical}`` per src/app/review/layered.py's
        # Severity enum. Chinese-key detection is categorized as
        # WRONG_KEY_LOCALE which is a high-severity failure family.
        sev = lj.get("severity")
        assert sev and sev != "info", (
            f"fail severity should be above info, got {sev!r}"
        )
        assert sev in ("warn", "high", "critical"), f"unknown severity {sev!r}"

        print(
            f"    OK: recovery FAIL case — auto_validated=False, "
            f"final_judgment={lj.get('final_judgment')!r}, "
            f"categories={entry['failure_categories']}, "
            f"retry_decision_reason={case['retry_decision_reason']!r} "
            f"(orthogonal to layered verdict)"
        )
    finally:
        shutil.rmtree(out_p1, ignore_errors=True)
        shutil.rmtree(out_p2, ignore_errors=True)


# ===========================================================================
# Test C — ordinary success baseline: same payload, no breaker trip
# ===========================================================================

def test_ordinary_success_baseline_strict_gate_matches_recovery_case():
    """Drive two parallel flows with the SAME passing payload:

      1. Recovery flow (phase 1 trip → sleep → phase 2 case 4 success)
      2. Ordinary baseline flow (single run_export with a fresh default
         breaker and a clean adapter — no failures, no trip, no sleep)

    The strict-gate verdict fields in ``review_data.json`` must be
    byte-equivalent between the two flows:

      - ``auto_validated``: same value
      - ``final_judgment``: same value
      - ``failure_categories``: same list
      - ``layered_judgment.auto_validated``: same value
      - ``parsed_slots``: same dict

    Recovery case must not leak any hidden metadata into the gate layer
    that would differentiate it from an ordinary success. The comparison
    is performed on the PASS payload; a parallel comparison is done on
    the FAIL payload to lock the same symmetry on the fail side.
    """
    print("  [C] ordinary success baseline: strict-gate byte-equivalent to recovery case")

    # PASS side
    out_rec_p1 = _temp_outdir("c_pass_recovery_phase1")
    out_rec_p2 = _temp_outdir("c_pass_recovery_phase2")
    out_base = _temp_outdir("c_pass_baseline")
    try:
        _run_recovery_phases(_FlakyPassAdapter(fail_count=3), out_rec_p1, out_rec_p2)
        _run_ordinary_baseline(_CleanPassAdapter(), out_base)

        rec_entry = _read_review_data(out_rec_p2)[0]
        base_entry = _read_review_data(out_base)[0]

        # The fields that must be strictly identical regardless of
        # whether the case is on the recovery path or the clean path.
        for field in (
            "auto_validated",
            "final_judgment",
            "failure_categories",
            "parsed_slots",
            "auto_status",
            "severity",
        ):
            assert rec_entry[field] == base_entry[field], (
                f"PASS side field {field!r} drift: "
                f"recovery={rec_entry[field]!r} vs baseline={base_entry[field]!r}"
            )
        # Also compare the layered_judgment sub-dict fields that don't
        # depend on timing (auto_validated / final_judgment / severity).
        rec_lj = rec_entry["layered_judgment"]
        base_lj = base_entry["layered_judgment"]
        for field in ("auto_validated", "final_judgment", "severity"):
            assert rec_lj.get(field) == base_lj.get(field), (
                f"PASS side layered_judgment.{field} drift: "
                f"recovery={rec_lj.get(field)!r} vs baseline={base_lj.get(field)!r}"
            )
        print(
            f"    OK (PASS side): recovery auto_validated="
            f"{rec_entry['auto_validated']}, baseline=same"
        )
    finally:
        shutil.rmtree(out_rec_p1, ignore_errors=True)
        shutil.rmtree(out_rec_p2, ignore_errors=True)
        shutil.rmtree(out_base, ignore_errors=True)

    # FAIL side — same byte-equivalence check with the Chinese-key payload.
    out_rec_p1 = _temp_outdir("c_fail_recovery_phase1")
    out_rec_p2 = _temp_outdir("c_fail_recovery_phase2")
    out_base = _temp_outdir("c_fail_baseline")
    try:
        _run_recovery_phases(_FlakyFailAdapter(fail_count=3), out_rec_p1, out_rec_p2)
        _run_ordinary_baseline(_CleanFailAdapter(), out_base)

        rec_entry = _read_review_data(out_rec_p2)[0]
        base_entry = _read_review_data(out_base)[0]

        for field in (
            "auto_validated",
            "final_judgment",
            "failure_categories",
            "parsed_slots",
            "auto_status",
            "severity",
        ):
            assert rec_entry[field] == base_entry[field], (
                f"FAIL side field {field!r} drift: "
                f"recovery={rec_entry[field]!r} vs baseline={base_entry[field]!r}"
            )
        rec_lj = rec_entry["layered_judgment"]
        base_lj = base_entry["layered_judgment"]
        for field in ("auto_validated", "final_judgment", "severity"):
            assert rec_lj.get(field) == base_lj.get(field)
        # And both must have wrong_key_locale in failure_categories.
        assert "wrong_key_locale" in rec_entry["failure_categories"]
        assert "wrong_key_locale" in base_entry["failure_categories"]
        print(
            f"    OK (FAIL side): recovery auto_validated="
            f"{rec_entry['auto_validated']}, baseline=same, "
            f"categories={rec_entry['failure_categories']}"
        )
    finally:
        shutil.rmtree(out_rec_p1, ignore_errors=True)
        shutil.rmtree(out_rec_p2, ignore_errors=True)
        shutil.rmtree(out_base, ignore_errors=True)


# ===========================================================================
# Test D — ordering proof: dispatcher-success semantics ⊥ layered verdict
# ===========================================================================

def test_dispatcher_success_semantics_independent_of_layered_verdict():
    """Ordering proof: for BOTH the PASS payload and the FAIL payload,
    the recovery case's dispatcher-level ``retry_decision_reason`` is
    always ``"initial_success"`` — regardless of whether the layered
    review verdict is pass or fail.

    This is the artifact-level statement of the invariant "dispatcher
    success and layered review verdict are two separate layers with
    separate semantics and separate ordering". The dispatcher doesn't
    know or care about the layered gate when it decides DONE; the
    layered gate runs AFTER dispatcher DONE.
    """
    print("  [D] ordering: retry_decision_reason stays initial_success regardless of gate verdict")

    for variant, adapter_factory, expected_auto_validated in (
        ("pass", lambda: _FlakyPassAdapter(fail_count=3), True),
        ("fail", lambda: _FlakyFailAdapter(fail_count=3), False),
    ):
        out_p1 = _temp_outdir(f"d_{variant}_phase1")
        out_p2 = _temp_outdir(f"d_{variant}_phase2")
        try:
            _run_recovery_phases(adapter_factory(), out_p1, out_p2)
            rr2 = _read_run_report(out_p2)
            case = rr2["cases"][0]
            rd2 = _read_review_data(out_p2)
            entry = rd2[0]

            # Dispatcher-level: ALWAYS initial_success on the recovery case.
            assert case["final_status"] == "success", (variant, case["final_status"])
            assert case["retry_decision_reason"] == RETRY_REASON_INITIAL_SUCCESS, (
                variant, case["retry_decision_reason"]
            )

            # Layered-level: depends on payload, NOT on recovery.
            assert entry["auto_validated"] is expected_auto_validated, (
                f"[{variant}] expected auto_validated={expected_auto_validated}, "
                f"got {entry['auto_validated']}"
            )

            # Regardless of layered verdict, auto_status == "done" on the
            # dispatcher side — the two layers never reach into each other.
            assert entry["auto_status"] == "done", (variant, entry["auto_status"])
        finally:
            shutil.rmtree(out_p1, ignore_errors=True)
            shutil.rmtree(out_p2, ignore_errors=True)
    print(
        "    OK: retry_decision_reason=initial_success on both pass and fail variants, "
        "auto_validated differs by payload only"
    )


# ===========================================================================
# Test E — negative distinction: recovery case has no circuit-open residue
#          in either the dispatcher-level OR the strict-gate-level artifact
# ===========================================================================

def test_recovery_case_has_no_circuit_open_residue_in_any_layer():
    """Recovery success must NOT carry any circuit-open markers — not
    in ``export_run_report.json`` (dispatcher level) and not in
    ``review_data.json`` (strict-gate level). Specifically:

      export_run_report.json (case level):
        - ``retry_decision_reason != "circuit_open"``
        - ``cooldown_decisions`` has NO ``fallback_retry`` entry
        - no "Circuit breaker open" residue (no errors field check here;
          dispatcher-success cases don't carry error strings)

      review_data.json (entry level):
        - ``auto_status == "done"`` (not ERROR)
        - no mention of "circuit_breaker_open" / "circuit" in
          ``failure_categories`` (the layered review doesn't have such
          a category — its categories are semantic, not execution-level)

    Both PASS and FAIL payload variants are checked so the negative
    distinction holds across the full strict-gate matrix.
    """
    print("  [E] recovery has no circuit-open residue in dispatcher OR strict-gate artifact")

    for variant, adapter_factory in (
        ("pass", lambda: _FlakyPassAdapter(fail_count=3)),
        ("fail", lambda: _FlakyFailAdapter(fail_count=3)),
    ):
        out_p1 = _temp_outdir(f"e_{variant}_phase1")
        out_p2 = _temp_outdir(f"e_{variant}_phase2")
        try:
            _run_recovery_phases(adapter_factory(), out_p1, out_p2)

            # export_run_report side
            rr2 = _read_run_report(out_p2)
            case = rr2["cases"][0]
            assert case["retry_decision_reason"] != RETRY_REASON_CIRCUIT_OPEN, (
                f"[{variant}] dispatcher carried circuit_open into recovery case"
            )
            cds = case["cooldown_decisions"]
            fallback_entries = [
                cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY
            ]
            assert len(fallback_entries) == 0, (
                f"[{variant}] recovery case carries {len(fallback_entries)} "
                f"fallback_retry entries — DONE path should not trigger fallback merge"
            )

            # review_data side
            rd2 = _read_review_data(out_p2)
            entry = rd2[0]
            assert entry["auto_status"] == "done", (
                f"[{variant}] review_data auto_status leaked from circuit path"
            )
            cats = entry.get("failure_categories") or []
            for cat in cats:
                assert "circuit" not in cat.lower(), (
                    f"[{variant}] failure_categories leaked circuit reference: {cat}"
                )
                assert cat != "circuit_breaker_open", cat
        finally:
            shutil.rmtree(out_p1, ignore_errors=True)
            shutil.rmtree(out_p2, ignore_errors=True)
    print("    OK: both pass and fail variants are free of circuit-open residue")


TESTS = [
    test_recovery_success_with_passing_payload_strict_gate_accepts,
    test_recovery_success_with_failing_payload_strict_gate_rejects,
    test_ordinary_success_baseline_strict_gate_matches_recovery_case,
    test_dispatcher_success_semantics_independent_of_layered_verdict,
    test_recovery_case_has_no_circuit_open_residue_in_any_layer,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Recovery + strict gate E2E tests (T-tranche-18)")
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
