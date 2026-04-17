"""
test_artifact_explainability.py — default-gate explainability invariants for
the export_run_report.json artifact.

Background
==========
The runtime carries explainability fields that answer "why was this case
slow / failed / skipped" — cooldown decisions, retry-decision reasons,
health failure classifications, total-deadline budget, and policy source
labels. ``test_export_hardening.py`` already verifies that most of these
fields **exist** in the run report. This file is the dedicated layer that
verifies they carry **semantically meaningful values** end-to-end through
the JSON roundtrip — i.e. they are not None / 0 / empty placeholders, the
serialized shape matches the per-decision contract, and operator-supplied
values survive the full ``run_export → CaseTelemetry → RunTelemetry.to_dict
→ write_run_report → json.loads`` chain.

The 5 scenarios:
  A) cooldown_applied_path: when transport-fail cooldown is fully honored
     (small cooldown, generous deadline), the per-case ``cooldown_decisions``
     list contains a ``transport_retry`` entry with ``applied_s > 0``,
     ``clamped=False``, ``skipped=False``, and ``source`` carrying the
     operator-supplied label. ``total_cooldown_ms`` is positive.
  B) count_based_exhaustion_reason: when retries exhaust by *count*
     (not budget), per-case ``retry_decision_reason`` ==
     ``transport_retry_exhausted`` and ``budget_exhausted is False``, and
     ``attempts_used == 1 + max_retries`` (the documented count contract).
  C) cooldown_decision_shape_after_json_roundtrip: after JSON serialization,
     every ``cooldown_decisions[i]`` dict has the canonical 7-field
     ``WaitDecision`` shape; ``kind`` is one of the normalized enum values;
     numeric fields are floats; ``source`` is non-empty.
  D) health_failure_reason_classification_non_timeout: a non-timeout health
     failure (``connection_refused``) classifies and flows into both per-case
     and run-level ``health_failure_reason``. The existing hardening test
     only covers ``timeout``; this catches a regression in any other branch
     of ``_classify_urllib_exception``.
  E) operator_supplied_values_survive_roundtrip: every operator-supplied
     value (timeout, health timeout, max_retries, cooldown values, source
     labels) appears in the serialized run report at exactly the value the
     operator set, and ``policy_source_summary`` lists all 4 source labels
     non-default.

These are deterministic — they use the same ``adapter_override`` /
``used_mock_override=False`` injection path as test_export_hardening.py and
do not require any infra. The total wall-clock for the file is well under
2s because every test uses 1 case, ``transport_retry_cooldown_s ≤ 0.05``,
and ``scheduler_cooldown_*=0.0``.
"""
from __future__ import annotations

import json
import shutil
import sys
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

# Import the export_human_review.py script as a module (same pattern as
# test_export_hardening.py).
_SPEC = importlib.util.spec_from_file_location(
    "ehr_explain", str(_ROOT / "scripts" / "export_human_review.py"),
)
ehr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ehr)

from src.app.execution.timeouts import (
    WAIT_KIND_TRANSPORT_RETRY,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_KIND_FALLBACK_RETRY,
)
from src.app.llm.adapters.vllm_http import (
    HealthProbeResult,
    HEALTH_REASON_OK,
    HEALTH_REASON_TIMEOUT,
    HEALTH_REASON_CONNECTION_REFUSED,
    HEALTH_REASON_DNS_FAILURE,
    HEALTH_REASON_HTTP_ERROR,
    HEALTH_REASON_MALFORMED_RESPONSE,
    HEALTH_REASON_UNEXPECTED_ERROR,
)


# ---------------------------------------------------------------------------
# Constants — pinned wait kinds and field names
# ---------------------------------------------------------------------------

KNOWN_WAIT_KINDS = {
    WAIT_KIND_TRANSPORT_RETRY,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_KIND_FALLBACK_RETRY,
}

# Canonical WaitDecision JSON shape (post asdict).
WAIT_DECISION_KEYS = {
    "kind", "configured_s", "applied_s",
    "clamped", "skipped", "skip_reason", "source",
}


# ---------------------------------------------------------------------------
# Local fake adapters — kept tiny so this file is self-contained.
# ---------------------------------------------------------------------------

class _AlwaysFailAdapter:
    """Adapter that raises ConnectionError on every generate. Used to drive
    the LLMClient retry loop deterministically. Returns ``True`` from
    ``is_available()`` so build_adapter is happy and the cooldown path
    actually engages."""
    provider_name = "always-fail"

    def __init__(self):
        self.calls = 0
        self.live = True

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        raise ConnectionError("simulated transport failure (deterministic)")


class _SuccessAdapter:
    """Adapter that returns a clean strict-JSON payload for any user input.
    Used when we need a successful run that exercises a non-failure code
    path (e.g. policy source survival)."""
    provider_name = "success-stub"

    def __init__(self):
        self.calls = 0
        self.live = True
        # Pre-set: stays None unless a test explicitly stages a probe.
        self.last_health_probe_result: HealthProbeResult | None = None

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        return {
            "text": '{"intent": "거실 확장"}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

# 1-case fixture — use the first DEFAULT case so the test runs in milliseconds.
_DEFAULT_CASES = ehr.load_cases_file(ehr.DEFAULT_CASES_PATH)
ONE_CASE = _DEFAULT_CASES[:1]


def _temp_outdir(name: str) -> Path:
    p = _ROOT / "runtime" / "_test_artifact_explainability" / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_report(out: Path) -> dict:
    return json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))


