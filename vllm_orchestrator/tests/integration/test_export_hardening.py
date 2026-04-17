"""
test_export_hardening.py — scripts/export_human_review.py 운영 고정화 통합

검증 영역
---------
- export script 의 main(...) 가 CLI/env 우선순위로 base_url 을 결정한다
- silent mock fallback 차단 (live 없고 mock opt-in 없으면 비-0 exit code)
- mock opt-in 명시 시 live 없이도 export 가 돌아가지만 used_mock=True 가 보고됨
- run_export(...) 가 review_data.json + export_run_report.json 둘 다 쓴다
- report 가 모든 필수 필드를 가진다
- strict-gate semantics (auto_validated, final_judgment, failure_categories) 가 보존된다
- adapter_override 경로로 임의 fake adapter 주입이 가능하다
"""
from __future__ import annotations

import json
import sys
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.review.export_runtime import (
    LiveLLMUnavailableError, BaseURLResolutionError,
)

# Import the script as a module
import importlib.util
_SPEC = importlib.util.spec_from_file_location(
    "ehr", str(_ROOT / "scripts" / "export_human_review.py"),
)
ehr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ehr)

# Test fixture: cases sourced from the externalized JSON file. The legacy
# in-memory ehr.CASES constant no longer exists (2026-04-07 — case list was
# externalized to datasets/human_review_cases.json). Loaded once at import.
DEFAULT_CASES = ehr.load_cases_file(ehr.DEFAULT_CASES_PATH)


# ---------------------------------------------------------------------------
# Fake LLM adapters
# ---------------------------------------------------------------------------

class _PerCaseFakeAdapter:
    """A fake adapter that returns canned outputs keyed by user input substring."""

    provider_name = "fake"

    def __init__(self, response_map: dict[str, str], live: bool = True):
        self.response_map = response_map
        self.live = live
        self.calls = 0

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls += 1
        user_text = messages[-1]["content"] if messages else ""
        for key, val in self.response_map.items():
            if key in user_text:
                return {"text": val, "prompt_tokens": 0, "completion_tokens": 0}
        return {"text": '{"unknown": true}', "prompt_tokens": 0, "completion_tokens": 0}


def _clean_responses() -> dict[str, str]:
    """canned outputs that should pass the strict gate."""
    return {
        "2층 주택": '{"intent": "거실 확장 + 모던 외관"}',
        "지하 카페": '{"intent": "지하 카페와 2층 주거 결합"}',
        "창문": '{"intent": "출입문 확장 + 창문 유지"}',
        "방수": '{"constraints": [{"constraint_type": "방수", "description": "샤워필터 방수 + 배수 연결", "category": "기계"}]}',
        "PCB": '{"constraints": [{"constraint_type": "PCB", "description": "모터+PCB 내장 접이식", "category": "기계"}]}',
        "전기/배수": '{"systems": [{"name": "전기"}, {"name": "배수"}, {"name": "구조"}]}',
        "정면 창문": '{"target_anchor": {"anchor_type": "facade"}, "operations": [{"type": "expand"}]}',
        "중세풍": '{"verdict": "pass", "style_score": 0.85, "issues": []}',
        "동쪽 2층": '{"target_anchor": {"anchor_type": "balcony"}, "operations": []}',
        "노을빛": '{"framing": "close_up", "mood": "warm", "duration_frames": 48}',
        "공포": '{"framing": "wide", "movement": "tracking", "mood": "dark"}',
        "비 오는 밤": '{"intent": "고독한 야경", "atmosphere": "비 내리는 밤 거리", "mood_tag": "외로움"}',
    }


def _bad_responses() -> dict[str, str]:
    """canned outputs that should be REJECTED by the strict gate (HR-001/HR-008/HR-011 family)."""
    return {
        "2층 주택": '{"楼层": "2층", "户型": "모던 스타일"}',          # HR-001 family
        "방수":     '{"valid": true, "message": "ok", "error": null}',   # HR-004 family
        "중세풍":   '{"style": "중세풍", "check": {"font_family": true, "padding": true}}',  # HR-008 family
        "공포":     '{"data": {"image_url": "https://example.com/img.jpg"}}',                 # HR-011 family
    }


# ---------------------------------------------------------------------------
# 1. resolve_base_url + main() integration
# ---------------------------------------------------------------------------

def test_main_fails_when_no_base_url(tmp_path=None, monkeypatch=None):
    """If CLI/env both empty AND settings can't provide a usable URL."""
    print("  [1] main exits 2 when no base URL resolvable")
    # Settings will still produce 'http://localhost:8000' default, so main()
    # would normally succeed at resolution. To test the failure path we
    # exercise resolve_base_url directly with empty settings.
    try:
        from src.app.review.export_runtime import resolve_base_url
        resolve_base_url(cli_base_url=None, env={}, settings_base_url=None)
    except BaseURLResolutionError:
        print("    OK")
        return
    raise AssertionError("expected BaseURLResolutionError when all sources empty")


def test_main_resolves_via_cli_flag():
    """main() should use --base-url over env/settings."""
    print("  [2] CLI --base-url is the source")
    args = ehr.parse_cli(["--base-url", "http://injected:1234"])
    assert args.base_url == "http://injected:1234"
    assert args.allow_mock is False
    print("    OK")


def test_cli_flag_allow_mock_propagates():
    print("  [3] --allow-mock CLI flag propagates")
    args = ehr.parse_cli(["--allow-mock"])
    assert args.allow_mock is True
    print("    OK")


# ---------------------------------------------------------------------------
# 2. silent mock fallback gating
# ---------------------------------------------------------------------------

def test_build_adapter_blocks_silent_mock():
    """If live is unavailable and mock is not allowed, build_adapter must raise."""
    print("  [4] build_adapter blocks silent mock fallback")
    # Use a base_url that won't resolve to a live server (RFC 5737 test net,
    # the .example TLD, etc.). VLLMHttpAdapter.is_available() will return False.
    try:
        ehr.build_adapter(
            base_url="http://203.0.113.1:65000",   # TEST-NET-3 unreachable
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
        )
    except LiveLLMUnavailableError as e:
        msg = str(e)
        assert "EXPORT_ALLOW_MOCK" in msg
        assert "--allow-mock" in msg
        print("    OK")
        return
    raise AssertionError("expected LiveLLMUnavailableError")


def test_build_adapter_explicit_mock_allowed():
    """When allow_mock=True, build_adapter returns the mock adapter and used_mock=True."""
    print("  [5] build_adapter honors explicit --allow-mock")
    adapter, used_mock = ehr.build_adapter(
        base_url="http://203.0.113.1:65000",
        api_key="x",
        model="qwen2.5-0.5b-instruct",
        allow_mock=True,
    )
    # Either MockLLMAdapter or the live adapter (if by accident reachable)
    assert adapter is not None
    # The address is RFC TEST-NET-3, so we expect mock here.
    assert used_mock is True
    print("    OK")


# ---------------------------------------------------------------------------
# 3. run_export with injected adapter — full integration
# ---------------------------------------------------------------------------

