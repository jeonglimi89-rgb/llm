"""
test_export_runtime.py — review/export_runtime.py 단위 테스트

다루는 영역
-----------
1. resolve_base_url(...) precedence: CLI > env > settings > fail
2. mock_allowed(...) — explicit opt-in only
3. require_live_or_explicit_mock(...) — hard-fail when live down + mock not allowed
4. CountingAdapter — generate / exception counting + reset_case isolation
5. CallCounters arithmetic — attempt / transport_retry / parse_retry
6. _percentile / RunSummary aggregation
7. CaseTelemetry / RunTelemetry serialization round-trip
8. case_telemetry_from_result mapping
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.review.export_runtime import (
    BaseURLResolutionError, LiveLLMUnavailableError, CasesFileError,
    UnsafeMockOutputError,
    ResolvedEndpoint, ResolvedOutDir,
    resolve_base_url, mock_allowed, require_live_or_explicit_mock,
    CountingAdapter, CallCounters,
    CaseTelemetry, RunTelemetry, RunSummary,
    write_run_report, case_telemetry_from_result,
    _percentile, build_timeout_policy,
    parse_health_timeout, normalize_max_retries, load_cases_file,
    peek_cases_schema_version, resolve_mock_safe_out_dir,
    DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_MAX_RETRIES,
    SUPPORTED_CASES_SCHEMA_VERSIONS, MOCK_RUNS_SUBDIR,
)
from src.app.execution.timeouts import TimeoutPolicy
from src.app.settings import TimeoutSettings
from datetime import datetime, UTC


# ---------------------------------------------------------------------------
# resolve_base_url precedence
# ---------------------------------------------------------------------------

def test_resolve_cli_beats_env_and_settings():
    print("  [1] resolve_base_url: CLI beats env and settings")
    ep = resolve_base_url(
        cli_base_url="http://cli:1",
        env={"LLM_BASE_URL": "http://env:1"},
        settings_base_url="http://settings:1",
    )
    assert ep.base_url == "http://cli:1"
    assert ep.source == "cli"
    print("    OK")


def test_resolve_env_beats_settings():
    print("  [2] resolve_base_url: env beats settings (no CLI)")
    ep = resolve_base_url(
        cli_base_url=None,
        env={"LLM_BASE_URL": "http://env:1"},
        settings_base_url="http://settings:1",
    )
    assert ep.base_url == "http://env:1"
    assert ep.source == "env"
    print("    OK")


def test_resolve_settings_used_when_env_absent():
    print("  [3] resolve_base_url: settings used when CLI and env absent")
    ep = resolve_base_url(
        cli_base_url=None,
        env={},
        settings_base_url="http://settings:1",
    )
    assert ep.base_url == "http://settings:1"
    assert ep.source == "settings"
    print("    OK")


def test_resolve_fails_when_no_source():
    print("  [4] resolve_base_url: fails clearly when no usable source")
    try:
        resolve_base_url(cli_base_url=None, env={}, settings_base_url=None)
    except BaseURLResolutionError as e:
        msg = str(e)
        assert "no usable LLM base URL" in msg
        assert "LLM_BASE_URL" in msg
        print("    OK")
        return
    raise AssertionError("expected BaseURLResolutionError")


def test_resolve_rejects_garbage_cli():
    print("  [5] resolve_base_url: garbage CLI value falls through to next source")
    ep = resolve_base_url(
        cli_base_url="not-a-url",
        env={"LLM_BASE_URL": "http://env:1"},
        settings_base_url="http://settings:1",
    )
    assert ep.source == "env"
    print("    OK")


def test_resolve_empty_string_cli_falls_through():
    print("  [6] resolve_base_url: empty string CLI falls through")
    ep = resolve_base_url(
        cli_base_url="",
        env={"LLM_BASE_URL": "http://env:1"},
        settings_base_url="http://settings:1",
    )
    assert ep.source == "env"
    print("    OK")


def test_resolve_carries_api_key_and_model_from_env():
    print("  [7] resolve_base_url: api_key/model env > settings")
    ep = resolve_base_url(
        cli_base_url=None,
        env={
            "LLM_BASE_URL": "http://env:1",
            "LLM_API_KEY": "env-key",
            "LLM_MODEL": "env-model",
        },
        settings_base_url="http://settings:1",
        settings_api_key="settings-key",
        settings_model="settings-model",
    )
    assert ep.api_key == "env-key"
    assert ep.model == "env-model"
    print("    OK")


def test_resolve_carries_api_key_and_model_from_settings_when_env_absent():
    print("  [8] resolve_base_url: api_key/model fall back to settings")
    ep = resolve_base_url(
        cli_base_url="http://cli:1",
        env={},
        settings_api_key="s-key",
        settings_model="s-model",
    )
    assert ep.api_key == "s-key"
    assert ep.model == "s-model"
    print("    OK")


# ---------------------------------------------------------------------------
# mock_allowed
# ---------------------------------------------------------------------------

def test_mock_allowed_default_false():
    print("  [9] mock_allowed: default False")
    assert mock_allowed(env={}) is False
    print("    OK")


def test_mock_allowed_cli_flag_true():
    print("  [10] mock_allowed: cli_flag=True overrides")
    assert mock_allowed(cli_flag=True, env={}) is True
    print("    OK")


def test_mock_allowed_env_opt_in():
    print("  [11] mock_allowed: EXPORT_ALLOW_MOCK opt-in (1/true/yes/on)")
    for v in ("1", "true", "TRUE", "yes", "ON"):
        assert mock_allowed(env={"EXPORT_ALLOW_MOCK": v}) is True, v
    for v in ("0", "false", "no", "off", ""):
        assert mock_allowed(env={"EXPORT_ALLOW_MOCK": v}) is False, v
    print("    OK")


# ---------------------------------------------------------------------------
# require_live_or_explicit_mock
# ---------------------------------------------------------------------------

def test_require_live_passes_when_live_up():
    print("  [12] require_live_or_explicit_mock: passes when live is up")
    require_live_or_explicit_mock(True, allow_mock=False, base_url="http://x:1")
    print("    OK")


def test_require_live_passes_when_mock_explicitly_allowed():
    print("  [13] require_live_or_explicit_mock: passes when mock explicitly allowed")
    require_live_or_explicit_mock(False, allow_mock=True, base_url="http://x:1")
    print("    OK")


def test_require_live_raises_when_live_down_and_mock_not_allowed():
    print("  [14] require_live_or_explicit_mock: raises LiveLLMUnavailableError on default")
    try:
        require_live_or_explicit_mock(False, allow_mock=False, base_url="http://x:1")
    except LiveLLMUnavailableError as e:
        msg = str(e)
        assert "http://x:1" in msg
        assert "EXPORT_ALLOW_MOCK" in msg
        assert "--allow-mock" in msg
        print("    OK")
        return
    raise AssertionError("expected LiveLLMUnavailableError")


# ---------------------------------------------------------------------------
# CountingAdapter
# ---------------------------------------------------------------------------

class _StubAdapter:
    provider_name = "stub"
    def __init__(self, raises=False):
        self.raises = raises
    def is_available(self):
        return True
    def generate(self, *a, **kw):
        if self.raises:
            raise ConnectionError("boom")
        return {"text": '{"k": "v"}'}


def test_counting_adapter_counts_calls_and_exceptions():
    print("  [15] CountingAdapter: counts generate calls and exceptions")
    inner = _StubAdapter()
    w = CountingAdapter(inner=inner)
    w.generate(messages=[])
    w.generate(messages=[])
    snap = w.snapshot()
    assert snap.generate_calls == 2
    assert snap.generate_exceptions == 0

    inner2 = _StubAdapter(raises=True)
    w2 = CountingAdapter(inner=inner2)
    try:
        w2.generate(messages=[])
    except ConnectionError:
        pass
    snap2 = w2.snapshot()
    assert snap2.generate_calls == 1
    assert snap2.generate_exceptions == 1
    print("    OK")


def test_counting_adapter_reset_case_isolates_per_case():
    print("  [16] CountingAdapter.reset_case isolates case telemetry")
    inner = _StubAdapter()
    w = CountingAdapter(inner=inner)
    w.generate(messages=[])
    w.generate(messages=[])
    w.reset_case()
    w.generate(messages=[])
    snap = w.snapshot()
    assert snap.generate_calls == 1
    print("    OK")


def test_counting_adapter_proxies_is_available():
    print("  [17] CountingAdapter proxies is_available")
    w = CountingAdapter(inner=_StubAdapter())
    assert w.is_available() is True
    assert w.provider_name.startswith("counting:")
    print("    OK")


# ---------------------------------------------------------------------------
# CallCounters arithmetic
# ---------------------------------------------------------------------------

def test_callcounters_first_attempt_success():
    print("  [18] CallCounters: first-attempt success")
    c = CallCounters(generate_calls=1, generate_exceptions=0)
    assert c.attempt_count == 1
    assert c.transport_retry_count == 0
    assert c.parse_retry_count == 0
    print("    OK")


def test_callcounters_parse_retry_only():
    print("  [19] CallCounters: 1 parse retry then success")
    c = CallCounters(generate_calls=2, generate_exceptions=0)
    assert c.attempt_count == 2
    assert c.transport_retry_count == 0
    assert c.parse_retry_count == 1
    print("    OK")


def test_callcounters_transport_retry_only():
    print("  [20] CallCounters: 1 transport retry then success")
    c = CallCounters(generate_calls=2, generate_exceptions=1)
    assert c.attempt_count == 2
    assert c.transport_retry_count == 1
    assert c.parse_retry_count == 0
    print("    OK")


def test_callcounters_no_negative_parse_retry():
    print("  [21] CallCounters: negative parse_retry_count clamped to 0")
    c = CallCounters(generate_calls=1, generate_exceptions=1)
    assert c.parse_retry_count == 0
    print("    OK")


# ---------------------------------------------------------------------------
# _percentile + RunTelemetry aggregation
# ---------------------------------------------------------------------------

def test_percentile_basic():
    print("  [22] _percentile basic")
    assert _percentile([], 50) == 0
    assert _percentile([10], 50) == 10
    assert _percentile([1, 2, 3, 4, 5], 50) == 3
    assert _percentile([1, 2, 3, 4, 5], 95) == 5
    assert _percentile([1, 2, 3, 4, 5], 100) == 5
    assert _percentile([1, 2, 3, 4, 5], 0) == 1
    print("    OK")


def _make_case(case_id, latency_ms, *, judgment="pass", status="success", retries=0, parse=0):
    return CaseTelemetry(
        case_id=case_id, domain="builder", task="x",
        latency_ms=latency_ms,
        attempt_count=1 + retries + parse,
        transport_retry_count=retries,
        parse_retry_count=parse,
        final_status=status,
        auto_validated=(judgment == "pass"),
        final_judgment=judgment,
        severity=("info" if judgment == "pass" else "high"),
        failure_categories=[] if judgment == "pass" else ["x"],
    )


def test_run_telemetry_finalize_basic():
    print("  [23] RunTelemetry.finalize aggregates correctly")
    rt = RunTelemetry(started_at="2026-04-06T00:00:00+00:00")
    rt.add_case(_make_case("HR-001", 5000, judgment="pass"))
    rt.add_case(_make_case("HR-002", 15000, judgment="fail", retries=1))
    rt.add_case(_make_case("HR-003", 35000, judgment="needs_review", parse=2))
    rt.add_case(_make_case("HR-004", 81000, judgment="fail", parse=1))
    rt.finalize()

    s = rt.summary
    assert s.total_cases == 4
    assert s.pass_count == 1
    assert s.fail_count == 2
    assert s.needs_review_count == 1
    assert s.max_latency_ms == 81000
    assert s.over_10s_count == 3   # 15000, 35000, 81000
    assert s.over_30s_count == 2   # 35000, 81000
    assert s.over_60s_count == 1   # 81000
    assert s.total_transport_retries == 1
    assert s.total_parse_retries == 3
    print("    OK")


def test_run_telemetry_serialization_round_trip(tmp_path=None):
    print("  [24] RunTelemetry to_dict / write_run_report round trip")
    rt = RunTelemetry(
        started_at="2026-04-06T00:00:00+00:00",
        used_mock=False,
        resolved_base_url="http://x:1",
        resolved_base_url_source="cli",
        resolved_model="qwen2.5-0.5b-instruct",
    )
    rt.add_case(_make_case("HR-001", 1234, judgment="pass"))
    rt.finalize()

    out = Path(__file__).resolve().parent.parent.parent / "runtime" / "_test_run_report.json"
    try:
        write_run_report(rt, out)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["resolved_base_url"] == "http://x:1"
        assert loaded["resolved_base_url_source"] == "cli"
        assert loaded["used_mock"] is False
        assert loaded["summary"]["total_cases"] == 1
        assert loaded["summary"]["pass_count"] == 1
        assert len(loaded["cases"]) == 1
        assert loaded["cases"][0]["case_id"] == "HR-001"
    finally:
        if out.exists():
            out.unlink()
    print("    OK")


# ---------------------------------------------------------------------------
# case_telemetry_from_result
# ---------------------------------------------------------------------------

def test_case_telemetry_from_result_pass_case():
    print("  [25] case_telemetry_from_result: success case")
    result_dict = {
        "status": "done",
        "latency_ms": 4500,
        "layered_judgment": {
            "auto_validated": True,
            "final_judgment": "pass",
            "severity": "info",
            "failure_categories": [],
        },
    }
    ct = case_telemetry_from_result(
        case_id="HR-003", domain="builder", task="patch_intent_parse",
        start_ts=1700000000.0, end_ts=1700000004.5,
        counters=CallCounters(generate_calls=1, generate_exceptions=0),
        task_result_dict=result_dict,
        used_mock=False,
        resolved_base_url="http://x:1",
        resolved_model="qwen2.5-0.5b-instruct",
    )
    assert ct.final_status == "success"
    assert ct.auto_validated is True
    assert ct.final_judgment == "pass"
    assert ct.severity == "info"
    assert ct.attempt_count == 1
    assert ct.latency_ms == 4500
    assert ct.tool_name == "builder.patch_intent_parse"
    print("    OK")


def test_case_telemetry_from_result_fail_case():
    print("  [26] case_telemetry_from_result: fail case carries failure_categories")
    result_dict = {
        "status": "done",
        "latency_ms": 8100,
        "layered_judgment": {
            "auto_validated": False,
            "final_judgment": "fail",
            "severity": "critical",
            "failure_categories": ["wrong_key_locale", "task_contract_violation"],
        },
    }
    ct = case_telemetry_from_result(
        case_id="HR-001", domain="builder", task="requirement_parse",
        start_ts=0, end_ts=8.1,
        counters=CallCounters(generate_calls=1, generate_exceptions=0),
        task_result_dict=result_dict,
        used_mock=False,
        resolved_base_url="http://x:1",
        resolved_model="qwen2.5-0.5b-instruct",
    )
    assert ct.final_status == "success"   # dispatcher status=done; gate-level fail is final_judgment
    assert ct.auto_validated is False
    assert ct.final_judgment == "fail"
    assert "wrong_key_locale" in ct.failure_categories
    print("    OK")


def test_case_telemetry_from_result_dispatcher_error():
    print("  [27] case_telemetry_from_result: dispatcher error → final_status=failed")
    result_dict = {
        "status": "error",
        "latency_ms": 2000,
        "layered_judgment": {
            "auto_validated": False,
            "final_judgment": "fail",
            "severity": "critical",
            "failure_categories": ["schema_failure"],
        },
    }
    ct = case_telemetry_from_result(
        case_id="HR-X", domain="cad", task="constraint_parse",
        start_ts=0, end_ts=2.0,
        counters=CallCounters(generate_calls=1, generate_exceptions=1),
        task_result_dict=result_dict,
        used_mock=False,
        resolved_base_url="http://x:1",
        resolved_model="qwen2.5-0.5b-instruct",
    )
    assert ct.final_status == "failed"
    assert ct.transport_retry_count == 1
    print("    OK")


# ---------------------------------------------------------------------------
# build_timeout_policy
# ---------------------------------------------------------------------------

def test_build_timeout_policy_default_returns_stock_policy():
    print("  [28] build_timeout_policy(None) returns default TimeoutPolicy (strict=120)")
    p = build_timeout_policy(None)
    assert isinstance(p, TimeoutPolicy)
    assert p.get_timeout("strict_json") == TimeoutSettings().strict_json_s
    assert p.get_timeout("fast_chat") == TimeoutSettings().fast_chat_s
    assert p.get_timeout("long_context") == TimeoutSettings().long_context_s
    assert p.get_timeout("embedding") == TimeoutSettings().embedding_s
    print("    OK")


def test_build_timeout_policy_override_every_pool():
    print("  [29] build_timeout_policy(7) overrides every pool")
    p = build_timeout_policy(7)
    for pool in ("strict_json", "fast_chat", "long_context", "embedding"):
        assert p.get_timeout(pool) == 7, pool
    assert p.hard_timeout == 7
    print("    OK")


def test_build_timeout_policy_unknown_pool_falls_back_to_strict_json():
    print("  [30] build_timeout_policy(7) unknown pool falls through to strict_json (=7)")
    p = build_timeout_policy(7)
    assert p.get_timeout("some_unknown_pool") == 7
    print("    OK")


def test_build_timeout_policy_very_short_allowed():
    print("  [31] build_timeout_policy(1) allows 1s (used for timeout-enforcement tests)")
    p = build_timeout_policy(1)
    assert p.get_timeout("strict_json") == 1
    print("    OK")


def test_build_timeout_policy_zero_clamped_to_floor():
    """T-tranche 2026-04-07: clamp to float floor (0.001s = 1ms), not int 1.

    The old behavior absorbed sub-second precision by truncating to int and
    raising the floor to 1s. The new contract preserves sub-second precision
    end-to-end and only protects against literal zero/negative values.
    """
    print("  [32] build_timeout_policy(0) clamps to 0.001 (float floor)")
    p = build_timeout_policy(0)
    t = p.get_timeout("strict_json")
    assert t == 0.001, f"got {t}"
    assert isinstance(t, float)
    assert p.hard_timeout == 0.001
    print("    OK")


# ---------------------------------------------------------------------------
# parse_health_timeout
# ---------------------------------------------------------------------------

def test_parse_health_timeout_unset_returns_default():
    print("  [33] parse_health_timeout: unset → default 5.0 / source=default")
    v, src = parse_health_timeout(env={})
    assert v == DEFAULT_HEALTH_TIMEOUT_S == 5.0
    assert src == "default"
    print("    OK")


def test_parse_health_timeout_int_string():
    print("  [34] parse_health_timeout: '7' → 7.0 / source=env")
    v, src = parse_health_timeout(env={"LLM_HEALTH_TIMEOUT": "7"})
    assert v == 7.0
    assert src == "env"
    print("    OK")


def test_parse_health_timeout_float_string():
    print("  [35] parse_health_timeout: '2.5' → 2.5 / source=env")
    v, src = parse_health_timeout(env={"LLM_HEALTH_TIMEOUT": "2.5"})
    assert v == 2.5
    assert src == "env"
    print("    OK")


def test_parse_health_timeout_invalid_falls_back_to_default():
    print("  [36] parse_health_timeout: 'abc' → 5.0 / source=default_invalid")
    v, src = parse_health_timeout(env={"LLM_HEALTH_TIMEOUT": "abc"})
    assert v == 5.0
    assert src == "default_invalid"
    print("    OK")


def test_parse_health_timeout_zero_or_negative_clamped():
    print("  [37] parse_health_timeout: 0 / -1 / -3.7 → 0.1 / source=env_clamped")
    for raw in ("0", "-1", "-3.7"):
        v, src = parse_health_timeout(env={"LLM_HEALTH_TIMEOUT": raw})
        assert v == 0.1, f"raw={raw} got v={v}"
        assert src == "env_clamped", f"raw={raw} got src={src}"
    print("    OK")


def test_parse_health_timeout_empty_string_treated_as_unset():
    print("  [38] parse_health_timeout: empty string → default")
    v, src = parse_health_timeout(env={"LLM_HEALTH_TIMEOUT": ""})
    assert v == 5.0
    assert src == "default"
    print("    OK")


# ---------------------------------------------------------------------------
# normalize_max_retries
# ---------------------------------------------------------------------------

def test_normalize_max_retries_default():
    print("  [39] normalize_max_retries(None) → DEFAULT_MAX_RETRIES (1)")
    assert normalize_max_retries(None) == DEFAULT_MAX_RETRIES == 1
    print("    OK")


def test_normalize_max_retries_explicit_zero():
    print("  [40] normalize_max_retries(0) → 0 (single attempt, no retry)")
    assert normalize_max_retries(0) == 0
    print("    OK")


def test_normalize_max_retries_explicit_two():
    print("  [41] normalize_max_retries(2) → 2")
    assert normalize_max_retries(2) == 2
    print("    OK")


def test_normalize_max_retries_negative_clamped_to_zero():
    print("  [42] normalize_max_retries(-3) → 0 (clamped, not error)")
    assert normalize_max_retries(-3) == 0
    print("    OK")


def test_normalize_max_retries_garbage_falls_back_to_default():
    print("  [43] normalize_max_retries('abc') → 1 (default)")
    assert normalize_max_retries("abc") == 1
    print("    OK")


# ---------------------------------------------------------------------------
# load_cases_file
# ---------------------------------------------------------------------------

import tempfile
import os as _os


def _write_temp_json(content) -> Path:
    fd, name = tempfile.mkstemp(suffix=".json")
    _os.close(fd)
    p = Path(name)
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    else:
        p.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_cases_dict_wrapper_form():
    print("  [44] load_cases_file: {'schema_version':'1.0', 'cases':[{...}, ...]} dict form")
    # 2026-04-07: wrapper form now requires schema_version (Task B).
    p = _write_temp_json({
        "schema_version": "1.0",
        "cases": [
            {"domain": "builder", "task": "requirement_parse", "input": "test1"},
            {"domain": "cad",     "task": "constraint_parse", "input": "test2"},
        ],
    })
    try:
        cs = load_cases_file(p)
        assert cs == [
            ("builder", "requirement_parse", "test1"),
            ("cad",     "constraint_parse", "test2"),
        ]
    finally:
        p.unlink()
    print("    OK")


def test_load_cases_list_of_dicts_form():
    print("  [45] load_cases_file: bare list of dicts")
    p = _write_temp_json([
        {"domain": "minecraft", "task": "edit_parse", "input": "x"},
    ])
    try:
        cs = load_cases_file(p)
        assert cs == [("minecraft", "edit_parse", "x")]
    finally:
        p.unlink()
    print("    OK")


def test_load_cases_list_of_tuples_form():
    print("  [46] load_cases_file: list of 3-element JSON arrays")
    p = _write_temp_json([
        ["builder", "requirement_parse", "test1"],
        ["cad",     "constraint_parse",  "test2"],
    ])
    try:
        cs = load_cases_file(p)
        assert cs == [
            ("builder", "requirement_parse", "test1"),
            ("cad",     "constraint_parse",  "test2"),
        ]
    finally:
        p.unlink()
    print("    OK")


def test_load_cases_default_repo_file_loads_12():
    print("  [47] load_cases_file: default datasets/human_review_cases.json loads 12")
    default_path = (
        Path(__file__).resolve().parent.parent.parent
        / "datasets" / "human_review_cases.json"
    )
    assert default_path.exists(), default_path
    cs = load_cases_file(default_path)
    assert len(cs) == 12
    # First case must match the legacy hard-coded ordering exactly
    assert cs[0] == ("builder", "requirement_parse", "2층 주택 거실 크게, 모던 스타일")
    assert cs[-1] == ("animation", "lighting_intent_parse", "비 오는 밤 외로운 분위기")
    print("    OK")


def test_load_cases_missing_file_raises():
    print("  [48] load_cases_file: missing file → CasesFileError")
    bad = Path("/__definitely_does_not_exist__/cases.json")
    try:
        load_cases_file(bad)
    except CasesFileError as e:
        assert "not found" in str(e)
        print("    OK")
        return
    raise AssertionError("expected CasesFileError")


def test_load_cases_invalid_json_raises():
    print("  [49] load_cases_file: invalid JSON → CasesFileError")
    p = _write_temp_json("not really json {{{")
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "valid JSON" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_empty_list_raises():
    print("  [50] load_cases_file: empty list → CasesFileError")
    p = _write_temp_json([])
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "empty" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_dict_without_cases_key_raises():
    print("  [51] load_cases_file: dict missing 'cases' key → CasesFileError")
    p = _write_temp_json({"items": []})
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "'cases' key" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_top_level_scalar_raises():
    print("  [52] load_cases_file: top-level scalar → CasesFileError")
    p = _write_temp_json(42)
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "top-level" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_entry_missing_fields_raises():
    print("  [53] load_cases_file: entry missing required field → CasesFileError")
    p = _write_temp_json([{"domain": "builder", "task": "x"}])  # missing input
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "missing/invalid" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_entry_wrong_type_raises():
    print("  [54] load_cases_file: entry of wrong type (string) → CasesFileError")
    p = _write_temp_json(["not a dict"])
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "must be a dict" in str(e) or "3-element list" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_preserves_order():
    print("  [55] load_cases_file: order preserved exactly")
    items = [
        {"domain": "z", "task": "a", "input": "1"},
        {"domain": "a", "task": "z", "input": "2"},
        {"domain": "m", "task": "m", "input": "3"},
    ]
    p = _write_temp_json(items)
    try:
        cs = load_cases_file(p)
        assert [c[0] for c in cs] == ["z", "a", "m"]
    finally:
        p.unlink()
    print("    OK")


# ---------------------------------------------------------------------------
# schema_version enforcement (Task B)
# ---------------------------------------------------------------------------

def test_load_cases_wrapper_with_valid_schema_version_passes():
    print("  [56] load_cases_file: wrapper + schema_version='1.0' passes")
    p = _write_temp_json({
        "schema_version": "1.0",
        "cases": [{"domain": "builder", "task": "requirement_parse", "input": "x"}],
    })
    try:
        cs = load_cases_file(p)
        assert cs == [("builder", "requirement_parse", "x")]
    finally:
        p.unlink()
    print("    OK")


def test_load_cases_wrapper_missing_schema_version_raises():
    print("  [57] load_cases_file: wrapper missing schema_version → CasesFileError")
    p = _write_temp_json({"cases": [{"domain": "d", "task": "t", "input": "x"}]})
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "schema_version" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_wrapper_unsupported_schema_version_raises():
    print("  [58] load_cases_file: wrapper with schema_version='2.0' → CasesFileError")
    p = _write_temp_json({
        "schema_version": "2.0",
        "cases": [{"domain": "d", "task": "t", "input": "x"}],
    })
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "2.0" in str(e) and "supported versions" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_wrapper_non_string_schema_version_raises():
    print("  [59] load_cases_file: wrapper with schema_version=1.0 (number) → CasesFileError")
    # JSON number instead of string: explicitly rejected.
    p = _write_temp_json({
        "schema_version": 1.0,
        "cases": [{"domain": "d", "task": "t", "input": "x"}],
    })
    raised = False
    try:
        load_cases_file(p)
    except CasesFileError as e:
        assert "schema_version" in str(e)
        raised = True
    finally:
        p.unlink()
    assert raised, "expected CasesFileError"
    print("    OK")


def test_load_cases_legacy_bare_list_still_allowed():
    print("  [60] load_cases_file: bare list (no schema_version) still allowed (legacy)")
    p = _write_temp_json([{"domain": "d", "task": "t", "input": "x"}])
    try:
        cs = load_cases_file(p)
        assert cs == [("d", "t", "x")]
    finally:
        p.unlink()
    print("    OK")


def test_load_cases_default_dataset_has_supported_schema_version():
    print("  [61] default datasets/human_review_cases.json has schema_version '1.0'")
    default_path = (
        Path(__file__).resolve().parent.parent.parent
        / "datasets" / "human_review_cases.json"
    )
    cs = load_cases_file(default_path)
    assert len(cs) == 12
    sv = peek_cases_schema_version(default_path)
    assert sv == "1.0", f"got {sv!r}"
    assert sv in SUPPORTED_CASES_SCHEMA_VERSIONS
    print("    OK")


# ---------------------------------------------------------------------------
# peek_cases_schema_version
# ---------------------------------------------------------------------------

def test_peek_schema_version_wrapper_form():
    print("  [62] peek_cases_schema_version: wrapper form returns string")
    p = _write_temp_json({"schema_version": "1.0", "cases": []})
    try:
        assert peek_cases_schema_version(p) == "1.0"
    finally:
        p.unlink()
    print("    OK")


def test_peek_schema_version_legacy_list_returns_none():
    print("  [63] peek_cases_schema_version: legacy bare list → None")
    p = _write_temp_json([{"domain": "d", "task": "t", "input": "x"}])
    try:
        assert peek_cases_schema_version(p) is None
    finally:
        p.unlink()
    print("    OK")


def test_peek_schema_version_missing_file_returns_none():
    print("  [64] peek_cases_schema_version: missing file → None (no raise)")
    assert peek_cases_schema_version(Path("/__nope__.json")) is None
    print("    OK")


def test_peek_schema_version_garbage_returns_none():
    print("  [65] peek_cases_schema_version: invalid JSON → None (no raise)")
    p = _write_temp_json("not json {{{")
    try:
        assert peek_cases_schema_version(p) is None
    finally:
        p.unlink()
    print("    OK")


# ---------------------------------------------------------------------------
# Mock output isolation (Task A)
# ---------------------------------------------------------------------------

def _fixed_now():
    return datetime(2026, 4, 7, 12, 34, 56, tzinfo=UTC)


def test_resolve_out_dir_live_default():
    print("  [66] resolve_mock_safe_out_dir: live run + no override → default_live")
    live = Path("/fake/runtime/human_review")
    r = resolve_mock_safe_out_dir(used_mock=False, live_default_dir=live)
    assert r.out_dir == live
    assert r.source == "default_live"
    print("    OK")


def test_resolve_out_dir_live_with_user_override_accepted():
    print("  [67] resolve_mock_safe_out_dir: live run + any user path → user_override_mock_safe")
    live = Path("/fake/runtime/human_review")
    r = resolve_mock_safe_out_dir(used_mock=False, live_default_dir=live, user_out_dir=Path("/tmp/anywhere"))
    assert r.out_dir == Path("/tmp/anywhere")
    assert r.source == "user_override_mock_safe"
    print("    OK")


def test_resolve_out_dir_mock_default_isolated_timestamped(tmp_path=None):
    print("  [68] resolve_mock_safe_out_dir: mock run + no override → default_mock_isolated with timestamp")
    live = Path("/fake/runtime/human_review")
    r = resolve_mock_safe_out_dir(
        used_mock=True, live_default_dir=live, now=_fixed_now(),
    )
    assert r.source == "default_mock_isolated"
    assert r.out_dir == live / MOCK_RUNS_SUBDIR / "20260407_123456"
    print("    OK")


def test_resolve_out_dir_mock_refuses_live_root():
    print("  [69] resolve_mock_safe_out_dir: mock run + user override=live → UnsafeMockOutputError")
    live = Path(__file__).resolve().parent  # any real dir works for resolve()
    raised = False
    try:
        resolve_mock_safe_out_dir(
            used_mock=True, live_default_dir=live, user_out_dir=live,
        )
    except UnsafeMockOutputError as e:
        assert "live default directory" in str(e) or "not inside" in str(e)
        raised = True
    assert raised, "expected UnsafeMockOutputError"
    print("    OK")


def test_resolve_out_dir_mock_refuses_unrelated_path():
    print("  [70] resolve_mock_safe_out_dir: mock + unrelated user path → UnsafeMockOutputError")
    live = Path(__file__).resolve().parent  # existing dir
    raised = False
    try:
        resolve_mock_safe_out_dir(
            used_mock=True, live_default_dir=live, user_out_dir=Path("/tmp/somewhere_else"),
        )
    except UnsafeMockOutputError as e:
        assert "not inside" in str(e)
        raised = True
    assert raised, "expected UnsafeMockOutputError"
    print("    OK")


def test_resolve_out_dir_mock_accepts_path_inside_mock_runs():
    print("  [71] resolve_mock_safe_out_dir: mock + user path under mock_runs/ → user_override_mock_safe")
    live_real = Path(__file__).resolve().parent  # real dir for resolve()
    mock_root = live_real / MOCK_RUNS_SUBDIR
    mock_root.mkdir(parents=True, exist_ok=True)
    safe = mock_root / "custom"
    try:
        r = resolve_mock_safe_out_dir(
            used_mock=True, live_default_dir=live_real, user_out_dir=safe,
        )
        assert r.source == "user_override_mock_safe"
        assert r.out_dir == safe
    finally:
        import shutil as _sh
        _sh.rmtree(mock_root, ignore_errors=True)
    print("    OK")


def test_resolve_out_dir_mock_refuses_live_root_alias_via_mock_runs_sibling():
    """A mock_runs sibling (e.g. mock_runs_wrong) must not sneak through."""
    print("  [72] resolve_mock_safe_out_dir: mock_runs_wrong is NOT under mock_runs → refused")
    live_real = Path(__file__).resolve().parent
    sneaky = live_real / "mock_runs_wrong"
    sneaky.mkdir(parents=True, exist_ok=True)
    try:
        raised = False
        try:
            resolve_mock_safe_out_dir(
                used_mock=True, live_default_dir=live_real, user_out_dir=sneaky,
            )
        except UnsafeMockOutputError:
            raised = True
        assert raised, "should have refused mock_runs_wrong"
    finally:
        import shutil as _sh
        _sh.rmtree(sneaky, ignore_errors=True)
    print("    OK")


def test_resolve_out_dir_mock_is_different_every_second():
    print("  [73] resolve_mock_safe_out_dir: two different timestamps produce different dirs")
    live = Path("/fake/runtime/human_review")
    t1 = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 7, 12, 0, 1, tzinfo=UTC)
    r1 = resolve_mock_safe_out_dir(used_mock=True, live_default_dir=live, now=t1)
    r2 = resolve_mock_safe_out_dir(used_mock=True, live_default_dir=live, now=t2)
    assert r1.out_dir != r2.out_dir
    print("    OK")


# ---------------------------------------------------------------------------
# T-tranche: UnifiedTimeoutPolicy + float timeout end-to-end + RetryDecision
# ---------------------------------------------------------------------------

from src.app.execution.timeouts import (
    UnifiedTimeoutPolicy,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_HEALTH_TIMEOUT_S as _UNIFIED_DEFAULT_HEALTH,
)
from src.app.llm.client import (
    RetryDecision,
    RETRY_REASON_INITIAL_SUCCESS,
    RETRY_REASON_RETRY_SUCCESS,
    RETRY_REASON_PARSE_RETRY_EXHAUSTED,
    RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED,
    RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_INITIAL,
    RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_RETRY,
)
from src.app.llm.adapters.vllm_http import (
    VLLMHttpAdapter, HealthProbeResult,
    HEALTH_REASON_OK, HEALTH_REASON_TIMEOUT,
    HEALTH_REASON_CONNECTION_REFUSED, HEALTH_REASON_HTTP_ERROR,
    HEALTH_REASON_MALFORMED_RESPONSE, HEALTH_REASON_UNEXPECTED_ERROR,
    _classify_urllib_exception,
)
import urllib.error


def test_unified_default_health_matches_export_runtime():
    """Single source of truth — execution.timeouts is canonical, export_runtime
    re-exports the same constant."""
    print("  [74] DEFAULT_HEALTH_TIMEOUT_S has a single source of truth")
    assert DEFAULT_HEALTH_TIMEOUT_S == _UNIFIED_DEFAULT_HEALTH == 5.0
    print("    OK")


def test_unified_policy_default_deadline_derives_from_count_and_timeout():
    print("  [75] UnifiedTimeoutPolicy default deadline = (1+max_retries)*request_timeout")
    p = UnifiedTimeoutPolicy(request_timeout_s=2.0, max_retries=2)
    assert p.effective_total_deadline_s() == 6.0
    p2 = UnifiedTimeoutPolicy(request_timeout_s=10.0, max_retries=0)
    assert p2.effective_total_deadline_s() == 10.0
    print("    OK")


def test_unified_policy_explicit_deadline_at_least_request_timeout():
    """Even when caller passes a tiny explicit deadline, the deadline is at
    least one full request_timeout so the first attempt always gets a chance."""
    print("  [76] UnifiedTimeoutPolicy: explicit deadline is clamped to >= request_timeout")
    p = UnifiedTimeoutPolicy(request_timeout_s=5.0, max_retries=3, total_deadline_s=1.0)
    assert p.effective_total_deadline_s() == 5.0
    print("    OK")


def test_unified_policy_to_legacy_timeout_policy_preserves_float():
    print("  [77] UnifiedTimeoutPolicy.to_legacy_timeout_policy preserves float seconds")
    p = UnifiedTimeoutPolicy(request_timeout_s=2.5)
    legacy = p.to_legacy_timeout_policy()
    assert legacy.get_timeout("strict_json") == 2.5
    assert legacy.get_timeout("fast_chat") == 2.5
    assert legacy.get_timeout("long_context") == 2.5
    assert legacy.get_timeout("embedding") == 2.5
    assert isinstance(legacy.get_timeout("strict_json"), float)
    print("    OK")


def test_unified_policy_clamps_zero_request_timeout():
    print("  [78] UnifiedTimeoutPolicy clamps non-positive request_timeout to default")
    p = UnifiedTimeoutPolicy(request_timeout_s=0)
    assert p.request_timeout_s == DEFAULT_REQUEST_TIMEOUT_S
    p2 = UnifiedTimeoutPolicy(request_timeout_s=-3.0)
    assert p2.request_timeout_s == DEFAULT_REQUEST_TIMEOUT_S
    print("    OK")


def test_unified_policy_clamps_negative_max_retries_to_zero():
    print("  [79] UnifiedTimeoutPolicy clamps negative max_retries to 0")
    p = UnifiedTimeoutPolicy(max_retries=-5)
    assert p.max_retries == 0
    print("    OK")


def test_unified_policy_to_dict_has_effective_deadline():
    print("  [80] UnifiedTimeoutPolicy.to_dict carries effective_total_deadline_s")
    p = UnifiedTimeoutPolicy(request_timeout_s=3.0, max_retries=2)
    d = p.to_dict()
    assert d["effective_total_deadline_s"] == 9.0
    assert d["request_timeout_s"] == 3.0
    assert d["max_retries"] == 2
    print("    OK")


def test_build_timeout_policy_preserves_subsecond_float():
    """T-tranche fix: 0.5s no longer gets truncated to 0 (then floored to 1)."""
    print("  [81] build_timeout_policy(0.5) preserves 0.5 (was truncated to 1 before)")
    p = build_timeout_policy(0.5)
    assert p.get_timeout("strict_json") == 0.5
    assert p.get_timeout("fast_chat") == 0.5
    print("    OK")


def test_build_timeout_policy_accepts_int_too():
    print("  [82] build_timeout_policy(7) still accepts int (=7.0)")
    p = build_timeout_policy(7)
    assert p.get_timeout("strict_json") == 7.0
    print("    OK")


# ---------------------------------------------------------------------------
# T-tranche: VLLMHttpAdapter no longer carries fixed 5s magic number
# ---------------------------------------------------------------------------

def test_vllm_http_adapter_default_health_timeout_imported_from_execution():
    """The adapter must NOT define its own 5.0 constant. The default must come
    from execution.timeouts (the single source of truth)."""
    print("  [83] VLLMHttpAdapter default health timeout comes from execution.timeouts")
    import src.app.llm.adapters.vllm_http as vllm_http_mod
    # The constant the adapter exposes (still bound) must equal the unified one.
    assert vllm_http_mod.DEFAULT_HEALTH_TIMEOUT_S == _UNIFIED_DEFAULT_HEALTH
    # And the bound name in vllm_http_mod must be the same object as in execution.timeouts.
    from src.app.execution import timeouts as _exec_timeouts
    assert vllm_http_mod.DEFAULT_HEALTH_TIMEOUT_S == _exec_timeouts.DEFAULT_HEALTH_TIMEOUT_S
    print("    OK")


def test_vllm_http_adapter_constructor_default_health_timeout():
    print("  [84] VLLMHttpAdapter() default health_timeout_s = 5.0")
    a = VLLMHttpAdapter("http://x:1", "k", "m")
    assert a.health_timeout_s == 5.0
    a2 = VLLMHttpAdapter("http://x:1", "k", "m", health_timeout_s=2.5)
    assert a2.health_timeout_s == 2.5
    print("    OK")


# ---------------------------------------------------------------------------
# T-tranche: HealthProbeResult classification helper
# ---------------------------------------------------------------------------

def test_classify_urllib_timeout():
    print("  [85] _classify_urllib_exception: TimeoutError → 'timeout'")
    reason, _ = _classify_urllib_exception(TimeoutError("timed out"))
    assert reason == HEALTH_REASON_TIMEOUT
    print("    OK")


def test_classify_urllib_url_error_refused():
    print("  [86] _classify_urllib_exception: URLError(ConnectionRefused) → 'connection_refused'")
    err = urllib.error.URLError(ConnectionRefusedError("Connection refused"))
    reason, _ = _classify_urllib_exception(err)
    assert reason == HEALTH_REASON_CONNECTION_REFUSED
    print("    OK")


def test_classify_urllib_http_error():
    print("  [87] _classify_urllib_exception: HTTPError → 'http_error'")
    err = urllib.error.HTTPError(
        url="http://x", code=503, msg="Service Unavailable",
        hdrs=None, fp=None,
    )
    reason, detail = _classify_urllib_exception(err)
    assert reason == HEALTH_REASON_HTTP_ERROR
    assert "503" in detail
    print("    OK")


def test_classify_unexpected_falls_back():
    print("  [88] _classify_urllib_exception: random RuntimeError → 'unexpected_error'")
    reason, _ = _classify_urllib_exception(RuntimeError("???"))
    assert reason == HEALTH_REASON_UNEXPECTED_ERROR
    print("    OK")


def test_health_probe_result_to_dict_has_keys():
    print("  [89] HealthProbeResult.to_dict has all the expected keys")
    r = HealthProbeResult(available=False, reason="timeout",
                          elapsed_ms=42, timeout_used_s=2.5, detail="x")
    d = r.to_dict()
    for k in ("available", "reason", "elapsed_ms", "timeout_used_s", "detail"):
        assert k in d
    assert d["timeout_used_s"] == 2.5
    print("    OK")


# ---------------------------------------------------------------------------
# T-tranche: RetryDecision dataclass + LLMClient bounded-retry math
# ---------------------------------------------------------------------------

class _StubAlwaysOkAdapter:
    provider_name = "stub-ok"
    def is_available(self): return True
    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        return {"text": '{"k": "v"}'}


class _StubAlwaysRaisesAdapter:
    provider_name = "stub-raises"
    def is_available(self): return True
    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        raise ConnectionError("simulated transport failure")


def _make_llm_client(adapter, max_retries=1):
    from src.app.llm.client import LLMClient
    from src.app.execution.circuit_breaker import CircuitBreaker
    from src.app.observability.health_registry import HealthRegistry
    return LLMClient(adapter, HealthRegistry(), CircuitBreaker(), max_retries=max_retries)


def test_retry_decision_initial_success():
    print("  [90] RetryDecision: initial success → attempts_used=1, reason=initial_success")
    llm = _make_llm_client(_StubAlwaysOkAdapter(), max_retries=2)
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=10.0)
    rd = llm.last_retry_decision
    assert parsed is not None
    assert rd.attempts_used == 1
    assert rd.transport_failures == 0
    assert rd.parse_failures == 0
    assert rd.budget_exhausted is False
    assert rd.retry_decision_reason == RETRY_REASON_INITIAL_SUCCESS
    print("    OK")


def test_retry_decision_transport_retry_exhausted():
    print("  [91] RetryDecision: max_retries=2 + always-fail → attempts=3, transport_retry_exhausted")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=2)
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=10.0, total_deadline_s=120.0)
    rd = llm.last_retry_decision
    assert parsed is None
    assert rd.attempts_used == 3
    assert rd.transport_failures == 3
    assert rd.budget_exhausted is False
    assert rd.retry_decision_reason == RETRY_REASON_TRANSPORT_RETRY_EXHAUSTED
    print("    OK")


def test_retry_decision_budget_exhausted_before_retry():
    """Tight deadline forces the bounded-retry guard to skip the second attempt."""
    print("  [92] RetryDecision: tight deadline → budget_exhausted_before_retry")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=5)
    # request timeout 1s, deadline 1.001s — only one attempt fits
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=1.0, total_deadline_s=1.001)
    rd = llm.last_retry_decision
    assert parsed is None
    assert rd.budget_exhausted is True
    assert rd.retry_decision_reason == RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_RETRY
    assert rd.attempts_used == 1   # only the initial attempt happened
    print("    OK")


def test_retry_decision_budget_zero_skips_initial():
    """A literal zero deadline should skip even the initial attempt and report
    budget_exhausted_before_initial."""
    print("  [93] RetryDecision: total_deadline_s=0 → budget_exhausted_before_initial")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=2)
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=1.0, total_deadline_s=0.0)
    rd = llm.last_retry_decision
    assert parsed is None
    assert rd.budget_exhausted is True
    assert rd.retry_decision_reason == RETRY_REASON_BUDGET_EXHAUSTED_BEFORE_INITIAL
    # We never actually called generate(), so attempts_used stays at 0
    assert rd.attempts_used == 0
    print("    OK")


def test_retry_decision_unbounded_when_deadline_none():
    """total_deadline_s=None preserves the legacy count-only behavior."""
    print("  [94] RetryDecision: total_deadline_s=None → no budget enforcement")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=1)
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=1.0, total_deadline_s=None)
    rd = llm.last_retry_decision
    # Without a deadline, both attempts run; cooldown defaults to 2s but we
    # don't assert on cooldown here, only on attempt count.
    assert rd.attempts_used == 2
    assert rd.budget_exhausted is False
    print("    OK")


# ---------------------------------------------------------------------------
# T-tranche 2 — cooldown / fallback policy externalization
# ---------------------------------------------------------------------------

from src.app.execution.timeouts import (
    WaitDecision,
    clamp_wait_to_budget,
    DEFAULT_TRANSPORT_RETRY_COOLDOWN_S,
    DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S,
    DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S,
    DEFAULT_FALLBACK_RETRY_DELAY_S,
    WAIT_KIND_TRANSPORT_RETRY,
    WAIT_KIND_SCHEDULER_HEAVY,
    WAIT_KIND_SCHEDULER_LIGHT,
    WAIT_KIND_FALLBACK_RETRY,
    WAIT_SKIP_REASON_BUDGET_EXHAUSTED,
    WAIT_SKIP_REASON_CLAMPED_TO_BUDGET,
    WAIT_SKIP_REASON_ZERO_CONFIGURED,
    WAIT_SKIP_REASON_ALREADY_ELAPSED,
)
from src.app.execution.scheduler import Scheduler
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.core.contracts import TaskRequest


def test_unified_policy_carries_cooldown_defaults():
    """T-tranche 2: cooldown sub-section has the documented defaults."""
    print("  [95] UnifiedTimeoutPolicy default cooldown values")
    p = UnifiedTimeoutPolicy()
    assert p.transport_retry_cooldown_s == DEFAULT_TRANSPORT_RETRY_COOLDOWN_S == 2.0
    assert p.scheduler_cooldown_heavy_s == DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S == 2.0
    assert p.scheduler_cooldown_light_s == DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S == 0.5
    assert p.fallback_retry_delay_s == DEFAULT_FALLBACK_RETRY_DELAY_S == 2.0
    assert p.cooldown_source == "default"
    print("    OK")


def test_unified_policy_clamps_negative_cooldown_to_zero():
    print("  [96] UnifiedTimeoutPolicy clamps negative cooldown values to 0")
    p = UnifiedTimeoutPolicy(
        transport_retry_cooldown_s=-1,
        scheduler_cooldown_heavy_s=-1,
        scheduler_cooldown_light_s=-0.5,
        fallback_retry_delay_s=-100,
    )
    assert p.transport_retry_cooldown_s == 0.0
    assert p.scheduler_cooldown_heavy_s == 0.0
    assert p.scheduler_cooldown_light_s == 0.0
    assert p.fallback_retry_delay_s == 0.0
    print("    OK")


def test_unified_policy_cooldown_source_propagates():
    print("  [97] UnifiedTimeoutPolicy cooldown_source is preserved verbatim")
    p = UnifiedTimeoutPolicy(cooldown_source="test")
    assert p.cooldown_source == "test"
    print("    OK")


# ---------------------------------------------------------------------------
# clamp_wait_to_budget — five branches
# ---------------------------------------------------------------------------

def test_clamp_wait_zero_configured_skipped():
    print("  [98] clamp_wait_to_budget: configured_s<=0 → skipped/zero_configured")
    d = clamp_wait_to_budget(kind=WAIT_KIND_TRANSPORT_RETRY, configured_s=0,
                             total_deadline_s=10, elapsed_s=0)
    assert d.skipped and d.applied_s == 0
    assert d.skip_reason == WAIT_SKIP_REASON_ZERO_CONFIGURED
    print("    OK")


def test_clamp_wait_no_deadline_full_value():
    print("  [99] clamp_wait_to_budget: total_deadline_s=None → applied = configured")
    d = clamp_wait_to_budget(kind=WAIT_KIND_TRANSPORT_RETRY, configured_s=2.0,
                             total_deadline_s=None, elapsed_s=0)
    assert d.applied_s == 2.0
    assert not d.skipped and not d.clamped
    print("    OK")


def test_clamp_wait_plenty_of_budget_full_value():
    print("  [100] clamp_wait_to_budget: plenty of budget → applied = configured")
    d = clamp_wait_to_budget(kind=WAIT_KIND_TRANSPORT_RETRY, configured_s=2.0,
                             total_deadline_s=10.0, elapsed_s=0, headroom_s=1.0)
    assert d.applied_s == 2.0
    assert not d.clamped and not d.skipped
    print("    OK")


def test_clamp_wait_clamped_to_budget():
    print("  [101] clamp_wait_to_budget: tight budget → clamped to remaining")
    # remaining = 3.0 - 0 - 1.0 = 2.0; configured = 5.0; → applied = 2.0
    d = clamp_wait_to_budget(kind=WAIT_KIND_TRANSPORT_RETRY, configured_s=5.0,
                             total_deadline_s=3.0, elapsed_s=0, headroom_s=1.0)
    assert d.clamped is True
    assert d.applied_s == 2.0
    assert d.skip_reason == WAIT_SKIP_REASON_CLAMPED_TO_BUDGET
    print("    OK")


def test_clamp_wait_budget_exhausted():
    print("  [102] clamp_wait_to_budget: zero remaining → skipped/budget_exhausted")
    d = clamp_wait_to_budget(kind=WAIT_KIND_TRANSPORT_RETRY, configured_s=2.0,
                             total_deadline_s=1.0, elapsed_s=0.5, headroom_s=1.0)
    assert d.skipped is True
    assert d.applied_s == 0.0
    assert d.skip_reason == WAIT_SKIP_REASON_BUDGET_EXHAUSTED
    print("    OK")


def test_clamp_wait_carries_source_and_kind():
    print("  [103] clamp_wait_to_budget: source and kind are passed through")
    d = clamp_wait_to_budget(kind=WAIT_KIND_FALLBACK_RETRY, configured_s=1.0,
                             total_deadline_s=None, elapsed_s=0, source="settings")
    assert d.kind == WAIT_KIND_FALLBACK_RETRY
    assert d.source == "settings"
    print("    OK")


def test_wait_decision_to_dict_has_keys():
    print("  [104] WaitDecision.to_dict has expected keys")
    d = WaitDecision(
        kind=WAIT_KIND_SCHEDULER_HEAVY, configured_s=2.0, applied_s=0.5,
        clamped=True, source="policy",
    )
    serialized = d.to_dict()
    for k in ("kind", "configured_s", "applied_s", "clamped", "skipped", "skip_reason", "source"):
        assert k in serialized
    print("    OK")


# ---------------------------------------------------------------------------
# Scheduler — back-compat constructor + policy constructor
# ---------------------------------------------------------------------------

def test_scheduler_legacy_positional_constructor_back_compat():
    print("  [105] Scheduler legacy positional constructor still works")
    s = Scheduler()  # no args
    assert s.cooldown_heavy_s == DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S
    assert s.cooldown_light_s == DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S
    assert s.cooldown_source == "constructor_default"

    s2 = Scheduler(cooldown_heavy_s=4.0, cooldown_light_s=1.0)
    assert s2.cooldown_heavy_s == 4.0
    assert s2.cooldown_light_s == 1.0
    assert s2.cooldown_source == "constructor_default"
    print("    OK")


def test_scheduler_policy_constructor_takes_precedence():
    print("  [106] Scheduler(policy=) overrides positional defaults + sets source")
    p = UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=7.5,
        scheduler_cooldown_light_s=0.25,
        cooldown_source="settings",
    )
    s = Scheduler(policy=p)
    assert s.cooldown_heavy_s == 7.5
    assert s.cooldown_light_s == 0.25
    assert s.cooldown_source == "settings"
    print("    OK")


def test_scheduler_first_pre_execute_no_cooldown():
    print("  [107] Scheduler.pre_execute on first call → zero_configured skipped")
    s = Scheduler(policy=UnifiedTimeoutPolicy(cooldown_source="policy"))
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="x")
    decision = s.pre_execute(req)
    assert decision.skipped is True
    assert decision.applied_s == 0
    assert decision.skip_reason == WAIT_SKIP_REASON_ZERO_CONFIGURED
    assert s.last_wait_decision is decision
    print("    OK")


def test_scheduler_pre_execute_after_heavy_clamps_to_budget():
    """After a heavy task, the next pre_execute should produce a heavy
    cooldown WaitDecision that is clamped if the deadline is too tight."""
    print("  [108] Scheduler.pre_execute(heavy → next) clamps cooldown to budget")
    p = UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=2.0,
        scheduler_cooldown_light_s=0.5,
        cooldown_source="test",
    )
    s = Scheduler(policy=p)
    heavy = TaskRequest(domain="builder", task_name="requirement_parse", user_input="x")
    s.post_execute(heavy)  # mark heavy
    nxt = TaskRequest(domain="cad", task_name="constraint_parse", user_input="x")
    # tight: deadline 1.0, headroom 0.8 → available = 0.2; configured cooldown 2.0
    decision = s.pre_execute(nxt, total_deadline_s=1.0, request_headroom_s=0.8)
    assert decision.kind == WAIT_KIND_SCHEDULER_HEAVY
    # Either clamped to ~0.2 or skipped if elapsed wiped budget; either is correct.
    if decision.clamped:
        assert 0 < decision.applied_s < 2.0
    elif decision.skipped:
        assert decision.skip_reason in (
            WAIT_SKIP_REASON_BUDGET_EXHAUSTED,
            WAIT_SKIP_REASON_ALREADY_ELAPSED,
        )
    else:
        raise AssertionError(f"unexpected decision: {decision}")
    print("    OK")


def test_scheduler_pre_execute_after_light_uses_light_kind():
    print("  [109] Scheduler.pre_execute after light task → kind=scheduler_light")
    p = UnifiedTimeoutPolicy(
        scheduler_cooldown_heavy_s=2.0,
        scheduler_cooldown_light_s=0.5,
        cooldown_source="test",
    )
    s = Scheduler(policy=p)
    light = TaskRequest(domain="minecraft", task_name="style_check", user_input="x")
    s.post_execute(light)
    nxt = TaskRequest(domain="cad", task_name="constraint_parse", user_input="x")
    decision = s.pre_execute(nxt, total_deadline_s=120.0, request_headroom_s=1.0)
    assert decision.kind == WAIT_KIND_SCHEDULER_LIGHT
    print("    OK")


# ---------------------------------------------------------------------------
# LLMClient transport-fail cooldown derived from policy + clamped to budget
# ---------------------------------------------------------------------------

def test_llm_client_cooldown_value_from_policy_constructor():
    print("  [110] LLMClient transport_retry_cooldown_s comes from constructor (default+policy)")
    from src.app.llm.client import LLMClient
    from src.app.execution.circuit_breaker import CircuitBreaker
    from src.app.observability.health_registry import HealthRegistry

    # default (back-compat)
    llm = LLMClient(_StubAlwaysOkAdapter(), HealthRegistry(), CircuitBreaker())
    assert llm.transport_retry_cooldown_s == DEFAULT_TRANSPORT_RETRY_COOLDOWN_S
    assert llm.transport_retry_cooldown_source == "default"

    # explicit (T-tranche 2 wiring)
    llm2 = LLMClient(
        _StubAlwaysOkAdapter(), HealthRegistry(), CircuitBreaker(),
        transport_retry_cooldown_s=0.05,
        transport_retry_cooldown_source="settings",
    )
    assert llm2.transport_retry_cooldown_s == 0.05
    assert llm2.transport_retry_cooldown_source == "settings"
    print("    OK")


def test_llm_client_records_cooldown_decision_in_retry_decision():
    """When the LLMClient retries after a transport failure, the cooldown
    WaitDecision is appended to RetryDecision.cooldown_decisions."""
    print("  [111] LLMClient: cooldown decision is recorded in RetryDecision")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=1)
    # Use a very small cooldown so the test doesn't sleep 2s
    llm.transport_retry_cooldown_s = 0.01
    llm.transport_retry_cooldown_source = "test"
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=10.0, total_deadline_s=None)
    rd = llm.last_retry_decision
    # 1 transport failure + 1 retry → 1 cooldown decision recorded
    assert len(rd.cooldown_decisions) == 1, rd.cooldown_decisions
    cd = rd.cooldown_decisions[0]
    assert cd["kind"] == WAIT_KIND_TRANSPORT_RETRY
    assert cd["source"] == "test"
    # No deadline → fully applied
    assert cd["applied_s"] == 0.01
    assert rd.total_cooldown_ms == 10
    print("    OK")


def test_llm_client_cooldown_clamped_when_budget_tight():
    """Tight deadline → cooldown clamped down (not full configured value)."""
    print("  [112] LLMClient: cooldown clamped when total_deadline tight")
    llm = _make_llm_client(_StubAlwaysRaisesAdapter(), max_retries=1)
    llm.transport_retry_cooldown_s = 5.0
    llm.transport_retry_cooldown_source = "test"
    # timeout=1.0, deadline=2.5 → after first attempt budget left = ~2.5s,
    # headroom = 1.0 → cooldown can use at most 1.5s, clamped from 5.0
    parsed, raw, ms = llm.extract_slots("sys", "user", timeout_s=1.0, total_deadline_s=2.5)
    rd = llm.last_retry_decision
    # The first cooldown decision should be clamped (or skipped if budget gone)
    if rd.cooldown_decisions:
        cd = rd.cooldown_decisions[0]
        assert cd["clamped"] is True or cd["skipped"] is True
        assert cd["configured_s"] == 5.0
        assert cd["applied_s"] < 5.0
    print("    OK")


# ---------------------------------------------------------------------------
# DegradedModeHandler — policy wiring
# ---------------------------------------------------------------------------

def test_degraded_handler_policy_constructor():
    print("  [113] DegradedModeHandler(policy=) takes fallback delay from policy")
    p = UnifiedTimeoutPolicy(fallback_retry_delay_s=3.5, cooldown_source="settings")
    h = DegradedModeHandler(policy=p)
    assert h.fallback_retry_delay_s == 3.5
    assert h.fallback_retry_delay_source == "settings"
    print("    OK")


def test_degraded_handler_legacy_constructor_back_compat():
    print("  [114] DegradedModeHandler legacy constructor still works (default delay)")
    h = DegradedModeHandler()
    assert h.fallback_retry_delay_s == DEFAULT_FALLBACK_RETRY_DELAY_S
    assert h.fallback_retry_delay_source == "default"
    print("    OK")


def test_degraded_handler_records_wait_decision_on_handle_failure():
    print("  [115] DegradedModeHandler.handle_failure populates last_wait_decision")
    h = DegradedModeHandler(policy=UnifiedTimeoutPolicy(cooldown_source="settings"))
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="x")
    h.handle_failure(req, errors=["test"])
    assert h.last_wait_decision is not None
    assert h.last_wait_decision.kind == WAIT_KIND_FALLBACK_RETRY
    assert h.last_wait_decision.applied_s == 0.0   # current handler doesn't sleep
    assert h.last_wait_decision.source == "settings"
    print("    OK")


# ---------------------------------------------------------------------------
# bootstrap.Container wires everything through unified policy (smoke)
# ---------------------------------------------------------------------------

def test_bootstrap_build_unified_policy_from_settings():
    print("  [116] bootstrap._build_unified_policy synthesizes a policy from settings")
    from src.app.bootstrap import _build_unified_policy
    from src.app.settings import AppSettings
    s = AppSettings()
    s.fallback.retry_delay_s = 3.0
    p = _build_unified_policy(s)
    assert p.transport_retry_cooldown_s == 3.0
    assert p.fallback_retry_delay_s == 3.0
    assert p.cooldown_source == "settings"
    print("    OK")


TESTS = [
    test_resolve_cli_beats_env_and_settings,
    test_resolve_env_beats_settings,
    test_resolve_settings_used_when_env_absent,
    test_resolve_fails_when_no_source,
    test_resolve_rejects_garbage_cli,
    test_resolve_empty_string_cli_falls_through,
    test_resolve_carries_api_key_and_model_from_env,
    test_resolve_carries_api_key_and_model_from_settings_when_env_absent,
    test_mock_allowed_default_false,
    test_mock_allowed_cli_flag_true,
    test_mock_allowed_env_opt_in,
    test_require_live_passes_when_live_up,
    test_require_live_passes_when_mock_explicitly_allowed,
    test_require_live_raises_when_live_down_and_mock_not_allowed,
    test_counting_adapter_counts_calls_and_exceptions,
    test_counting_adapter_reset_case_isolates_per_case,
    test_counting_adapter_proxies_is_available,
    test_callcounters_first_attempt_success,
    test_callcounters_parse_retry_only,
    test_callcounters_transport_retry_only,
    test_callcounters_no_negative_parse_retry,
    test_percentile_basic,
    test_run_telemetry_finalize_basic,
    test_run_telemetry_serialization_round_trip,
    test_case_telemetry_from_result_pass_case,
    test_case_telemetry_from_result_fail_case,
    test_case_telemetry_from_result_dispatcher_error,
    test_build_timeout_policy_default_returns_stock_policy,
    test_build_timeout_policy_override_every_pool,
    test_build_timeout_policy_unknown_pool_falls_back_to_strict_json,
    test_build_timeout_policy_very_short_allowed,
    test_build_timeout_policy_zero_clamped_to_floor,
    test_parse_health_timeout_unset_returns_default,
    test_parse_health_timeout_int_string,
    test_parse_health_timeout_float_string,
    test_parse_health_timeout_invalid_falls_back_to_default,
    test_parse_health_timeout_zero_or_negative_clamped,
    test_parse_health_timeout_empty_string_treated_as_unset,
    test_normalize_max_retries_default,
    test_normalize_max_retries_explicit_zero,
    test_normalize_max_retries_explicit_two,
    test_normalize_max_retries_negative_clamped_to_zero,
    test_normalize_max_retries_garbage_falls_back_to_default,
    test_load_cases_dict_wrapper_form,
    test_load_cases_list_of_dicts_form,
    test_load_cases_list_of_tuples_form,
    test_load_cases_default_repo_file_loads_12,
    test_load_cases_missing_file_raises,
    test_load_cases_invalid_json_raises,
    test_load_cases_empty_list_raises,
    test_load_cases_dict_without_cases_key_raises,
    test_load_cases_top_level_scalar_raises,
    test_load_cases_entry_missing_fields_raises,
    test_load_cases_entry_wrong_type_raises,
    test_load_cases_preserves_order,
    # Task B — schema_version
    test_load_cases_wrapper_with_valid_schema_version_passes,
    test_load_cases_wrapper_missing_schema_version_raises,
    test_load_cases_wrapper_unsupported_schema_version_raises,
    test_load_cases_wrapper_non_string_schema_version_raises,
    test_load_cases_legacy_bare_list_still_allowed,
    test_load_cases_default_dataset_has_supported_schema_version,
    test_peek_schema_version_wrapper_form,
    test_peek_schema_version_legacy_list_returns_none,
    test_peek_schema_version_missing_file_returns_none,
    test_peek_schema_version_garbage_returns_none,
    # Task A — mock output isolation
    test_resolve_out_dir_live_default,
    test_resolve_out_dir_live_with_user_override_accepted,
    test_resolve_out_dir_mock_default_isolated_timestamped,
    test_resolve_out_dir_mock_refuses_live_root,
    test_resolve_out_dir_mock_refuses_unrelated_path,
    test_resolve_out_dir_mock_accepts_path_inside_mock_runs,
    test_resolve_out_dir_mock_refuses_live_root_alias_via_mock_runs_sibling,
    test_resolve_out_dir_mock_is_different_every_second,
    # T-tranche
    test_unified_default_health_matches_export_runtime,
    test_unified_policy_default_deadline_derives_from_count_and_timeout,
    test_unified_policy_explicit_deadline_at_least_request_timeout,
    test_unified_policy_to_legacy_timeout_policy_preserves_float,
    test_unified_policy_clamps_zero_request_timeout,
    test_unified_policy_clamps_negative_max_retries_to_zero,
    test_unified_policy_to_dict_has_effective_deadline,
    test_build_timeout_policy_preserves_subsecond_float,
    test_build_timeout_policy_accepts_int_too,
    test_vllm_http_adapter_default_health_timeout_imported_from_execution,
    test_vllm_http_adapter_constructor_default_health_timeout,
    test_classify_urllib_timeout,
    test_classify_urllib_url_error_refused,
    test_classify_urllib_http_error,
    test_classify_unexpected_falls_back,
    test_health_probe_result_to_dict_has_keys,
    test_retry_decision_initial_success,
    test_retry_decision_transport_retry_exhausted,
    test_retry_decision_budget_exhausted_before_retry,
    test_retry_decision_budget_zero_skips_initial,
    test_retry_decision_unbounded_when_deadline_none,
    # T-tranche 2 — cooldown / fallback policy externalization
    test_unified_policy_carries_cooldown_defaults,
    test_unified_policy_clamps_negative_cooldown_to_zero,
    test_unified_policy_cooldown_source_propagates,
    test_clamp_wait_zero_configured_skipped,
    test_clamp_wait_no_deadline_full_value,
    test_clamp_wait_plenty_of_budget_full_value,
    test_clamp_wait_clamped_to_budget,
    test_clamp_wait_budget_exhausted,
    test_clamp_wait_carries_source_and_kind,
    test_wait_decision_to_dict_has_keys,
    test_scheduler_legacy_positional_constructor_back_compat,
    test_scheduler_policy_constructor_takes_precedence,
    test_scheduler_first_pre_execute_no_cooldown,
    test_scheduler_pre_execute_after_heavy_clamps_to_budget,
    test_scheduler_pre_execute_after_light_uses_light_kind,
    test_llm_client_cooldown_value_from_policy_constructor,
    test_llm_client_records_cooldown_decision_in_retry_decision,
    test_llm_client_cooldown_clamped_when_budget_tight,
    test_degraded_handler_policy_constructor,
    test_degraded_handler_legacy_constructor_back_compat,
    test_degraded_handler_records_wait_decision_on_handle_failure,
    test_bootstrap_build_unified_policy_from_settings,
]


if __name__ == "__main__":
    print("=" * 60)
    print("export_runtime unit tests")
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