def _is_finite_positive_float(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0


def _is_non_placeholder_string(x) -> bool:
    return isinstance(x, str) and len(x) > 0 and x not in {"unknown", "None", "null"}


# ===========================================================================
# Scenario A — cooldown decisions on the *applied* path (non-clamp non-skip)
# ===========================================================================

def test_cooldown_applied_path_appears_in_artifact_with_positive_applied_s():
    """Force a transport failure with a small but non-zero
    ``transport_retry_cooldown_s`` and a generous deadline. The cooldown
    must be **fully honored** — the per-case ``cooldown_decisions`` list
    must contain at least one ``transport_retry`` entry with ``applied_s
    > 0``, ``clamped=False``, ``skipped=False``, and ``source`` equal to
    the operator-supplied label.

    This locks the *non-degenerate* path of the cooldown contract — the
    existing hardening tests only cover the clamp/skip and zero paths.
    """
    print("  [A] cooldown applied path: transport_retry with applied_s>0 in artifact")
    out = _temp_outdir("cooldown_applied")
    try:
        adapter = _AlwaysFailAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=1,                       # 1 retry → triggers cooldown once
            total_deadline_s=10.0,               # generous: cooldown stays fully honored
            transport_retry_cooldown_s=0.05,     # small but > 0 so applied_s > 0
            scheduler_cooldown_heavy_s=0.0,      # disable scheduler waits to isolate transport_retry
            scheduler_cooldown_light_s=0.0,
            cooldown_source="test",              # custom source label to verify carry
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)

        # Per-case cooldown_decisions exists and has at least one transport_retry
        # entry with positive applied_s.
        assert len(rr["cases"]) == 1
        case = rr["cases"][0]
        cds = case["cooldown_decisions"]
        assert isinstance(cds, list) and len(cds) >= 1, (
            f"expected cooldown_decisions non-empty, got {cds}"
        )
        transport_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_TRANSPORT_RETRY]
        assert len(transport_entries) >= 1, (
            f"expected at least one transport_retry decision, got kinds="
            f"{[cd.get('kind') for cd in cds]}"
        )

        applied_entry = transport_entries[0]
        assert applied_entry["clamped"] is False, applied_entry
        assert applied_entry["skipped"] is False, applied_entry
        assert applied_entry["configured_s"] == 0.05, applied_entry
        # applied_s must be positive — proves "fully honored" path for the
        # transport_retry slot specifically.
        assert _is_finite_positive_float(applied_entry["applied_s"]), applied_entry
        # source must carry the operator label, not a default
        assert applied_entry["source"] == "test", applied_entry

        # Aggregate must reflect the applied wait. NOTE: ``case["cooldown_clamped"]``
        # is an OR across *all* cooldown decisions including the scheduler's
        # first-call ``zero_configured`` skipped entry, so it can legitimately
        # be True even when the transport_retry slot was fully honored. We
        # therefore assert on the *transport_retry-only* slice instead.
        transport_clamped_or_skipped = any(
            cd.get("clamped") or cd.get("skipped") for cd in transport_entries
        )
        assert transport_clamped_or_skipped is False, (
            f"every transport_retry decision should be fully honored, "
            f"got {transport_entries}"
        )
        # total_cooldown_ms must reflect the applied transport wait
        assert case["total_cooldown_ms"] >= 1, case["total_cooldown_ms"]
        # And the run-level total mirrors per-case
        assert rr["total_cooldown_ms"] >= case["total_cooldown_ms"], rr

        # Adapter must have been called at least 2x (initial + 1 retry).
        assert adapter.calls >= 2, adapter.calls
        print(
            f"    OK: applied_s={applied_entry['applied_s']:.3f}, "
            f"total_cooldown_ms={case['total_cooldown_ms']}, calls={adapter.calls}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario B — count-based retry exhaustion reason (NOT budget exhaustion)
# ===========================================================================

def test_count_based_retry_exhaustion_reason_in_artifact():
    """Force ``max_retries`` count to exhaust *without* tripping the budget
    rail. The artifact must show ``retry_decision_reason ==
    'transport_retry_exhausted'``, ``budget_exhausted is False``, and
    ``attempts_used == 1 + max_retries`` (the count-contract).

    The existing hardening tests cover the budget-exhaustion reason and the
    success reasons. This locks the orthogonal count-exhaustion reason so
    a future change that collapses the two reasons is caught.
    """
    print("  [B] count-based exhaustion: retry_decision_reason=transport_retry_exhausted")
    out = _temp_outdir("count_exhaustion")
    try:
        adapter = _AlwaysFailAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=2,                       # 2 retries → 3 attempts total
            total_deadline_s=30.0,               # very generous: never trips budget
            transport_retry_cooldown_s=0.01,     # tiny — no time pressure
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        case = rr["cases"][0]

        # The exact count contract: attempts == 1 + max_retries
        assert case["attempts_used"] == 3, (
            f"expected attempts_used=3 (1+2 retries), got {case['attempts_used']}"
        )
        # Count exhaustion reason — NOT budget exhaustion
        assert case["retry_decision_reason"] == "transport_retry_exhausted", (
            f"got reason={case['retry_decision_reason']!r}"
        )
        assert case["budget_exhausted"] is False, case
        # Run-level aggregate
        assert rr["total_attempts_used"] == 3, rr["total_attempts_used"]
        assert rr["total_budget_exhausted"] == 0, rr["total_budget_exhausted"]
        # And final_status is failed (the case never produced a parse)
        assert case["final_status"] == "failed", case["final_status"]

        # Sanity: the adapter was called exactly attempts_used times
        assert adapter.calls == 3, adapter.calls
        print("    OK: attempts_used=3, retry_decision_reason=transport_retry_exhausted")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario C — cooldown_decisions JSON-roundtrip shape contract
# ===========================================================================

def test_cooldown_decisions_dict_shape_after_json_roundtrip():
    """Every ``cooldown_decisions[i]`` dict, after a JSON roundtrip, must
    carry the canonical 7-field WaitDecision shape, ``kind`` must be one
    of the four normalized enum values, ``configured_s`` / ``applied_s``
    must be numeric, and ``source`` must be a non-empty string.

    This is a *shape* test, not a key-existence test: it locks the
    serialized contract that operators / log scrapers will parse against.
    A future change that adds a field is fine; one that drops or renames
    a field will fail here.
    """
    print("  [C] cooldown_decisions[i] shape after JSON roundtrip")
    out = _temp_outdir("cooldown_shape")
    try:
        adapter = _AlwaysFailAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=1,
            total_deadline_s=10.0,
            transport_retry_cooldown_s=0.02,
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        case = rr["cases"][0]
        cds = case["cooldown_decisions"]
        assert len(cds) >= 1, "no cooldown_decisions to validate"

        for i, cd in enumerate(cds):
            assert isinstance(cd, dict), f"cooldown_decisions[{i}] is not a dict"
            # Exact key set — no missing, no surprise rename
            assert set(cd.keys()) == WAIT_DECISION_KEYS, (
                f"cooldown_decisions[{i}] keys mismatch: "
                f"got {set(cd.keys())}, expected {WAIT_DECISION_KEYS}"
            )
            # kind must be one of the 4 normalized values
            assert cd["kind"] in KNOWN_WAIT_KINDS, (
                f"cooldown_decisions[{i}].kind={cd['kind']!r} not in "
                f"{sorted(KNOWN_WAIT_KINDS)}"
            )
            # numeric contract
            assert isinstance(cd["configured_s"], (int, float)) and not isinstance(cd["configured_s"], bool)
            assert isinstance(cd["applied_s"], (int, float)) and not isinstance(cd["applied_s"], bool)
            assert cd["configured_s"] >= 0.0
            assert cd["applied_s"] >= 0.0
            # boolean contract
            assert isinstance(cd["clamped"], bool)
            assert isinstance(cd["skipped"], bool)
            # string contract
            assert isinstance(cd["source"], str) and len(cd["source"]) > 0
            assert isinstance(cd["skip_reason"], str)   # may be "" but must be string
            # Internal consistency: skipped → applied_s == 0
            if cd["skipped"]:
                assert cd["applied_s"] == 0.0, cd
            # Internal consistency: clamped → applied_s < configured_s
            if cd["clamped"]:
                assert cd["applied_s"] < cd["configured_s"], cd
        print(f"    OK: validated {len(cds)} cooldown decisions, all canonical shape")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario D — health failure reason classification (non-timeout branch)
# ===========================================================================

def test_health_failure_reason_classification_connection_refused():
    """Stage a synthetic non-OK ``HealthProbeResult`` with reason
    ``connection_refused`` and verify the artifact carries that exact
    reason in both per-case and run-level ``health_failure_reason``.

    The hardening suite already covers the ``timeout`` branch. This locks
    the **second** classification path so a regression in
    ``_classify_urllib_exception`` that broke any non-timeout reason
    would be caught.
    """
    print("  [D] health_failure_reason: connection_refused classification flows to artifact")
    out = _temp_outdir("health_connection_refused")
    try:
        adapter = _SuccessAdapter()
        # Stage a non-OK probe BEFORE run_export so dispatcher's
        # _snapshot_health_probe pulls it via CountingAdapter.inner unwrap.
        adapter.last_health_probe_result = HealthProbeResult(
            available=False,
            reason=HEALTH_REASON_CONNECTION_REFUSED,
            elapsed_ms=12,
            timeout_used_s=5.0,
            detail="simulated refused on health probe",
        )

        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)

        # Per-case
        case = rr["cases"][0]
        assert case["health_failure_reason"] == HEALTH_REASON_CONNECTION_REFUSED, case
        assert _is_non_placeholder_string(case["health_failure_reason"])
        # Run-level rolled-up reason
        assert rr["health_failure_reason"] == HEALTH_REASON_CONNECTION_REFUSED, rr["health_failure_reason"]
        # And it's not the default-empty case (key exists with meaningful value)
        assert _is_non_placeholder_string(rr["health_failure_reason"])
        print(f"    OK: per-case + run-level reason='{HEALTH_REASON_CONNECTION_REFUSED}'")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario E — operator-supplied values survive end-to-end roundtrip with