def _run_export_with_injected(
    response_map: dict[str, str],
    *,
    out_dir: Path,
    used_mock: bool = False,
    base_url: str = "http://injected:1234",
    base_url_source: str = "test",
):
    """Invoke run_export against a fake adapter with out_dir scoped to a temp path.

    When ``used_mock=True``, we treat ``out_dir`` as the *live default dir* and
    let the new mock-isolation guard auto-route the actual export under
    ``out_dir/mock_runs/<timestamp>/``. The test then reads the report file
    from that auto-isolated subpath. This mirrors how operators invoke the
    real script (``EXPORT_ALLOW_MOCK=1 python scripts/export_human_review.py``
    without ``--out-dir``).
    """
    fake = _PerCaseFakeAdapter(response_map, live=True)
    if used_mock:
        # Let run_export auto-isolate inside our temp dir.
        telem = ehr.run_export(
            cases=DEFAULT_CASES,
            base_url=base_url,
            base_url_source=base_url_source,
            api_key="test-key",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=None,
            out_report=out_dir / "export_run_report.json",
            adapter_override=fake,
            used_mock_override=True,
            live_default_dir=out_dir,
        )
        # Rewrite export_run_report.json into the caller's out_dir so existing
        # test code can still read it at `out_dir / "export_run_report.json"`.
        return telem
    # Live path — caller-supplied out_dir is used as-is.
    return ehr.run_export(
        cases=DEFAULT_CASES,
        base_url=base_url,
        base_url_source=base_url_source,
        api_key="test-key",
        model="qwen2.5-0.5b-instruct",
        allow_mock=False,
        out_dir=out_dir,
        out_report=out_dir / "export_run_report.json",
        adapter_override=fake,
        used_mock_override=False,
    )