# semantic-value bounds (not just key-exists)
# ===========================================================================

def test_operator_supplied_values_survive_roundtrip_with_semantic_bounds():
    """Drive ``run_export`` with non-default operator values for every
    explainability lever and assert each one appears in the JSON report
    at exactly the value the operator set, with semantic bounds:

      - ``effective_request_timeout_s == 2.5`` (the supplied float)
      - ``effective_health_timeout_s == 4.0``
      - ``effective_total_deadline_s == (1 + max_retries) * timeout``
        (i.e. the *derived* default formula, not None)
      - ``configured_transport_retry_cooldown_s == 0.07``
      - ``configured_scheduler_cooldown_heavy_s == 1.5``
      - ``configured_scheduler_cooldown_light_s == 0.25``
      - ``configured_fallback_retry_delay_s == 0.5``
      - ``cooldown_source == "test"``
      - ``policy_source_summary`` is a 4-key dict with non-default values
        for every slot (catches any future "source dropped to default")

    This scenario answers: "if I set X, does the artifact prove I set X?"
    Field-exists-but-default would have been silent without this.
    """
    print("  [E] operator-supplied values survive roundtrip with semantic bounds")
    out = _temp_outdir("operator_values")
    try:
        adapter = _SuccessAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://carry:42",
            base_url_source="env",
            api_key="x",
            model="m-test",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=2.5,
            request_timeout_source="cli",
            health_timeout_s=4.0,
            health_timeout_source="env",
            max_retries=3,
            transport_retry_cooldown_s=0.07,
            scheduler_cooldown_heavy_s=1.5,
            scheduler_cooldown_light_s=0.25,
            fallback_retry_delay_s=0.5,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)

        # ---- Effective values (post-policy) ------------------------------
        assert rr["effective_request_timeout_s"] == 2.5, rr["effective_request_timeout_s"]
        assert rr["effective_health_timeout_s"] == 4.0, rr["effective_health_timeout_s"]
        # Derived default: (1 + max_retries) * request_timeout = (1+3) * 2.5 = 10.0
        assert rr["effective_total_deadline_s"] == (1 + 3) * 2.5, rr["effective_total_deadline_s"]

        # ---- Configured cooldown sub-section -----------------------------
        assert rr["configured_transport_retry_cooldown_s"] == 0.07
        assert rr["configured_scheduler_cooldown_heavy_s"] == 1.5
        assert rr["configured_scheduler_cooldown_light_s"] == 0.25
        assert rr["configured_fallback_retry_delay_s"] == 0.5
        assert rr["cooldown_source"] == "test"

        # ---- policy_source_summary: all 4 slots non-default --------------
        ps = rr["policy_source_summary"]
        assert isinstance(ps, dict), f"policy_source_summary not a dict: {ps!r}"
        # Exact 4-key shape
        assert set(ps.keys()) == {
            "base_url_source", "request_timeout_source",
            "health_timeout_source", "cooldown_source",
        }, set(ps.keys())
        # Every slot non-default and matches what we set
        assert ps["base_url_source"] == "env"
        assert ps["request_timeout_source"] == "cli"
        assert ps["health_timeout_source"] == "env"
        assert ps["cooldown_source"] == "test"
        # And every slot is non-empty / non-placeholder
        for k, v in ps.items():
            assert _is_non_placeholder_string(v), f"policy_source_summary[{k}]={v!r} is placeholder"
            assert v != "default", f"policy_source_summary[{k}] dropped to 'default'"

        # ---- Per-case carry: each case must record the same effective_request_timeout_s
        case = rr["cases"][0]
        assert case["effective_request_timeout_s"] == 2.5, case["effective_request_timeout_s"]
        # And per-case cooldown source carries through
        assert case["transport_retry_cooldown_source"] == "test", case
        assert case["configured_transport_retry_cooldown_s"] == 0.07, case

        print("    OK: every operator value carried verbatim into the artifact")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario F — full health reason sweep (7 normalized values)
# ===========================================================================
#
# T-tranche-8 (2026-04-08): parametrized sweep that drives every normalized
# ``HEALTH_REASON_*`` constant through a full ``run_export`` roundtrip and
# verifies the JSON artifact pins both per-case and run-level values.
#
# Contract (re-verified against src/app/llm/adapters/vllm_http.py and
# src/app/review/export_runtime.py::case_telemetry_from_result):
#
#   - ``HealthProbeResult.available == True`` + ``reason == "ok"``:
#         ``case_telemetry_from_result`` intentionally leaves
#         ``health_failure_reason`` as ``None`` because the
#         ``if hp and not hp.get("available", True)`` branch is skipped.
#         Run-level aggregator then keeps it at ``None`` too.
#
#   - Any ``available == False`` reason (timeout / connection_refused /
#         dns_failure / http_error / malformed_response / unexpected_error):
#         ``health_failure_reason`` becomes the exact normalized string,
#         and the run-level rolling aggregator copies it across.
#
# The row table below is read by pytest.parametrize and by a secondary
# drift-prevention test that checks its completeness against the set of
# normalized constants imported from the adapter module.

_HEALTH_SWEEP_ROWS = [
    # (label, available, reason_constant,
    #  expected_case_health_failure_reason,
    #  expected_run_health_failure_reason)
    ("ok",                  True,  HEALTH_REASON_OK,                 None,                             None),
    ("timeout",             False, HEALTH_REASON_TIMEOUT,            HEALTH_REASON_TIMEOUT,            HEALTH_REASON_TIMEOUT),
    ("connection_refused",  False, HEALTH_REASON_CONNECTION_REFUSED, HEALTH_REASON_CONNECTION_REFUSED, HEALTH_REASON_CONNECTION_REFUSED),
    ("dns_failure",         False, HEALTH_REASON_DNS_FAILURE,        HEALTH_REASON_DNS_FAILURE,        HEALTH_REASON_DNS_FAILURE),
    ("http_error",          False, HEALTH_REASON_HTTP_ERROR,         HEALTH_REASON_HTTP_ERROR,         HEALTH_REASON_HTTP_ERROR),
    ("malformed_response",  False, HEALTH_REASON_MALFORMED_RESPONSE, HEALTH_REASON_MALFORMED_RESPONSE, HEALTH_REASON_MALFORMED_RESPONSE),
    ("unexpected_error",    False, HEALTH_REASON_UNEXPECTED_ERROR,   HEALTH_REASON_UNEXPECTED_ERROR,   HEALTH_REASON_UNEXPECTED_ERROR),
]