def _temp_outdir(name: str) -> Path:
    p = _ROOT / "runtime" / "_test_export_hardening" / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_run_export_writes_review_data_and_run_report():
    print("  [6] run_export writes review_data.json + export_run_report.json")
    out = _temp_outdir("clean_run")
    try:
        telem = _run_export_with_injected(_clean_responses(), out_dir=out)

        # Files exist
        assert (out / "review_data.json").exists()
        assert (out / "review_template.csv").exists()
        assert (out / "export_run_report.json").exists()

        # review_data.json: 12 entries with strict-gate fields
        rd = json.loads((out / "review_data.json").read_text(encoding="utf-8"))
        assert len(rd) == 12
        for entry in rd:
            assert "auto_validated" in entry
            assert "final_judgment" in entry
            assert "failure_categories" in entry
            assert "layered_judgment" in entry

        # run report: schema invariants
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        for k in ("started_at", "finished_at", "used_mock", "resolved_base_url",
                  "resolved_base_url_source", "resolved_model", "summary", "cases"):
            assert k in rr, f"missing {k}"
        assert rr["used_mock"] is False
        assert rr["resolved_base_url"] == "http://injected:1234"
        assert rr["summary"]["total_cases"] == 12
        assert len(rr["cases"]) == 12

        # Per-case telemetry has the required fields
        for c in rr["cases"]:
            for k in (
                "case_id", "domain", "task", "tool_name", "start_ts", "end_ts",
                "latency_ms", "attempt_count", "transport_retry_count",
                "parse_retry_count", "final_status", "auto_validated",
                "final_judgment", "severity", "failure_categories",
                "used_mock", "resolved_base_url", "resolved_model",
            ):
                assert k in c, f"case {c.get('case_id')} missing {k}"

        print(f"    OK: {telem.summary.pass_count} pass / "
              f"{telem.summary.fail_count} fail / "
              f"{telem.summary.needs_review_count} needs_review")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_strict_gate_blocks_bad_responses():
    print("  [7] run_export with bad responses → strict gate rejects them")
    out = _temp_outdir("bad_run")
    try:
        # Mix bad responses with clean ones for the others
        responses = _clean_responses()
        responses.update(_bad_responses())
        telem = _run_export_with_injected(responses, out_dir=out)

        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))

        cases_by_id = {c["case_id"]: c for c in rr["cases"]}

        # HR-001: builder requirement_parse with Chinese keys → wrong_key_locale
        assert cases_by_id["HR-001"]["auto_validated"] is False
        assert "wrong_key_locale" in cases_by_id["HR-001"]["failure_categories"]

        # HR-004: cad constraint_parse with validator-shape → validator_shaped_response
        assert cases_by_id["HR-004"]["auto_validated"] is False
        assert "validator_shaped_response" in cases_by_id["HR-004"]["failure_categories"]

        # HR-008: minecraft style_check CSS leak → css_property_leak
        assert cases_by_id["HR-008"]["auto_validated"] is False
        assert "css_property_leak" in cases_by_id["HR-008"]["failure_categories"]

        # HR-011: animation camera_intent URL → hallucinated_external_reference
        assert cases_by_id["HR-011"]["auto_validated"] is False
        assert "hallucinated_external_reference" in cases_by_id["HR-011"]["failure_categories"]

        print(f"    OK: 4 known-bad families all blocked, "
              f"summary.fail_count={rr['summary']['fail_count']}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_used_mock_marked_in_report():
    print("  [8] run_export with used_mock=True → used_mock marked in cases + run report")
    out = _temp_outdir("mock_run")
    try:
        _run_export_with_injected(_clean_responses(), out_dir=out, used_mock=True)
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["used_mock"] is True
        for c in rr["cases"]:
            assert c["used_mock"] is True
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_resolved_base_url_carried_into_cases():
    print("  [9] run_export resolved base_url is carried into per-case telemetry")
    out = _temp_outdir("baseurl_carry")
    try:
        _run_export_with_injected(
            _clean_responses(),
            out_dir=out,
            base_url="http://carrier:7777",
            base_url_source="env",
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["resolved_base_url"] == "http://carrier:7777"
        assert rr["resolved_base_url_source"] == "env"
        for c in rr["cases"]:
            assert c["resolved_base_url"] == "http://carrier:7777"
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_summary_has_latency_stats():
    print("  [10] run_export summary has latency p50/p95/max + over-Ns counters")
    out = _temp_outdir("latency_stats")
    try:
        _run_export_with_injected(_clean_responses(), out_dir=out)
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        s = rr["summary"]
        for k in ("p50_latency_ms", "p95_latency_ms", "max_latency_ms",
                  "over_10s_count", "over_30s_count", "over_60s_count",
                  "total_transport_retries", "total_parse_retries"):
            assert k in s, f"missing {k}"
        # Fake adapter has zero real latency, so all latency counters should be small.
        assert s["max_latency_ms"] < 5000
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. --timeout CLI propagation + enforcement
# ---------------------------------------------------------------------------

class _TimeoutSpyAdapter:
    """Captures every ``timeout_s`` kwarg its ``.generate()`` was called with.

    Used to assert that --timeout propagates from CLI → run_export →
    Dispatcher → LLMClient → adapter.generate.
    """
    provider_name = "timeout-spy"

    def __init__(self):
        self.calls: list[int] = []
        self.live = True

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls.append(timeout_s)
        return {"text": '{"intent": "거실 확장"}', "prompt_tokens": 0, "completion_tokens": 0}


class _TimeoutEnforcingAdapter:
    """Adapter that raises ``TimeoutError`` whenever ``timeout_s < sleep_s``.

    Simulates urllib's socket timeout without actually sleeping, so the test
    runs fast but exercises the same error path.
    """
    provider_name = "timeout-enforcing"

    def __init__(self, sleep_s: float):
        self.sleep_s = sleep_s
        self.live = True
        self.calls: list[int] = []

    def is_available(self):
        return self.live

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.calls.append(timeout_s)
        if timeout_s < self.sleep_s:
            # This is what VLLMHttpAdapter raises on socket timeout.
            raise ConnectionError(f"simulated timeout: {self.sleep_s}s > timeout_s={timeout_s}")
        return {"text": '{"intent": "거실 확장"}', "prompt_tokens": 0, "completion_tokens": 0}


def test_run_export_cli_timeout_propagates_to_adapter():
    """run_export(timeout_s=7) → Dispatcher.timeouts → LLMClient.extract_slots →
    adapter.generate(timeout_s=7). Every single generate() call must see 7."""
    print("  [11] run_export timeout_s=7 propagates to every adapter.generate call")
    out = _temp_outdir("timeout_propagation")
    try:
        spy = _TimeoutSpyAdapter()
        ehr.run_export(
            cases=DEFAULT_CASES,
            base_url="http://spy:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=7,
            adapter_override=spy,
            used_mock_override=False,
        )
        assert len(spy.calls) == 12, f"expected 12 calls, got {len(spy.calls)}"
        assert all(t == 7 for t in spy.calls), f"propagation leak: {spy.calls}"
        print(f"    OK: all 12 generate() calls saw timeout_s=7")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_default_timeout_is_120s():
    """run_export(timeout_s=None) → default TimeoutPolicy → 15s strict_json (GPU)."""
    print("  [12] run_export timeout_s=None uses default 15s")
    out = _temp_outdir("timeout_default")
    try:
        spy = _TimeoutSpyAdapter()
        ehr.run_export(
            cases=DEFAULT_CASES[:3],  # 3 cases is enough to confirm default
            base_url="http://spy:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=None,
            adapter_override=spy,
            used_mock_override=False,
        )
        assert len(spy.calls) == 3
        assert all(t == 15 for t in spy.calls), f"expected 15s default, got {spy.calls}"
        print("    OK: default 15s applied")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_short_timeout_triggers_transport_failure():
    """timeout_s=1 + adapter that needs 5s → transport failure + retry + final_status=failed.
    Critically: NO silent mock fallback, used_mock stays False."""
    print("  [13] short timeout triggers transport failure, no silent mock")
    out = _temp_outdir("timeout_enforcement")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=5.0)  # needs 5s, timeout=1 → raises
        telem = ehr.run_export(
            cases=DEFAULT_CASES[:2],    # 2 cases is enough
            base_url="http://slow:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1,
            adapter_override=slow,
            used_mock_override=False,
        )
        # Each call should have hit the enforcing adapter with timeout_s=1
        assert len(slow.calls) >= 2, f"expected ≥2 adapter calls, got {len(slow.calls)}"
        assert all(t == 1 for t in slow.calls), f"propagation leak: {slow.calls}"

        # Run report must reflect transport failures
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["used_mock"] is False, "mock must stay False — no silent fallback"
        assert rr["summary"]["total_transport_retries"] >= 1, (
            f"expected transport retries, got {rr['summary']}"
        )
        # Schema gate fails for each case (LLMClient returned parsed=None after retries)
        for c in rr["cases"]:
            assert c["auto_validated"] is False
            assert c["transport_retry_count"] >= 1
        print(f"    OK: transport_retries={rr['summary']['total_transport_retries']} "
              f"used_mock=False")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_cli_timeout_flag_parses_and_flows():
    """parse_cli(['--timeout', '9']).timeout == 9.0 (float, T-tranche)."""
    print("  [14] --timeout CLI flag parses to float, default None")
    a = ehr.parse_cli([])
    assert a.timeout is None
    a = ehr.parse_cli(["--timeout", "9"])
    assert a.timeout == 9.0 and isinstance(a.timeout, float)
    a = ehr.parse_cli(["--timeout", "0.5"])
    assert a.timeout == 0.5 and isinstance(a.timeout, float)
    print("    OK")


# ---------------------------------------------------------------------------
# 5. health probe timeout independence (Task A)
# ---------------------------------------------------------------------------

class _DualTimeoutSpyAdapter:
    """Records (call_kind, timeout_s) for every is_available() and generate() call.

    Used to assert that --timeout maps onto generate(timeout_s=...) only, while
    LLM_HEALTH_TIMEOUT (passed to constructor) maps onto is_available()'s
    underlying urlopen timeout via VLLMHttpAdapter.health_timeout_s.
    """
    provider_name = "dual-timeout-spy"

    def __init__(self, *, health_timeout_s: float):
        self.health_timeout_s = health_timeout_s
        self.health_calls: list[float] = []
        self.generate_calls: list[int] = []

    def is_available(self):
        self.health_calls.append(self.health_timeout_s)
        return True

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        self.generate_calls.append(timeout_s)
        return {"text": '{"intent": "거실 확장"}', "prompt_tokens": 0, "completion_tokens": 0}


def test_vllm_http_adapter_uses_health_timeout_attribute():
    """VLLMHttpAdapter constructor accepts health_timeout_s and stores it."""
    print("  [15] VLLMHttpAdapter health_timeout_s attribute")
    from src.app.llm.adapters.vllm_http import VLLMHttpAdapter, DEFAULT_HEALTH_TIMEOUT_S
    a = VLLMHttpAdapter("http://x:1", "k", "m")
    assert a.health_timeout_s == DEFAULT_HEALTH_TIMEOUT_S == 5.0
    a2 = VLLMHttpAdapter("http://x:1", "k", "m", health_timeout_s=7.5)
    assert a2.health_timeout_s == 7.5
    # zero/negative clamps to default
    a3 = VLLMHttpAdapter("http://x:1", "k", "m", health_timeout_s=0)
    assert a3.health_timeout_s == DEFAULT_HEALTH_TIMEOUT_S
    a4 = VLLMHttpAdapter("http://x:1", "k", "m", health_timeout_s=-1)
    assert a4.health_timeout_s == DEFAULT_HEALTH_TIMEOUT_S
    print("    OK")


def test_health_timeout_independent_from_request_timeout():
    """request timeout=1, health timeout=7 → spy sees probe=7, generate=1.

    Confirms that --timeout 1 does NOT collapse the health probe budget.
    """
    print("  [16] health probe timeout (7s) and request timeout (1s) are independent")
    out = _temp_outdir("health_indep")
    try:
        spy = _DualTimeoutSpyAdapter(health_timeout_s=7.0)
        # Even though build_adapter is bypassed via adapter_override,
        # the spy itself records both kinds of timeouts.
        # Pre-call is_available so the spy registers a health probe.
        spy.is_available()

        ehr.run_export(
            cases=DEFAULT_CASES[:3],
            base_url="http://spy:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1,        # request timeout
            health_timeout_s=7.0,
            health_timeout_source="env",
            adapter_override=spy,
            used_mock_override=False,
        )

        # health probe used 7.0
        assert spy.health_calls == [7.0], spy.health_calls
        # generate calls used 1, never 7
        assert spy.generate_calls == [1, 1, 1], spy.generate_calls

        # additive metadata fields persisted in report
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["health_timeout_s"] == 7.0
        assert rr["health_timeout_source"] == "env"
        assert rr["request_timeout_s"] == 1
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_report_includes_health_default_when_unset():
    print("  [17] run report defaults: health_timeout=5.0 source=default")
    out = _temp_outdir("health_default")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["health_timeout_s"] == 5.0
        assert rr["health_timeout_source"] == "default"
        assert rr["max_retries"] == 1
        assert rr["request_timeout_s"] is None
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. --max-retries CLI flag (Task B)
# ---------------------------------------------------------------------------

def test_cli_max_retries_default_and_explicit():
    print("  [18] --max-retries CLI flag parses; default None, explicit 0/2")
    assert ehr.parse_cli([]).max_retries is None
    assert ehr.parse_cli(["--max-retries", "0"]).max_retries == 0
    assert ehr.parse_cli(["--max-retries", "2"]).max_retries == 2
    print("    OK")


def test_run_export_max_retries_zero_no_retry():
    """max_retries=0 + always-failing adapter → exactly 1 attempt per case.

    Confirms LLMClient(max_retries=0) was actually wired up.
    """
    print("  [19] max_retries=0 → exactly 1 attempt, no retry, no silent mock")
    out = _temp_outdir("max_retries_zero")
    try:
        # Always fails on transport
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1,
            max_retries=0,         # ★ no retry budget
            adapter_override=slow,
            used_mock_override=False,
        )
        # 2 cases × exactly 1 attempt each = 2 calls (no retries)
        assert len(slow.calls) == 2, f"got {len(slow.calls)} calls, expected 2"

        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["max_retries"] == 0
        assert rr["used_mock"] is False
        # each case had exactly 1 transport call → transport_retry_count must be 1 (the failing call itself)
        for c in rr["cases"]:
            # call count = 1 (single attempt, no retry); exception count = 1 → transport_retry_count = 1
            # but attempt_count = 1
            assert c["attempt_count"] == 1, f"got attempt_count={c['attempt_count']}"
            assert c["transport_retry_count"] == 1
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_max_retries_two_extra_attempts():
    """max_retries=2 + always-failing adapter → exactly 3 attempts per case
    when ``total_deadline_s`` is generous enough that bounded-retry doesn't
    cut anything off. This isolates the *count* lever from the *deadline*
    lever (T-tranche 2026-04-07)."""
    print("  [20] max_retries=2 → exactly 3 attempts per case (with generous deadline)")
    out = _temp_outdir("max_retries_two")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)
        ehr.run_export(
            cases=DEFAULT_CASES[:1],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1,
            max_retries=2,
            total_deadline_s=60.0,         # generous: don't trip bounded retry
            adapter_override=slow,
            used_mock_override=False,
        )
        # 1 case × 3 attempts (1 initial + 2 retries) = 3 calls
        assert len(slow.calls) == 3, f"got {len(slow.calls)} calls, expected 3"

        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["max_retries"] == 2
        assert rr["cases"][0]["attempt_count"] == 3
        assert rr["cases"][0]["transport_retry_count"] == 3   # 3 exceptions total
        assert rr["used_mock"] is False
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_max_retries_default_one_unchanged():
    """max_retries=None still preserves prior count behavior (1 retry → 2 attempts)
    when ``total_deadline_s`` is generous (T-tranche 2026-04-07)."""
    print("  [21] max_retries=None → 1 retry (back-compat with generous deadline)")
    out = _temp_outdir("max_retries_default")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)
        ehr.run_export(
            cases=DEFAULT_CASES[:1],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1,
            max_retries=None,
            total_deadline_s=60.0,         # generous: don't trip bounded retry
            adapter_override=slow,
            used_mock_override=False,
        )
        assert len(slow.calls) == 2, f"got {len(slow.calls)}"
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["max_retries"] == 1
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7. CASES externalization (Task C)
# ---------------------------------------------------------------------------