@pytest.mark.parametrize(
    "label,available,reason_constant,expected_case_reason,expected_run_reason",
    _HEALTH_SWEEP_ROWS,
    ids=[row[0] for row in _HEALTH_SWEEP_ROWS],
)
def test_health_reason_sweep_survives_to_artifact(
    label, available, reason_constant, expected_case_reason, expected_run_reason,
):
    """Drive every normalized health reason through a full ``run_export``
    JSON roundtrip and verify the per-case + run-level values match the
    contract exactly. Failure branches must carry the normalized string;
    the ``ok`` branch must carry ``None`` per the export_runtime rule that
    only populates ``health_failure_reason`` when ``available=False``.
    """
    print(f"  [F:{label}] health reason {reason_constant!r} → artifact")
    out = _temp_outdir(f"health_sweep_{label}")
    try:
        adapter = _SuccessAdapter()
        # Stage the probe before run_export so dispatcher's
        # _snapshot_health_probe pulls it via CountingAdapter.inner unwrap.
        adapter.last_health_probe_result = HealthProbeResult(
            available=available,
            reason=reason_constant,
            elapsed_ms=12,
            timeout_used_s=5.0,
            detail=f"simulated {label} for T-tranche-8 sweep",
        )

        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        case = rr["cases"][0]

        # ---- Per-case semantic value -----------------------------------
        # For failure reasons: exact normalized string, non-placeholder.
        # For ``ok``: None per the export_runtime contract.
        assert case["health_failure_reason"] == expected_case_reason, (
            f"case health_failure_reason drift for {label}: "
            f"got {case['health_failure_reason']!r}, expected {expected_case_reason!r}"
        )
        if expected_case_reason is not None:
            assert _is_non_placeholder_string(case["health_failure_reason"]), (
                f"{label} case reason is placeholder"
            )

        # ---- Run-level semantic value ----------------------------------
        assert rr["health_failure_reason"] == expected_run_reason, (
            f"run-level health_failure_reason drift for {label}: "
            f"got {rr['health_failure_reason']!r}, expected {expected_run_reason!r}"
        )
        if expected_run_reason is not None:
            assert _is_non_placeholder_string(rr["health_failure_reason"])

        print(f"    OK: {label} → case={case['health_failure_reason']!r}, run={rr['health_failure_reason']!r}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_health_sweep_covers_every_normalized_reason_constant():
    """Drift-prevention: the parametrize table above must cover the complete
    set of ``HEALTH_REASON_*`` constants exported from the adapter module.
    If someone adds or removes a normalized reason, this test fires before
    the sweep can silently lose coverage of a new branch.
    """
    print("  [F/drift] parametrize table covers every HEALTH_REASON_* constant")
    import src.app.llm.adapters.vllm_http as vllm_http_mod
    exported = {
        getattr(vllm_http_mod, name)
        for name in dir(vllm_http_mod)
        if name.startswith("HEALTH_REASON_")
    }
    in_table = {row[2] for row in _HEALTH_SWEEP_ROWS}
    missing = exported - in_table
    extra = in_table - exported
    assert not missing, (
        f"health sweep table is missing these normalized reasons: {sorted(missing)}. "
        f"Add them to _HEALTH_SWEEP_ROWS with explicit expected values."
    )
    assert not extra, (
        f"health sweep table has reasons not in the adapter module: {sorted(extra)}. "
        f"These constants may have been renamed or removed."
    )
    assert len(_HEALTH_SWEEP_ROWS) == len(exported)
    print(f"    OK: {len(exported)} normalized reasons all in table")


# ===========================================================================
# Scenario G — scheduler heavy wait E2E, applied_s > 0 in artifact
# ===========================================================================

def test_scheduler_heavy_wait_applied_path_reaches_artifact():
    """Run two HEAVY cases back-to-back with ``scheduler_cooldown_heavy_s =
    0.1`` and a generous deadline. The second case's
    ``cooldown_decisions`` list must contain a ``scheduler_heavy`` entry
    with ``applied_s > 0``, ``skipped=False``, ``clamped=False``, and the
    operator-supplied ``source`` label. The run-level
    ``total_cooldown_ms`` aggregate must also reflect the applied wait.

    Why two HEAVY cases
    -------------------
    ``Scheduler.post_execute(request)`` sets ``_last_was_heavy =
    (request.task_type in HEAVY_TASKS)``. On the next ``pre_execute``,
    ``kind = WAIT_KIND_SCHEDULER_HEAVY`` iff ``_last_was_heavy`` was True.
    ``builder.requirement_parse`` is in HEAVY_TASKS, so two of those in
    sequence deterministically forces the heavy branch on case 2.
    """
    print("  [G] scheduler_heavy wait applied_s>0 reaches cooldown_decisions in JSON")
    out = _temp_outdir("scheduler_heavy_applied")
    try:
        # Two HEAVY cases → case 2 sees a scheduler_heavy wait.
        heavy_cases = [
            ("builder", "requirement_parse", "HR-heavy-1"),
            ("builder", "requirement_parse", "HR-heavy-2"),
        ]
        adapter = _SuccessAdapter()
        ehr.run_export(
            cases=heavy_cases,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=5.0,
            max_retries=0,
            total_deadline_s=30.0,               # generous: wait fully honored
            transport_retry_cooldown_s=0.0,      # isolate scheduler path
            scheduler_cooldown_heavy_s=0.1,      # small but > 0 → applied_s > 0
            scheduler_cooldown_light_s=0.0,
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        assert len(rr["cases"]) == 2

        # Case 1 has no prior execute → scheduler decision is skipped
        # (zero_configured). Case 2 is the one that carries the applied
        # scheduler_heavy wait.
        case2 = rr["cases"][1]
        cds = case2["cooldown_decisions"]
        assert isinstance(cds, list) and len(cds) >= 1, cds

        heavy_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_SCHEDULER_HEAVY]
        assert len(heavy_entries) >= 1, (
            f"case 2 cooldown_decisions must include a scheduler_heavy entry, "
            f"got kinds={[cd.get('kind') for cd in cds]}"
        )
        h = heavy_entries[0]

        # Semantic value assertions
        assert _is_finite_positive_float(h["applied_s"]), (
            f"scheduler_heavy applied_s must be > 0 on the applied path, got {h}"
        )
        assert h["skipped"] is False, h
        assert h["clamped"] is False, h
        assert h["source"] == "test", (
            f"scheduler source label must carry operator value, got {h['source']!r}"
        )
        assert h["configured_s"] > 0, h
        # Internal consistency: applied_s must not exceed configured_s
        # for the fully-honored path.
        assert h["applied_s"] <= h["configured_s"] + 1e-9, h
        # skip_reason is empty string for the fully-honored path
        assert h["skip_reason"] == "", h

        # Per-case aggregate must reflect the scheduler wait
        assert case2["total_cooldown_ms"] >= 1, case2["total_cooldown_ms"]

        # Run-level aggregate must include case 2's contribution
        assert rr["total_cooldown_ms"] >= case2["total_cooldown_ms"], rr

        print(
            f"    OK: scheduler_heavy applied_s={h['applied_s']:.3f}s "
            f"(configured={h['configured_s']}s) → total_cooldown_ms={case2['total_cooldown_ms']}"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario H — scheduler light wait E2E, applied_s > 0 in artifact
# ===========================================================================

def test_scheduler_light_wait_applied_path_reaches_artifact():
    """Same as G but for ``scheduler_light``. The ``kind`` is determined by
    whether the **previous** task was heavy; a non-heavy first case flips
    ``_last_was_heavy`` to False, so case 2 gets ``scheduler_light``.

    ``builder.patch_intent_parse`` is explicitly in LIGHT_TASKS (not in
    HEAVY_TASKS), which deterministically produces the light branch.
    """
    print("  [H] scheduler_light wait applied_s>0 reaches cooldown_decisions in JSON")
    out = _temp_outdir("scheduler_light_applied")
    try:
        # A light (non-heavy) task first, then any task → case 2 gets
        # scheduler_light kind.
        light_cases = [
            ("builder", "patch_intent_parse", "HR-light-1"),
            ("builder", "patch_intent_parse", "HR-light-2"),
        ]
        adapter = _SuccessAdapter()
        ehr.run_export(
            cases=light_cases,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=5.0,
            max_retries=0,
            total_deadline_s=30.0,
            transport_retry_cooldown_s=0.0,
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.1,     # small but > 0 → applied_s > 0
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        case2 = rr["cases"][1]
        cds = case2["cooldown_decisions"]
        light_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_SCHEDULER_LIGHT]
        assert len(light_entries) >= 1, (
            f"case 2 cooldown_decisions must include a scheduler_light entry, "
            f"got kinds={[cd.get('kind') for cd in cds]}"
        )
        l = light_entries[0]

        assert _is_finite_positive_float(l["applied_s"]), l
        assert l["skipped"] is False, l
        assert l["clamped"] is False, l
        assert l["source"] == "test", l
        assert l["configured_s"] > 0, l
        assert l["applied_s"] <= l["configured_s"] + 1e-9, l
        assert l["skip_reason"] == "", l

        assert case2["total_cooldown_ms"] >= 1, case2
        assert rr["total_cooldown_ms"] >= case2["total_cooldown_ms"], rr

        print(
            f"    OK: scheduler_light applied_s={l['applied_s']:.3f}s "
            f"(configured={l['configured_s']}s)"
        )
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
# Scenario I — fallback WaitDecision JSON-shape contract
# ===========================================================================

def test_fallback_wait_decision_serializes_with_canonical_shape():
    """``DegradedModeHandler.handle_failure(...)`` produces a
    ``WaitDecision`` on ``self.last_wait_decision``. The current handler
    intentionally does **not** sleep (silent-fallback risk avoidance), so
    ``applied_s == 0.0`` and ``skipped is True`` are the documented
    invariants. This test pins the **JSON shape contract** for that
    decision so a future change cannot silently drop a key, rename
    ``kind``, or break ``WaitDecision.to_dict()``.

    Why no full export-artifact roundtrip
    --------------------------------------
    ``run_export`` does not invoke ``DegradedModeHandler`` in its current
    flow — fallback is only used by the live API path via
    ``bootstrap.Container``. So locking "fallback decision survives to
    export_run_report.json" would require either changing production
    runtime or building a fake dispatcher path. Both are out of scope.
    Instead this test locks the *next-best-thing*: the dict that ``handle
    failure`` populates is JSON-serialization-stable and matches the same
    canonical 7-key WaitDecision shape every other wait kind uses, so the
    moment a future change wires fallback into the dispatcher cooldown
    merge, the value will already serialize correctly.
    """
    print("  [I] DegradedModeHandler fallback WaitDecision canonical JSON shape")
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy
    from src.app.core.contracts import TaskRequest

    p = UnifiedTimeoutPolicy(fallback_retry_delay_s=0.42, cooldown_source="test")
    h = DegradedModeHandler(policy=p)
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="x")
    h.handle_failure(req, errors=["simulated"])

    # Memory object exists and is a real WaitDecision
    decision = h.last_wait_decision
    assert decision is not None, "handle_failure must populate last_wait_decision"

    # JSON roundtrip — proves the dict is serialization-stable
    decision_dict = decision.to_dict()
    roundtripped = json.loads(json.dumps(decision_dict))

    # Canonical 7-key WaitDecision shape (same as scheduler / transport_retry)
    assert set(roundtripped.keys()) == WAIT_DECISION_KEYS, (
        f"fallback WaitDecision keys mismatch: got {set(roundtripped.keys())}"
    )
    # kind: must be the normalized fallback enum value
    assert roundtripped["kind"] == WAIT_KIND_FALLBACK_RETRY, roundtripped
    # configured_s: carries the operator/policy value
    assert roundtripped["configured_s"] == 0.42, roundtripped
    # applied_s: handler does not sleep (current contract) → 0.0
    assert roundtripped["applied_s"] == 0.0, roundtripped
    # skipped: True since applied_s == 0 with non-zero configured
    assert roundtripped["skipped"] is True, roundtripped
    # clamped: not on the clamp path (no deadline math here)
    assert roundtripped["clamped"] is False, roundtripped
    # source: must carry the policy source label
    assert roundtripped["source"] == "test", roundtripped
    # skip_reason: must be a string (zero_configured per the handler)
    assert isinstance(roundtripped["skip_reason"], str)
    assert roundtripped["skip_reason"] == "zero_configured", roundtripped

    # Numeric / boolean type contract
    assert isinstance(roundtripped["configured_s"], (int, float))
    assert isinstance(roundtripped["applied_s"], (int, float))
    assert isinstance(roundtripped["clamped"], bool)
    assert isinstance(roundtripped["skipped"], bool)
    print("    OK: 7-key shape, kind=fallback_retry, applied_s=0, skipped=True, source=test")


def test_fallback_wait_decision_source_default_when_no_policy():
    """Drift-prevention: when ``DegradedModeHandler`` is built via the
    legacy constructor (no ``policy=``), the emitted ``WaitDecision`` carries
    ``source == "default"`` (matching ``fallback_retry_delay_source``'s
    default), not ``"test"`` or anything else. This locks the asymmetry
    between policy-driven and constructor-default sources.
    """
    print("  [I/legacy] DegradedModeHandler legacy constructor → source='default'")
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.core.contracts import TaskRequest

    h = DegradedModeHandler()
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="x")
    h.handle_failure(req, errors=[])
    assert h.last_wait_decision is not None
    d = h.last_wait_decision.to_dict()
    assert d["kind"] == WAIT_KIND_FALLBACK_RETRY
    assert d["source"] == "default", d
    assert d["applied_s"] == 0.0
    assert d["skipped"] is True
    print("    OK")