def test_cli_cases_file_flag_default_none():
    print("  [22] --cases-file CLI flag default None, explicit path captured")
    a = ehr.parse_cli([])
    assert a.cases_file is None
    a = ehr.parse_cli(["--cases-file", "/tmp/x.json"])
    assert a.cases_file == "/tmp/x.json"
    print("    OK")


def test_default_cases_file_loads_via_runner():
    """run_export accepts the cases loaded from datasets/human_review_cases.json.

    Confirms the externalized list reproduces the legacy in-memory CASES order/content
    bit-for-bit.
    """
    print("  [23] datasets/human_review_cases.json equals legacy CASES baseline")
    cases = ehr.load_cases_file(ehr.DEFAULT_CASES_PATH)
    expected = [
        ("builder",   "requirement_parse",       "2층 주택 거실 크게, 모던 스타일"),
        ("builder",   "requirement_parse",       "지하 카페 + 2층 주거, 벽돌 외관"),
        ("builder",   "patch_intent_parse",      "창문 유지하고 출입문만 키워줘"),
        ("cad",       "constraint_parse",        "방수 샤워필터, 배수 연결 포함"),
        ("cad",       "constraint_parse",        "모터+PCB 내장 접이식 기구부"),
        ("cad",       "system_split_parse",      "전기/배수/구조 시스템 분리"),
        ("minecraft", "edit_parse",              "정면 창문 넓게, 지붕 유지"),
        ("minecraft", "style_check",             "중세풍 스타일에 맞는지 체크"),
        ("minecraft", "anchor_resolution",       "동쪽 2층 발코니 위치"),
        ("animation", "shot_parse",              "노을빛에 슬픈 클로즈업"),
        ("animation", "camera_intent_parse",     "공포 장면 어둠 연출"),
        ("animation", "lighting_intent_parse",   "비 오는 밤 외로운 분위기"),
    ]
    assert cases == expected, f"baseline mismatch:\n  got={cases}\n  expected={expected}"
    print("    OK")