# ===========================================================================
# Scenario J — settings-driven policy source survives end-to-end to artifact
# ===========================================================================

def test_settings_built_policy_source_settings_survives_to_artifact():
    """Build a ``UnifiedTimeoutPolicy`` via ``bootstrap._build_unified_policy(
    settings)`` (the same path the live ``Container`` takes), thread the
    same source label into ``run_export``, and verify the resulting
    ``export_run_report.json`` carries:

      - run-level ``cooldown_source == "settings"``
      - run-level ``configured_transport_retry_cooldown_s`` ==
        ``settings.fallback.retry_delay_s``
      - run-level ``configured_fallback_retry_delay_s`` ==
        ``settings.fallback.retry_delay_s``
      - run-level ``policy_source_summary["cooldown_source"] == "settings"``
      - per-case ``transport_retry_cooldown_source == "settings"``

    The hardening suite already verifies that ``Container`` *constructs*
    these objects with the right source label (memory-level). T-tranche-7
    scenario E verifies that operator-supplied values survive a
    roundtrip. This test closes the third corner: the **settings-derived**
    label flows through the *export pipeline* to *disk* artifact.
    """
    print("  [J] settings-derived policy source 'settings' reaches export_run_report.json")
    out = _temp_outdir("settings_source_survival")
    try:
        from src.app.bootstrap import _build_unified_policy
        from src.app.settings import AppSettings

        # Synthesize settings as the live container would. Bumping
        # retry_delay_s gives us a non-default value to assert against
        # exactly, so a default-fallback drift would fail the assertion.
        settings = AppSettings()
        settings.fallback.retry_delay_s = 0.13   # arbitrary distinctive value

        policy = _build_unified_policy(settings)
        # Sanity at memory level — _build_unified_policy contract.
        assert policy.cooldown_source == "settings"
        assert policy.transport_retry_cooldown_s == 0.13
        assert policy.fallback_retry_delay_s == 0.13

        # Now drive run_export with the policy-derived values + the same
        # source label. ``adapter_override`` keeps it deterministic and
        # default-gate-eligible (no live LLM).
        adapter = _SuccessAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="settings",          # match the policy source
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=float(policy.request_timeout_s),
            request_timeout_source="settings",
            health_timeout_s=float(policy.health_timeout_s),
            health_timeout_source="settings",
            max_retries=int(policy.max_retries),
            transport_retry_cooldown_s=float(policy.transport_retry_cooldown_s),
            scheduler_cooldown_heavy_s=0.0,
            scheduler_cooldown_light_s=0.0,
            fallback_retry_delay_s=float(policy.fallback_retry_delay_s),
            cooldown_source=policy.cooldown_source,
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)

        # ---- Run-level: cooldown source = "settings" survives ------------
        assert rr["cooldown_source"] == "settings", rr["cooldown_source"]
        assert rr["configured_transport_retry_cooldown_s"] == 0.13
        assert rr["configured_fallback_retry_delay_s"] == 0.13

        # ---- policy_source_summary: 4-key dict, all "settings" ----------
        ps = rr["policy_source_summary"]
        assert isinstance(ps, dict) and set(ps.keys()) == {
            "base_url_source", "request_timeout_source",
            "health_timeout_source", "cooldown_source",
        }, ps
        assert ps["base_url_source"] == "settings"
        assert ps["request_timeout_source"] == "settings"
        assert ps["health_timeout_source"] == "settings"
        assert ps["cooldown_source"] == "settings"
        # Every value non-empty / non-placeholder
        for k, v in ps.items():
            assert _is_non_placeholder_string(v), f"policy_source_summary[{k}]={v!r}"

        # ---- Per-case: cooldown source label carries -----------------
        case = rr["cases"][0]
        assert case["transport_retry_cooldown_source"] == "settings", case
        assert case["configured_transport_retry_cooldown_s"] == 0.13, case
        print("    OK: cooldown_source='settings' end-to-end, all 4 source slots = 'settings'")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_fallback_wait_reaches_artifact_cooldown_decisions_on_failure():
    """T-tranche-10 production closure: drive a failing case through
    ``run_export`` and verify the resulting ``export_run_report.json``
    actually contains a ``fallback_retry`` entry in the per-case
    ``cooldown_decisions`` list. This is the artifact-E2E counterpart
    to the T-tranche-9 shape-only test.

    Chain verified
    --------------
    1. ``_AlwaysFailAdapter`` raises on every ``generate`` → LLMClient's
       retry loop exhausts (count-based, not budget) → dispatcher's
       ``parsed is None`` failure branch is taken.
    2. The dispatcher now (T-tranche-10) calls
       ``self.fallback.record_wait_decision()`` on that branch, which
       populates ``DegradedModeHandler.last_wait_decision``.
    3. ``_merge_fallback_wait_into_retry_decision(rd_dict, self.fallback)``
       appends the fallback decision dict to ``cooldown_decisions`` and
       adds ``applied_s × 1000`` to ``total_cooldown_ms``.
    4. ``TaskResult.retry_decision = rd_dict`` → ``case_telemetry_from_result``
       copies ``cooldown_decisions`` into ``CaseTelemetry`` →
       ``RunTelemetry.to_dict`` → ``write_run_report`` → disk →
       ``json.loads``.

    Semantic assertions
    -------------------
      - per-case ``cooldown_decisions`` has exactly one
        ``kind="fallback_retry"`` entry (count == 1; multi-entry would
        mean double-merging)
      - the entry has the canonical 7-key WaitDecision shape
      - ``applied_s == 0.0`` and ``skipped is True`` (runtime semantics
        unchanged — the handler still doesn't sleep)
      - ``skip_reason == "zero_configured"``
      - ``source`` carries the operator-supplied cooldown_source label
      - ``configured_s`` equals the operator-supplied
        ``fallback_retry_delay_s``
      - ``total_cooldown_ms`` is unchanged by the fallback merge
        (because applied_s=0); the aggregate reflects only wait slots
        that actually consumed wall-clock
    """
    print("  [K] fallback_retry entry reaches artifact cooldown_decisions on failure")
    out = _temp_outdir("fallback_artifact_e2e")
    try:
        adapter = _AlwaysFailAdapter()
        ehr.run_export(
            cases=ONE_CASE,
            base_url="http://test:1",
            base_url_source="test",
            api_key="x",
            model="m",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=0,                      # single attempt, parse-fail → dispatcher failure path
            total_deadline_s=10.0,              # generous: not a budget-exhaust test
            transport_retry_cooldown_s=0.0,     # isolate fallback entry from transport_retry entries
            scheduler_cooldown_heavy_s=0.0,     # isolate fallback entry from scheduler entries
            scheduler_cooldown_light_s=0.0,
            fallback_retry_delay_s=0.42,        # non-default so default-drift would fail
            cooldown_source="test",
            adapter_override=adapter,
            used_mock_override=False,
        )
        rr = _read_report(out)
        assert len(rr["cases"]) == 1
        case = rr["cases"][0]
        # Case must actually be on a failure path; otherwise the test is
        # asserting the wrong branch.
        assert case["final_status"] == "failed", case["final_status"]

        cds = case["cooldown_decisions"]
        assert isinstance(cds, list) and len(cds) >= 1, cds

        fallback_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY]
        assert len(fallback_entries) == 1, (
            f"expected exactly one fallback_retry entry, got "
            f"{[cd.get('kind') for cd in cds]}"
        )
        fb = fallback_entries[0]

        # Canonical 7-key WaitDecision shape (same as every other wait kind)
        assert set(fb.keys()) == WAIT_DECISION_KEYS, fb

        # Semantic-value assertions
        assert fb["configured_s"] == 0.42, fb
        assert fb["applied_s"] == 0.0, fb
        assert fb["skipped"] is True, fb
        assert fb["clamped"] is False, fb
        assert fb["skip_reason"] == "zero_configured", fb
        assert fb["source"] == "test", fb

        # total_cooldown_ms is unchanged by a zero-applied fallback entry.
        # The existing transport-retry and scheduler slots are all 0 in
        # this scenario, so the aggregate stays at 0.
        assert case["total_cooldown_ms"] == 0, case["total_cooldown_ms"]
        assert rr["total_cooldown_ms"] == 0, rr["total_cooldown_ms"]

        # And the run report still honors T-tranche-7's invariant that the
        # *configured* fallback delay (a run-level field, not per-case)
        # survives the roundtrip at the operator-supplied value.
        assert rr["configured_fallback_retry_delay_s"] == 0.42, rr["configured_fallback_retry_delay_s"]
        assert rr["cooldown_source"] == "test", rr["cooldown_source"]
        print("    OK: fallback_retry entry in artifact, canonical shape, applied_s=0 → total=0")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_shed_branch_carries_fallback_wait_in_task_result_retry_decision():
    """T-tranche-11: the third Dispatcher failure exit site is
    ``dispatch()``'s ``OverloadError`` queue-shed branch. Unlike the
    two ``_execute()`` failures, the LLMClient never ran here, so
    there is no ``last_retry_decision`` to seed from — the dispatcher
    synthesizes an empty ``rd_dict`` on-the-fly and attaches the
    fallback wait into it.

    The test triggers the SHED branch deterministically by installing
    a fake ``QueueManager`` whose ``submit`` raises ``OverloadError``,
    then asserts the returned ``TaskResult.retry_decision`` carries a
    canonical 7-key fallback entry with semantic values intact.

    This is the **runtime** counterpart to the static AST drift check
    in ``tests/unit/test_dispatcher_failure_exit_inventory.py``. The
    static layer guarantees the wiring is *present in source*; this
    runtime layer guarantees the wiring actually *fires in practice*.
    """
    print("  [L] SHED branch: fallback_retry entry in TaskResult.retry_decision")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy, TimeoutPolicy
    from src.app.core.errors import OverloadError
    from src.app.core.contracts import TaskRequest
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    # Minimal fakes — no LLM, no real queue.
    class _SubmitOverloadQueue:
        """Queue stub whose ``submit`` always raises OverloadError so the
        dispatch() shed branch fires deterministically."""
        def submit(self, request, handler):
            raise OverloadError("deterministic shed for T-tranche-11")

    class _NullScheduler:
        last_wait_decision = None
        def pre_execute(self, *a, **k): pass
        def post_execute(self, *a, **k): pass

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    # Fallback with a non-default configured value so the carry is observable.
    fb = DegradedModeHandler(policy=UnifiedTimeoutPolicy(
        fallback_retry_delay_s=0.37,
        cooldown_source="test",
    ))

    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=_SubmitOverloadQueue(),
        scheduler=_NullScheduler(),
        timeouts=TimeoutPolicy(),
        fallback=fb,
    )

    # Dummy spec — dispatch() short-circuits at queue.submit before
    # spec gets touched, so any TaskSpec shape works.
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

    # Runtime semantics unchanged: still SHED, not DEGRADED.
    assert result.status == TaskStatus.SHED, result.status
    # retry_decision was synthesized for the fallback merge.
    rd = result.retry_decision
    assert rd is not None, "shed branch must attach retry_decision now that fallback is wired"
    assert isinstance(rd, dict)

    cds = rd.get("cooldown_decisions") or []
    fallback_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY]
    assert len(fallback_entries) == 1, (
        f"expected exactly one fallback_retry entry, got cooldown_decisions={cds}"
    )
    fb_cd = fallback_entries[0]
    # Canonical 7-key shape
    assert set(fb_cd.keys()) == WAIT_DECISION_KEYS, fb_cd
    # Semantic values
    assert fb_cd["kind"] == WAIT_KIND_FALLBACK_RETRY
    assert fb_cd["configured_s"] == 0.37, fb_cd
    assert fb_cd["applied_s"] == 0.0, fb_cd
    assert fb_cd["skipped"] is True, fb_cd
    assert fb_cd["clamped"] is False, fb_cd
    assert fb_cd["skip_reason"] == "zero_configured", fb_cd
    assert fb_cd["source"] == "test", fb_cd
    # No wall-clock consumed → total_cooldown_ms stays 0
    assert rd.get("total_cooldown_ms", 0) == 0, rd
    print(
        f"    OK: SHED result.retry_decision carries fallback_retry entry "
        f"(configured_s={fb_cd['configured_s']}, source={fb_cd['source']!r})"
    )