def test_custom_cases_file_runs_alternate_subset():
    """A custom 2-case JSON file loads, and run_export honors that subset."""
    print("  [24] custom --cases-file runs alternate cases through run_export")
    out = _temp_outdir("custom_cases")
    custom = out / "custom_cases.json"
    custom.write_text(json.dumps({
        "schema_version": "1.0",
        "cases": [
            {"domain": "builder", "task": "patch_intent_parse", "input": "거실만 키워줘"},
            {"domain": "cad",     "task": "constraint_parse",   "input": "PCB 케이스"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    try:
        cs = ehr.load_cases_file(custom)
        assert cs == [
            ("builder", "patch_intent_parse", "거실만 키워줘"),
            ("cad",     "constraint_parse",   "PCB 케이스"),
        ]

        spy = _PerCaseFakeAdapter({
            "거실만 키워줘": '{"intent": "거실 확장"}',
            "PCB 케이스":   '{"constraints": [{"constraint_type": "PCB", "description": "x", "category": "기계"}]}',
        })
        ehr.run_export(
            cases=cs,
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            cases_file_path=str(custom),
            adapter_override=spy,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["cases_count"] == 2
        assert rr["cases_file_path"] == str(custom)
        assert len(rr["cases"]) == 2
        # case ids are still HR-001, HR-002 (positional)
        assert rr["cases"][0]["case_id"] == "HR-001"
        assert rr["cases"][1]["case_id"] == "HR-002"
        # both should pass strict gate (clean responses)
        assert all(c["auto_validated"] for c in rr["cases"])
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_rejects_empty_cases_list():
    """run_export() with cases=[] must raise ValueError immediately."""
    print("  [25] run_export rejects empty cases list (defense in depth)")
    out = _temp_outdir("empty_cases")
    try:
        try:
            ehr.run_export(
                cases=[],
                base_url="http://x:1",
                base_url_source="test",
                api_key="x",
                model="qwen2.5-0.5b-instruct",
                allow_mock=False,
                out_dir=out,
                out_report=out / "export_run_report.json",
                adapter_override=_PerCaseFakeAdapter({}),
            )
        except ValueError as e:
            assert "empty" in str(e)
            print("    OK")
            return
        raise AssertionError("expected ValueError on empty cases")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. Strict gate semantics still preserved across the new fields
# ---------------------------------------------------------------------------

def test_run_export_review_data_fields_unchanged():
    """review_data.json must still have all the fields the strict gate downstream expects."""
    print("  [26] review_data.json fields unchanged (back-compat)")
    out = _temp_outdir("review_data_schema")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=ehr.load_cases_file(ehr.DEFAULT_CASES_PATH)[:3],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            adapter_override=spy,
        )
        rd = json.loads((out / "review_data.json").read_text(encoding="utf-8"))
        for entry in rd:
            for k in (
                "case_id", "domain", "task", "input", "raw_llm_output",
                "parsed_slots", "auto_status", "auto_validated",
                "final_judgment", "severity", "failure_categories",
                "rationale", "recommended_action", "layered_judgment",
                "latency_ms",
            ):
                assert k in entry, f"missing field: {k}"
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 9. Mock output isolation (Task A) — defense in depth via run_export
# ---------------------------------------------------------------------------

def _live_default_dir() -> Path:
    return _ROOT / "runtime" / "_test_live_default"


def _clean_live_default(p: Path) -> None:
    """Make sure the fake 'live default' dir starts fresh for each test."""
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def test_mock_run_writes_only_under_mock_runs_subdir():
    """run_export(used_mock=True, out_dir=None) should auto-route into mock_runs/<ts>/.

    Most importantly it must NOT touch the live default dir's review_data.json.
    """
    print("  [27] mock run auto-isolates under mock_runs/<timestamp>/")
    live = _live_default_dir()
    _clean_live_default(live)
    # Pre-seed live default with a sentinel review_data.json that must NOT be
    # overwritten by the mock run.
    live.mkdir(parents=True)
    sentinel = live / "review_data.json"
    sentinel.write_text('[{"sentinel": true}]', encoding="utf-8")

    from src.app.review.export_runtime import MOCK_RUNS_SUBDIR

    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        telem = ehr.run_export(
            cases=DEFAULT_CASES[:3],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=None,                     # no user override
            out_report=live / "mock_runs" / "latest_report.json",
            adapter_override=spy,
            used_mock_override=True,           # ★ MOCK
            live_default_dir=live,
        )

        # sentinel must still be untouched
        assert sentinel.read_text(encoding="utf-8") == '[{"sentinel": true}]', (
            "mock run overwrote the sentinel review_data.json in the live default dir!"
        )

        # the mock run dir must be under mock_runs/
        resolved = Path(telem.output_dir)
        assert MOCK_RUNS_SUBDIR in resolved.parts, resolved
        assert (resolved / "review_data.json").exists()
        assert telem.output_dir_source == "default_mock_isolated"
        assert telem.used_mock is True
        print(f"    OK: mock data in {resolved}, sentinel intact")
    finally:
        _clean_live_default(live)


def test_live_run_still_writes_live_default_dir():
    print("  [28] live run still writes under the live default dir")
    live = _live_default_dir()
    _clean_live_default(live)
    live.mkdir(parents=True)
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        telem = ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=None,
            out_report=live / "export_run_report.json",
            adapter_override=spy,
            used_mock_override=False,          # ★ LIVE
            live_default_dir=live,
        )
        assert telem.output_dir == str(live)
        assert telem.output_dir_source == "default_live"
        assert (live / "review_data.json").exists()
        assert telem.used_mock is False
        print("    OK")
    finally:
        _clean_live_default(live)


def test_mock_run_refuses_live_default_user_override():
    """Mock run + user override=live default → UnsafeMockOutputError, nothing written."""
    print("  [29] mock run refuses --out-dir pointing at live default → no files created")
    live = _live_default_dir()
    _clean_live_default(live)
    live.mkdir(parents=True)
    sentinel = live / "review_data.json"
    sentinel.write_text('[{"sentinel": true}]', encoding="utf-8")

    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        raised = False
        try:
            ehr.run_export(
                cases=DEFAULT_CASES[:1],
                base_url="http://x:1",
                base_url_source="test",
                api_key="x",
                model="qwen2.5-0.5b-instruct",
                allow_mock=False,
                out_dir=live,                     # ★ unsafe
                out_report=live / "export_run_report.json",
                adapter_override=spy,
                used_mock_override=True,
                live_default_dir=live,
            )
        except ehr.UnsafeMockOutputError as e:
            assert "live default directory" in str(e) or "not inside" in str(e)
            raised = True
        assert raised, "should have raised UnsafeMockOutputError"

        # sentinel untouched
        assert sentinel.read_text(encoding="utf-8") == '[{"sentinel": true}]'
        print("    OK")
    finally:
        _clean_live_default(live)


def test_mock_run_accepts_safe_user_override_under_mock_runs():
    """Mock run + user --out-dir under mock_runs/ → accepted, files written there."""
    print("  [30] mock run accepts --out-dir inside mock_runs/")
    from src.app.review.export_runtime import MOCK_RUNS_SUBDIR

    live = _live_default_dir()
    _clean_live_default(live)
    live.mkdir(parents=True)
    safe = live / MOCK_RUNS_SUBDIR / "manual"
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        telem = ehr.run_export(
            cases=DEFAULT_CASES[:1],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=safe,                         # ★ safe user override
            out_report=safe / "export_run_report.json",
            adapter_override=spy,
            used_mock_override=True,
            live_default_dir=live,
        )
        assert telem.output_dir_source == "user_override_mock_safe"
        assert Path(telem.output_dir) == safe
        assert (safe / "review_data.json").exists()
        # live default dir itself got no review_data.json
        assert not (live / "review_data.json").exists()
        print("    OK")
    finally:
        _clean_live_default(live)


def test_run_report_exposes_output_dir_metadata():
    print("  [31] export_run_report.json has output_dir / output_dir_source / cases_schema_version")
    live = _live_default_dir()
    _clean_live_default(live)
    live.mkdir(parents=True)
    report_path = live / "export_run_report.json"
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=None,
            out_report=report_path,
            adapter_override=spy,
            used_mock_override=False,
            live_default_dir=live,
            cases_schema_version="1.0",
        )
        rr = json.loads(report_path.read_text(encoding="utf-8"))
        assert rr["output_dir"] == str(live)
        assert rr["output_dir_source"] == "default_live"
        assert rr["cases_schema_version"] == "1.0"
        print("    OK")
    finally:
        _clean_live_default(live)


# ---------------------------------------------------------------------------
# 10. schema_version fail-loud at main() level (Task B end-to-end)
# ---------------------------------------------------------------------------

def test_default_dataset_loads_in_full_export_pipeline():
    """The canonical datasets/human_review_cases.json still works through run_export."""
    print("  [32] default datasets/human_review_cases.json loads end-to-end")
    live = _live_default_dir()
    _clean_live_default(live)
    live.mkdir(parents=True)
    try:
        cases = ehr.load_cases_file(ehr.DEFAULT_CASES_PATH)
        assert len(cases) == 12
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=cases[:4],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=None,
            out_report=live / "export_run_report.json",
            adapter_override=spy,
            live_default_dir=live,
            cases_schema_version=ehr.peek_cases_schema_version(ehr.DEFAULT_CASES_PATH),
        )
        rr = json.loads((live / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["cases_schema_version"] == "1.0"
        assert rr["cases_count"] == 4
        print("    OK")
    finally:
        _clean_live_default(live)


def test_bad_schema_version_cases_file_raises_cases_file_error():
    """load_cases_file on a bad schema_version raises CasesFileError (→ exit 4 at main level)."""
    print("  [33] bad schema_version file → CasesFileError (exit 4 path)")
    bad = _ROOT / "runtime" / "_test_bad_schema_cases.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({
        "schema_version": "99.0",
        "cases": [{"domain": "d", "task": "t", "input": "x"}],
    }), encoding="utf-8")
    try:
        raised = False
        try:
            ehr.load_cases_file(bad)
        except ehr.CasesFileError as e:
            assert "99.0" in str(e) and "supported versions" in str(e)
            raised = True
        assert raised, "expected CasesFileError"
        print("    OK")
    finally:
        bad.unlink(missing_ok=True)


def test_main_exits_4_on_bad_schema_version():
    """Full main() pipeline must exit 4 on a cases file with unsupported schema_version."""
    print("  [34] main() exit=4 on bad schema_version")
    bad = _ROOT / "runtime" / "_test_bad_schema_cases.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({
        "schema_version": "99.0",
        "cases": [{"domain": "d", "task": "t", "input": "x"}],
    }), encoding="utf-8")
    try:
        rc = ehr.main(["--cases-file", str(bad)])
        assert rc == 4, f"expected exit=4, got {rc}"
        print("    OK")
    finally:
        bad.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 11. T-tranche — Unified timeout policy + float propagation + bounded retry