def test_post_hoc_fallback_merge_fires_when_handler_exception_escapes_to_queue():
    """T-tranche-12 runtime closure: if ``Dispatcher._execute`` (the
    handler passed to ``QueueManager.submit``) ever lets an exception
    escape, ``QueueManager.submit``'s catch-all branch synthesizes
    its own ``TaskResult(status=ERROR, retry_decision=None)`` and
    returns it up through ``Dispatcher.dispatch``. The T-tranche-12
    post-hoc wrapper detects that ``retry_decision is None`` on a
    failure result and enriches it with a fallback ``WaitDecision``
    slot in-place.

    The test triggers this by monkey-patching the dispatcher instance's
    ``_execute`` attribute to a function that raises ``RuntimeError``.
    Python's attribute lookup resolves the instance attribute before
    the class method, so the inline ``handler`` closure inside
    ``dispatch()`` sees the raising version.

    This is the **runtime** counterpart to the queue_manager AST
    inventory layer in ``tests/unit/test_queue_manager_failure_exit_inventory.py``.
    """
    print("  [M] post-hoc wrapper: handler exception → queue ERROR → fallback merge")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy, TimeoutPolicy
    from src.app.execution.queue_manager import QueueManager
    from src.app.execution.scheduler import Scheduler
    from src.app.core.contracts import TaskRequest
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    # Real QueueManager so its catch-all exception branch is the path
    # under test. Real Scheduler to avoid nulling fallback-unrelated state.
    queue = QueueManager(max_concurrency=1, max_depth=10)
    scheduler = Scheduler(policy=UnifiedTimeoutPolicy(cooldown_source="test"))

    fb = DegradedModeHandler(policy=UnifiedTimeoutPolicy(
        fallback_retry_delay_s=0.21,
        cooldown_source="test",
    ))
    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=queue,
        scheduler=scheduler,
        timeouts=TimeoutPolicy(),
        fallback=fb,
    )

    # Monkey-patch the instance's _execute so the handler closure inside
    # dispatch() resolves to the raising version. Python attribute lookup
    # checks the instance dict before the class, so this works for the
    # ``self._execute(req, spec)`` call inside handler().
    def _raise_execute(req, spec, **kwargs):
        raise RuntimeError("simulated _execute exception for T-tranche-12")
    d._execute = _raise_execute

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

    # Runtime semantics unchanged: still ERROR from queue_manager's
    # catch-all. The dispatcher wrapper does NOT switch to DEGRADED.
    assert result.status == TaskStatus.ERROR, result.status
    assert "simulated _execute exception" in " ".join(result.errors or [])

    # T-tranche-12 post-hoc wrapper attached a synthesized retry_decision.
    rd = result.retry_decision
    assert rd is not None, (
        "post-hoc wrapper must synthesize retry_decision on failure results "
        "that came back from queue.submit with retry_decision=None"
    )
    cds = rd.get("cooldown_decisions") or []
    fallback_entries = [cd for cd in cds if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY]
    assert len(fallback_entries) == 1, (
        f"expected exactly one fallback_retry entry, got cooldown_decisions={cds}"
    )
    fb_cd = fallback_entries[0]
    assert set(fb_cd.keys()) == WAIT_DECISION_KEYS, fb_cd
    assert fb_cd["kind"] == WAIT_KIND_FALLBACK_RETRY
    assert fb_cd["configured_s"] == 0.21, fb_cd
    assert fb_cd["applied_s"] == 0.0, fb_cd
    assert fb_cd["skipped"] is True, fb_cd
    assert fb_cd["clamped"] is False, fb_cd
    assert fb_cd["skip_reason"] == "zero_configured", fb_cd
    assert fb_cd["source"] == "test", fb_cd
    assert rd.get("total_cooldown_ms", 0) == 0, rd
    print(
        f"    OK: handler exception → ERROR result → fallback_retry merged "
        f"(configured_s={fb_cd['configured_s']})"
    )


def test_post_hoc_wrapper_is_noop_on_dispatcher_produced_failure_results():
    """The T-tranche-12 post-hoc wrapper must NOT double-merge fallback
    into failure results that already carry a ``retry_decision`` from
    ``_execute``'s own failure exit sites (CircuitOpenError, parse-fail).
    The guard is ``retry_decision is None``, so results that came from
    ``_execute`` with a non-None ``rd_dict`` are left alone.

    This test feeds a synthetic dispatcher whose ``_execute`` returns a
    pre-merged ``TaskResult(status=ERROR, retry_decision={...})`` and
    verifies the wrapper does not re-enrich it (otherwise each ERROR
    case would accumulate two fallback_retry entries).
    """
    print("  [M/idempotent] post-hoc wrapper is a no-op when retry_decision already present")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy, TimeoutPolicy
    from src.app.execution.queue_manager import QueueManager
    from src.app.execution.scheduler import Scheduler
    from src.app.core.contracts import TaskRequest, TaskResult
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    queue = QueueManager(max_concurrency=1, max_depth=10)
    scheduler = Scheduler(policy=UnifiedTimeoutPolicy(cooldown_source="test"))
    fb = DegradedModeHandler(policy=UnifiedTimeoutPolicy(
        fallback_retry_delay_s=0.21, cooldown_source="test",
    ))
    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=queue,
        scheduler=scheduler,
        timeouts=TimeoutPolicy(),
        fallback=fb,
    )

    # Simulate _execute's own failure path: return a TaskResult that
    # already has retry_decision populated (as T-tranche-10's wiring does).
    pre_merged_rd = {
        "cooldown_decisions": [
            {"kind": WAIT_KIND_FALLBACK_RETRY, "configured_s": 0.21,
             "applied_s": 0.0, "clamped": False, "skipped": True,
             "skip_reason": "zero_configured", "source": "test"}
        ],
        "total_cooldown_ms": 0,
    }
    def _fake_execute(req, spec, **kwargs):
        return TaskResult(
            request_id=req.request_id,
            task_id=req.task_id,
            task_type=req.task_type,
            status=TaskStatus.ERROR,
            errors=["pre-merged failure"],
            retry_decision=pre_merged_rd,
        )
    d._execute = _fake_execute

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

    # Same status, same rd_dict reference — post-hoc wrapper skipped it.
    assert result.status == TaskStatus.ERROR
    rd = result.retry_decision
    assert rd is not None
    fallback_entries = [
        cd for cd in rd.get("cooldown_decisions", [])
        if cd.get("kind") == WAIT_KIND_FALLBACK_RETRY
    ]
    assert len(fallback_entries) == 1, (
        f"expected exactly one fallback_retry entry (no double-merge), "
        f"got {len(fallback_entries)}: {rd}"
    )
    print("    OK: pre-merged retry_decision is left untouched (idempotent)")