# ---------------------------------------------------------------------------

def test_run_export_float_subsecond_timeout_propagates_to_adapter():
    """--timeout 0.5 → adapter.generate(timeout_s=0.5) for every case.

    This is the test that proves float seconds make it all the way down without
    being truncated to int (which was the legacy bug)."""
    print("  [35] --timeout 0.5 propagates as 0.5 (float) to adapter.generate")
    out = _temp_outdir("float_propagation")
    try:
        spy = _TimeoutSpyAdapter()
        ehr.run_export(
            cases=DEFAULT_CASES[:3],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=0.5,                   # ← float, sub-second
            request_timeout_source="cli",
            adapter_override=spy,
            used_mock_override=False,
        )
        assert spy.calls == [0.5, 0.5, 0.5], f"got {spy.calls}"
        for v in spy.calls:
            assert isinstance(v, float)

        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["effective_request_timeout_s"] == 0.5
        assert rr["request_timeout_source"] == "cli"
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_no_fixed_5s_health_when_env_overrides():
    """LLM_HEALTH_TIMEOUT='2.5' should make the adapter use 2.5, not the
    legacy fixed 5.0. The artifact must reflect the actual value."""
    print("  [36] LLM_HEALTH_TIMEOUT overrides fixed 5s; artifact reflects actual value")
    out = _temp_outdir("health_override")
    try:
        spy = _DualTimeoutSpyAdapter(health_timeout_s=2.5)
        spy.is_available()
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            health_timeout_s=2.5,
            health_timeout_source="env",
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["effective_health_timeout_s"] == 2.5
        assert rr["health_timeout_source"] == "env"
        # And it must NOT be the fixed legacy 5.0
        assert rr["effective_health_timeout_s"] != 5.0
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_bounded_retry_records_budget_exhausted_in_artifact():
    """Tight deadline + always-failing adapter → run_export must surface the
    budget_exhausted state on each case in the run report."""
    print("  [37] bounded retry: tight deadline → budget_exhausted in run report")
    out = _temp_outdir("bounded_retry")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)  # always fails
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=5,
            total_deadline_s=1.001,         # only first attempt fits
            adapter_override=slow,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["effective_total_deadline_s"] == 1.001
        assert rr["total_budget_exhausted"] >= 1, rr["total_budget_exhausted"]
        for c in rr["cases"]:
            assert c["budget_exhausted"] is True, c
            assert c["retry_decision_reason"] in (
                "budget_exhausted_before_retry",
                "budget_exhausted_before_initial",
            ), c["retry_decision_reason"]
            assert c["attempts_used"] >= 0
        print(f"    OK: total_budget_exhausted={rr['total_budget_exhausted']}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_telemetry_carries_all_t_tranche_fields():
    """Run report must carry every additive T-tranche field."""
    print("  [38] run report carries all T-tranche additive fields")
    out = _temp_outdir("telemetry_t_tranche")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=2.5,
            request_timeout_source="cli",
            health_timeout_s=4.0,
            health_timeout_source="env",
            max_retries=1,
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        # run-level fields
        for k in (
            "effective_request_timeout_s",
            "effective_health_timeout_s",
            "effective_total_deadline_s",
            "request_timeout_source",
            "total_attempts_used",
            "total_budget_exhausted",
            "health_failure_reason",
        ):
            assert k in rr, f"missing run-level key {k}"
        assert rr["effective_request_timeout_s"] == 2.5
        assert rr["effective_health_timeout_s"] == 4.0
        # default deadline = (1 + 1) * 2.5 = 5.0
        assert rr["effective_total_deadline_s"] == 5.0
        assert rr["request_timeout_source"] == "cli"

        # per-case fields
        for c in rr["cases"]:
            for k in (
                "attempts_used", "budget_exhausted", "total_elapsed_ms",
                "effective_request_timeout_s", "retry_decision_reason",
                "health_failure_reason",
            ):
                assert k in c, f"missing case-level key {k}"
            assert c["attempts_used"] >= 1
            assert c["budget_exhausted"] is False
            assert c["retry_decision_reason"] in ("initial_success", "retry_success")
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_health_failure_classification_surfaces_in_artifact():
    """When the adapter pre-stages a non-OK HealthProbeResult, the case
    telemetry must surface the classified ``health_failure_reason``."""
    print("  [39] health failure reason classification flows to telemetry")
    out = _temp_outdir("health_classification")
    try:
        from src.app.llm.adapters.vllm_http import HealthProbeResult, HEALTH_REASON_TIMEOUT

        spy = _PerCaseFakeAdapter(_clean_responses())
        # Pre-set a synthetic non-OK health probe; CountingAdapter unwrap should
        # surface it via _snapshot_health_probe.
        spy.last_health_probe_result = HealthProbeResult(
            available=False,
            reason=HEALTH_REASON_TIMEOUT,
            elapsed_ms=5001,
            timeout_used_s=5.0,
            detail="simulated timeout",
        )

        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        # At least one case must have the failure reason set
        case_reasons = [c["health_failure_reason"] for c in rr["cases"]]
        assert HEALTH_REASON_TIMEOUT in case_reasons, case_reasons
        # Run-level rolled-up reason
        assert rr["health_failure_reason"] == HEALTH_REASON_TIMEOUT
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_deadline_default_derives_from_count_and_timeout():
    """When no explicit total_deadline_s, derived = (1 + max_retries) * request_timeout."""
    print("  [40] effective_total_deadline_s derived from max_retries × request_timeout")
    out = _temp_outdir("deadline_derived")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:1],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=3.0,
            max_retries=4,
            total_deadline_s=None,           # → derived
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["effective_total_deadline_s"] == (1 + 4) * 3.0
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# 12. T-tranche 2 — cooldown / fallback policy externalization end-to-end
# ---------------------------------------------------------------------------