def test_shed_branch_noop_when_dispatcher_has_no_fallback():
    """Back-compat drift check: a Dispatcher built without ``fallback=``
    (i.e. external callers that don't yet pass it) must still handle the
    shed branch cleanly. The ``retry_decision`` in that case is ``None``
    (no synthesis) and the TaskResult still carries ``status=SHED``.
    """
    print("  [L/legacy] SHED branch is None-safe when Dispatcher.fallback is None")
    from src.app.orchestration.dispatcher import Dispatcher
    from src.app.execution.timeouts import TimeoutPolicy
    from src.app.core.errors import OverloadError
    from src.app.core.contracts import TaskRequest
    from src.app.core.enums import TaskStatus
    from src.app.domain.registry import TaskSpec

    class _SubmitOverloadQueue:
        def submit(self, request, handler):
            raise OverloadError("deterministic shed")

    class _NullScheduler:
        last_wait_decision = None
        def pre_execute(self, *a, **k): pass
        def post_execute(self, *a, **k): pass

    class _NullLLM:
        last_retry_decision = None
        adapter = None

    d = Dispatcher(
        llm_client=_NullLLM(),
        queue=_SubmitOverloadQueue(),
        scheduler=_NullScheduler(),
        timeouts=TimeoutPolicy(),
        # no fallback= → back-compat default None
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
    # With no fallback, retry_decision stays None (no synthesis path ran).
    assert result.retry_decision is None, result.retry_decision
    print("    OK: no fallback → retry_decision is None, status still SHED")


def test_dispatcher_fallback_merge_helper_is_additive_and_noop_when_fallback_absent():
    """Drift-prevention: ``_merge_fallback_wait_into_retry_decision`` must
    be a no-op when ``fallback is None`` (back-compat for external callers
    that build ``Dispatcher`` without the optional kwarg) and must not
    touch ``rd_dict`` when ``fallback.last_wait_decision`` is None."""
    print("  [K/helper] _merge_fallback_wait_into_retry_decision is additive and None-safe")
    from src.app.orchestration.dispatcher import _merge_fallback_wait_into_retry_decision
    from src.app.fallback.degraded_modes import DegradedModeHandler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy

    base = {"cooldown_decisions": [{"kind": "transport_retry", "applied_s": 0.05}], "total_cooldown_ms": 50}

    # 1. fallback=None → returns unchanged
    out1 = _merge_fallback_wait_into_retry_decision(base, None)
    assert out1 == base
    assert out1 is base                 # same object, no copy

    # 2. fallback present but last_wait_decision=None → unchanged
    h = DegradedModeHandler(policy=UnifiedTimeoutPolicy(fallback_retry_delay_s=0.3, cooldown_source="test"))
    assert h.last_wait_decision is None
    out2 = _merge_fallback_wait_into_retry_decision(base, h)
    assert out2 == base

    # 3. fallback present with last_wait_decision → additive append
    h.record_wait_decision()
    out3 = _merge_fallback_wait_into_retry_decision(base, h)
    assert out3 is not base              # returns a fresh dict (non-mutation)
    assert len(out3["cooldown_decisions"]) == 2
    kinds = [d["kind"] for d in out3["cooldown_decisions"]]
    assert "transport_retry" in kinds
    assert "fallback_retry" in kinds
    # applied_s=0.0 → total_cooldown_ms unchanged
    assert out3["total_cooldown_ms"] == 50
    # Original dict was NOT mutated (shallow copy semantics, symmetric to scheduler merge)
    assert len(base["cooldown_decisions"]) == 1
    print("    OK: None-safe, last_wait_decision-None-safe, additive, non-mutating")


def test_bootstrap_container_settings_flow_matches_run_export_artifact_shape():
    """Cross-check: the live ``Container`` path constructs all four sites
    with ``source == "settings"`` (this part is locked by hardening test
    #47 already), AND the artifact-level test above proves the same
    label survives ``run_export``. This test makes the cross-link
    explicit: instead of asserting both halves separately, it builds
    the Container, reads its policy, and asserts that policy's
    ``cooldown_source`` equals what artifact tests expect.

    This is a tiny "the two halves agree" check, not a re-verification
    of the wiring. It exists so a future change that splits the source
    string into a different label (e.g. ``"settings_v2"``) breaks the
    end-to-end assumption immediately.
    """
    print("  [J/cross] Container().policy.cooldown_source == 'settings' (cross-check)")
    from src.app.bootstrap import Container
    from src.app.settings import AppSettings

    s = AppSettings()
    s.llm.base_url = "http://203.0.113.1:65000"   # TEST-NET-3 unreachable
    s.fallback.retry_delay_s = 0.13
    c = Container(settings=s, _skip_llm_probe=True)
    assert c.policy.cooldown_source == "settings", c.policy.cooldown_source
    # And the four downstream consumers all read 'settings':
    assert c.scheduler.cooldown_source == "settings"
    assert c.fallback.fallback_retry_delay_source == "settings"
    assert c.llm_client.transport_retry_cooldown_source == "settings"
    print("    OK: policy + scheduler + fallback + llm_client all source='settings'")


# ===========================================================================
TESTS = [
    test_cooldown_applied_path_appears_in_artifact_with_positive_applied_s,
    test_count_based_retry_exhaustion_reason_in_artifact,
    test_cooldown_decisions_dict_shape_after_json_roundtrip,
    test_health_failure_reason_classification_connection_refused,
    test_operator_supplied_values_survive_roundtrip_with_semantic_bounds,
    # T-tranche-8 additions
    test_health_sweep_covers_every_normalized_reason_constant,
    test_scheduler_heavy_wait_applied_path_reaches_artifact,
    test_scheduler_light_wait_applied_path_reaches_artifact,
    # T-tranche-9 additions
    test_fallback_wait_decision_serializes_with_canonical_shape,
    test_fallback_wait_decision_source_default_when_no_policy,
    test_settings_built_policy_source_settings_survives_to_artifact,
    test_bootstrap_container_settings_flow_matches_run_export_artifact_shape,
    # T-tranche-10 additions — production closure
    test_fallback_wait_reaches_artifact_cooldown_decisions_on_failure,
    test_dispatcher_fallback_merge_helper_is_additive_and_noop_when_fallback_absent,
    # T-tranche-11 additions — shed branch runtime coverage
    test_shed_branch_carries_fallback_wait_in_task_result_retry_decision,
    test_shed_branch_noop_when_dispatcher_has_no_fallback,
    # T-tranche-12 additions — queue post-hoc wrapper coverage
    test_post_hoc_fallback_merge_fires_when_handler_exception_escapes_to_queue,
    test_post_hoc_wrapper_is_noop_on_dispatcher_produced_failure_results,
    # NOTE: test_health_reason_sweep_survives_to_artifact is parametrized so
    # pytest expands it into 7 IDs at collection time; the bare function
    # isn't in this list because it would be called without its parameters
    # under the __main__ runner. Use ``pytest ...`` to exercise the sweep.
]


if __name__ == "__main__":
    print("=" * 60)
    print("artifact explainability invariants (T-tranche-7)")
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