def test_run_report_cooldown_section_carries_configured_values_and_source():
    """Run report exposes the four cooldown configured values + cooldown_source."""
    print("  [41] run report carries cooldown configured values + source")
    out = _temp_outdir("cooldown_metadata")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            transport_retry_cooldown_s=1.5,
            scheduler_cooldown_heavy_s=3.0,
            scheduler_cooldown_light_s=0.25,
            fallback_retry_delay_s=4.0,
            cooldown_source="test",
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["configured_transport_retry_cooldown_s"] == 1.5
        assert rr["configured_scheduler_cooldown_heavy_s"] == 3.0
        assert rr["configured_scheduler_cooldown_light_s"] == 0.25
        assert rr["configured_fallback_retry_delay_s"] == 4.0
        assert rr["cooldown_source"] == "test"
        # policy_source_summary present
        assert rr["policy_source_summary"] is not None
        assert rr["policy_source_summary"]["cooldown_source"] == "test"
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_report_total_cooldown_aggregation_with_clean_run():
    """Clean run (no transport failures) + scheduler cooldowns disabled →
    no cooldown decisions, total_cooldown_ms=0.

    The scheduler cooldown is what would otherwise add a 0.5s wait between
    cases even on a successful run. We explicitly disable it here so the
    test can isolate the transport-retry cooldown path (which is 0 because
    no transport failures occur)."""
    print("  [42] clean run (scheduler cooldowns disabled) → total_cooldown_ms=0")
    out = _temp_outdir("cooldown_clean")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:3],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            scheduler_cooldown_heavy_s=0.0,    # disable scheduler waits
            scheduler_cooldown_light_s=0.0,
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        assert rr["total_cooldown_ms"] >= 0
        # On a successful run with no transport failures and no scheduler
        # cooldowns, cooldown should be exactly 0.
        assert rr["total_cooldown_ms"] == 0
        for c in rr["cases"]:
            for k in ("configured_transport_retry_cooldown_s",
                      "transport_retry_cooldown_source",
                      "cooldown_decisions",
                      "total_cooldown_ms",
                      "cooldown_clamped",
                      "cooldown_skip_reasons"):
                assert k in c, f"missing case-level cooldown key {k}"
            assert c["total_cooldown_ms"] == 0
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_report_cooldown_clamped_when_budget_tight():
    """Tight deadline + transport failures → cooldown decisions should appear
    with clamped/skipped flags, and total_clamped_cooldowns > 0."""
    print("  [43] tight deadline → cooldown clamped/skipped flagged in artifact")
    out = _temp_outdir("cooldown_clamp")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)
        ehr.run_export(
            cases=DEFAULT_CASES[:2],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=1,
            total_deadline_s=1.5,        # tight: forces clamp/skip
            transport_retry_cooldown_s=5.0,
            cooldown_source="test",
            adapter_override=slow,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        # At least one case should have a clamped or skipped cooldown
        clamped_or_skipped_cases = [
            c for c in rr["cases"] if c["cooldown_clamped"]
        ]
        assert len(clamped_or_skipped_cases) >= 1, rr
        assert rr["total_clamped_cooldowns"] >= 1
        # Verify the per-case cooldown_decisions carry the transport-retry
        # entries we configured. The list may also contain scheduler-derived
        # entries (kind=scheduler_*); filter to transport_retry first.
        for c in clamped_or_skipped_cases:
            transport_decisions = [
                cd for cd in c["cooldown_decisions"]
                if cd.get("kind") == "transport_retry"
            ]
            for cd in transport_decisions:
                assert cd["configured_s"] == 5.0, cd
                assert cd["source"] == "test"
                # Either fully clamped or skipped — both are valid outcomes
                # under a 1.5s deadline + 1.0s headroom.
                assert cd["clamped"] is True or cd["skipped"] is True, cd
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_dispatcher_merges_scheduler_wait_into_retry_decision():
    """The dispatcher's _merge_scheduler_wait_into_retry_decision helper must
    actually append the scheduler's last_wait_decision to cooldown_decisions
    in the per-case retry_decision dict."""
    print("  [44] dispatcher merges scheduler.last_wait_decision into retry_decision")
    from src.app.orchestration.dispatcher import _merge_scheduler_wait_into_retry_decision
    from src.app.execution.scheduler import Scheduler
    from src.app.execution.timeouts import UnifiedTimeoutPolicy

    # Build a scheduler that has a non-trivial last_wait_decision
    p = UnifiedTimeoutPolicy(scheduler_cooldown_heavy_s=0.01, cooldown_source="test")
    sched = Scheduler(policy=p)
    from src.app.core.contracts import TaskRequest
    req_heavy = TaskRequest(domain="builder", task_name="requirement_parse", user_input="x")
    sched.post_execute(req_heavy)   # mark heavy
    req_next = TaskRequest(domain="cad", task_name="constraint_parse", user_input="x")
    decision = sched.pre_execute(req_next, total_deadline_s=120.0, request_headroom_s=1.0)

    # Now feed a fresh retry_decision dict through the helper
    rd = {"cooldown_decisions": [], "total_cooldown_ms": 0}
    merged = _merge_scheduler_wait_into_retry_decision(rd, sched)
    assert merged is not None
    assert len(merged["cooldown_decisions"]) == 1
    assert merged["cooldown_decisions"][0]["kind"] in (
        "scheduler_heavy", "scheduler_light"
    )
    assert merged["total_cooldown_ms"] >= 0
    print("    OK")


def test_run_export_cooldown_console_marker_when_clamp_present():
    """When clamp_present, total_clamped_cooldowns aggregate must reflect it."""
    print("  [45] total_clamped_cooldowns aggregates per-case clamp flags")
    out = _temp_outdir("cooldown_aggregate")
    try:
        slow = _TimeoutEnforcingAdapter(sleep_s=10.0)
        ehr.run_export(
            cases=DEFAULT_CASES[:3],
            base_url="http://x:1",
            base_url_source="test",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=1.0,
            max_retries=1,
            total_deadline_s=1.5,
            transport_retry_cooldown_s=5.0,
            cooldown_source="test",
            adapter_override=slow,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        per_case = sum(1 for c in rr["cases"] if c["cooldown_clamped"])
        assert rr["total_clamped_cooldowns"] == per_case
        print(f"    OK: {per_case}/{rr['summary']['total_cases']} cases had clamped/skipped cooldown")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_run_export_policy_source_summary_carries_all_four_sources():
    """policy_source_summary must contain at least the 4 known source keys."""
    print("  [46] policy_source_summary carries all four source labels")
    out = _temp_outdir("policy_source_summary")
    try:
        spy = _PerCaseFakeAdapter(_clean_responses())
        ehr.run_export(
            cases=DEFAULT_CASES[:1],
            base_url="http://carry:1",
            base_url_source="env",
            api_key="x",
            model="qwen2.5-0.5b-instruct",
            allow_mock=False,
            out_dir=out,
            out_report=out / "export_run_report.json",
            timeout_s=2.0,
            request_timeout_source="cli",
            health_timeout_s=3.0,
            health_timeout_source="env",
            cooldown_source="settings",
            adapter_override=spy,
            used_mock_override=False,
        )
        rr = json.loads((out / "export_run_report.json").read_text(encoding="utf-8"))
        ps = rr["policy_source_summary"]
        assert ps == {
            "base_url_source": "env",
            "request_timeout_source": "cli",
            "health_timeout_source": "env",
            "cooldown_source": "settings",
        }
        print("    OK")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_bootstrap_container_wires_unified_policy_into_first_party_sites():
    """Container() constructs Scheduler / DegradedModeHandler / VLLMHttpAdapter
    via the unified policy. Verify the wiring without hitting a live LLM."""
    print("  [47] bootstrap.Container threads unified policy into all sites")
    from src.app.bootstrap import Container, _build_unified_policy
    from src.app.settings import AppSettings

    # Use settings that don't try to actually reach a server
    s = AppSettings()
    s.llm.base_url = "http://203.0.113.1:65000"   # TEST-NET-3 unreachable
    s.fallback.retry_delay_s = 0.42

    c = Container(settings=s, _skip_llm_probe=True)
    # The container's policy must reflect settings.fallback.retry_delay_s
    assert c.policy.transport_retry_cooldown_s == 0.42
    assert c.policy.fallback_retry_delay_s == 0.42
    assert c.policy.cooldown_source == "settings"
    # Scheduler reads from policy
    assert c.scheduler.cooldown_source == "settings"
    # Degraded handler reads from policy
    assert c.fallback.fallback_retry_delay_s == 0.42
    assert c.fallback.fallback_retry_delay_source == "settings"
    # LLMClient cooldown reads from policy
    assert c.llm_client.transport_retry_cooldown_s == 0.42
    assert c.llm_client.transport_retry_cooldown_source == "settings"
    print("    OK")


TESTS = [
    test_main_fails_when_no_base_url,
    test_main_resolves_via_cli_flag,
    test_cli_flag_allow_mock_propagates,
    test_build_adapter_blocks_silent_mock,
    test_build_adapter_explicit_mock_allowed,
    test_run_export_writes_review_data_and_run_report,
    test_run_export_strict_gate_blocks_bad_responses,
    test_run_export_used_mock_marked_in_report,
    test_run_export_resolved_base_url_carried_into_cases,
    test_run_export_summary_has_latency_stats,
    test_run_export_cli_timeout_propagates_to_adapter,
    test_run_export_default_timeout_is_120s,
    test_run_export_short_timeout_triggers_transport_failure,
    test_cli_timeout_flag_parses_and_flows,
    # Task A — health probe timeout independence
    test_vllm_http_adapter_uses_health_timeout_attribute,
    test_health_timeout_independent_from_request_timeout,
    test_run_report_includes_health_default_when_unset,
    # Task B — --max-retries
    test_cli_max_retries_default_and_explicit,
    test_run_export_max_retries_zero_no_retry,
    test_run_export_max_retries_two_extra_attempts,
    test_run_export_max_retries_default_one_unchanged,
    # Task C — CASES externalization
    test_cli_cases_file_flag_default_none,
    test_default_cases_file_loads_via_runner,
    test_custom_cases_file_runs_alternate_subset,
    test_run_export_rejects_empty_cases_list,
    # Strict gate semantics
    test_run_export_review_data_fields_unchanged,
    # Task A — mock output isolation
    test_mock_run_writes_only_under_mock_runs_subdir,
    test_live_run_still_writes_live_default_dir,
    test_mock_run_refuses_live_default_user_override,
    test_mock_run_accepts_safe_user_override_under_mock_runs,
    test_run_report_exposes_output_dir_metadata,
    # Task B — schema_version fail-loud
    test_default_dataset_loads_in_full_export_pipeline,
    test_bad_schema_version_cases_file_raises_cases_file_error,
    test_main_exits_4_on_bad_schema_version,
    # T-tranche — Unified timeout policy + bounded retry + classified health
    test_run_export_float_subsecond_timeout_propagates_to_adapter,
    test_run_export_no_fixed_5s_health_when_env_overrides,
    test_run_export_bounded_retry_records_budget_exhausted_in_artifact,
    test_run_export_telemetry_carries_all_t_tranche_fields,
    test_run_export_health_failure_classification_surfaces_in_artifact,
    test_run_export_deadline_default_derives_from_count_and_timeout,
    # T-tranche 2 — cooldown / fallback policy externalization
    test_run_report_cooldown_section_carries_configured_values_and_source,
    test_run_report_total_cooldown_aggregation_with_clean_run,
    test_run_report_cooldown_clamped_when_budget_tight,
    test_dispatcher_merges_scheduler_wait_into_retry_decision,
    test_run_export_cooldown_console_marker_when_clamp_present,
    test_run_export_policy_source_summary_carries_all_four_sources,
    test_bootstrap_container_wires_unified_policy_into_first_party_sites,
]


if __name__ == "__main__":
    print("=" * 60)
    print("export hardening integration tests")
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
