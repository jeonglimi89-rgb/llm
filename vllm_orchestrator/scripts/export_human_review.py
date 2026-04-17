"""
export_human_review.py — 핵심 태스크 인간 검수 패킷 생성

실행:
    cd vllm_orchestrator
    python scripts/export_human_review.py [옵션]

옵션
----
    --base-url URL          명시적 LLM endpoint (예: http://192.168.57.105:8000)
    --allow-mock            live 가 죽었을 때 mock adapter 로 의도적 fallback (loud)
    --out-dir PATH          review_data.json / review_template.csv 의 출력 디렉터리.
                            * live run 기본: runtime/human_review/
                            * mock run 기본: runtime/human_review/mock_runs/<timestamp>/
                            * mock run 에서 사용자 경로 지정 시 mock_runs/ 아래만 허용.
    --out-report PATH       export_run_report.json 출력 경로 (기본: runtime/human_review/export_run_report.json)
    --timeout SECONDS       per-call request timeout (초). 기본은 strict_json TimeoutPolicy 사용
    --max-retries N         transport / parse retry budget (기본 1; 0 허용; 음수는 0 으로 clamp)
    --cases-file PATH       case list JSON 파일 (기본: datasets/human_review_cases.json)

환경변수
--------
    LLM_BASE_URL            base_url 의 ENV 우선순위
    LLM_API_KEY             API key
    LLM_MODEL               모델 이름
    LLM_HEALTH_TIMEOUT      health probe timeout (초; 기본 5.0; --timeout 과 분리됨)
    EXPORT_ALLOW_MOCK       1/true/yes/on 이면 mock fallback 명시 허용

출력
----
    runtime/human_review/review_data.json         — 12건 input/output + layered judgment
    runtime/human_review/review_template.csv      — 사람이 채울 체크리스트
    runtime/human_review/export_run_report.json   — 운영 telemetry artifact (additive metadata)

NOTE 2026-04-06:
    ``auto_validated`` 는 더 이상 dispatcher 의 약한 boolean (JSON 파싱 성공)
    이 아니라 review/task_contracts.evaluate_task_contract 가 만든 5게이트
    통과 여부다. 동시에 layered_judgment 전체를 함께 저장해서 사람이 어떤
    게이트가 왜 실패했는지 바로 볼 수 있다.

NOTE 2026-04-06 (operational hardening):
    - 하드코딩 endpoint 제거. CLI > env > settings 순서로 명시 resolve.
    - silent mock fallback 차단. live 가 죽으면 명시적 ``--allow-mock`` /
      ``EXPORT_ALLOW_MOCK=1`` 가 없으면 LiveLLMUnavailableError 로 hard fail.
    - per-case + run-level telemetry → export_run_report.json.

NOTE 2026-04-07 (operational hardening — follow-up):
    - request timeout 과 health probe timeout 분리 (LLM_HEALTH_TIMEOUT).
    - retry budget 노출 (--max-retries; 0 허용).
    - case list 외부 파일화 (datasets/human_review_cases.json + --cases-file).
    - run report 에 additive metadata: request_timeout_s / health_timeout_s /
      health_timeout_source / max_retries / cases_file_path / cases_count.

NOTE 2026-04-07 (stability follow-up — mock isolation + schema versioning):
    - mock run 은 자동으로 runtime/human_review/mock_runs/<timestamp>/ 아래로
      격리되어 live review_data.json 을 절대 덮어쓸 수 없다.
    - mock run 에서 --out-dir 을 주면 반드시 mock_runs/ 하위 경로여야 하며,
      그 외에는 UnsafeMockOutputError → exit 5 로 hard fail 한다.
    - cases file 은 wrapper 형태일 때 schema_version 필드가 필수이며,
      현재 지원되는 값은 "1.0". mismatch 는 CasesFileError → exit 4.
    - run report 에 additive metadata: output_dir / output_dir_source /
      cases_schema_version.
"""
from __future__ import annotations

import sys, json, csv, os, time, argparse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.settings import AppSettings
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import (
    TimeoutPolicy, UnifiedTimeoutPolicy, DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_TRANSPORT_RETRY_COOLDOWN_S, DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S,
    DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S, DEFAULT_FALLBACK_RETRY_DELAY_S,
)
from src.app.llm.client import LLMClient
from src.app.llm.adapters.vllm_http import VLLMHttpAdapter
from src.app.llm.adapters.mock_llm import MockLLMAdapter
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.core.contracts import TaskRequest
from src.app.review.task_contracts import evaluate_task_contract
from src.app.review.export_runtime import (
    BaseURLResolutionError, LiveLLMUnavailableError, CasesFileError,
    UnsafeMockOutputError,
    resolve_base_url, mock_allowed, require_live_or_explicit_mock,
    CountingAdapter, RunTelemetry, write_run_report,
    case_telemetry_from_result, build_timeout_policy,
    parse_health_timeout, normalize_max_retries, load_cases_file,
    peek_cases_schema_version,
    resolve_mock_safe_out_dir, ResolvedOutDir,
    DEFAULT_HEALTH_TIMEOUT_S, DEFAULT_MAX_RETRIES, DEFAULT_CASES_FILENAME,
    MOCK_RUNS_SUBDIR,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "human_review"
DEFAULT_REPORT_PATH = OUT_DIR / "export_run_report.json"
DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent.parent / "datasets" / DEFAULT_CASES_FILENAME
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_cli(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="export_human_review",
        description="Run real LLM export of HR-001..HR-012 with strict gate + telemetry.",
    )
    p.add_argument("--base-url", default=None,
                   help="LLM base URL (CLI > env LLM_BASE_URL > settings)")
    p.add_argument("--allow-mock", action="store_true",
                   help="explicitly allow MockLLMAdapter fallback if live fails")
    p.add_argument("--out-dir", default=None,
                   help=f"output directory for review_data.json / review_template.csv. "
                        f"Live default: runtime/human_review/. Mock default: "
                        f"runtime/human_review/{MOCK_RUNS_SUBDIR}/<timestamp>/. "
                        f"Mock runs with a user-supplied --out-dir must point INSIDE "
                        f"{MOCK_RUNS_SUBDIR}/ or the script exits with UnsafeMockOutputError.")
    p.add_argument("--out-report", default=str(DEFAULT_REPORT_PATH),
                   help="path for export_run_report.json")
    p.add_argument("--timeout", type=float, default=None,
                   help="per-call request timeout in seconds (float allowed; e.g. 0.5). "
                        "Default: strict_json policy. Separate from health probe timeout "
                        "(LLM_HEALTH_TIMEOUT).")
    p.add_argument("--max-retries", type=int, default=None,
                   help=f"transport/parse retry budget (default {DEFAULT_MAX_RETRIES}; 0 allowed; negative clamped to 0)")
    p.add_argument("--cases-file", default=None,
                   help=f"path to a JSON file with HR cases (default: datasets/{DEFAULT_CASES_FILENAME})")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Adapter factory (live first, mock only on explicit opt-in)
# ---------------------------------------------------------------------------

def build_adapter(
    *,
    base_url: str,
    api_key: str,
    model: str,
    allow_mock: bool,
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S,
):
    """Resolve a real LLM adapter. Mock only on explicit opt-in.

    Returns a tuple ``(adapter, used_mock)`` where ``adapter`` may be a
    ``CountingAdapter`` wrapper. Raises ``LiveLLMUnavailableError`` if live
    is down and mock is not explicitly allowed.

    ``health_timeout_s`` is the deadline used for the ``/health`` probe and
    is intentionally **separate** from the request timeout (``--timeout``).
    """
    real = VLLMHttpAdapter(base_url, api_key, model, health_timeout_s=health_timeout_s)
    is_live = False
    try:
        is_live = real.is_available()
    except Exception:
        is_live = False

    require_live_or_explicit_mock(is_live, allow_mock=allow_mock, base_url=base_url)

    if is_live:
        return real, False

    # opt-in mock path
    print("[WARN] LIVE LLM unavailable. Using MockLLMAdapter (explicit opt-in).")
    print("       used_mock=true will be recorded in export_run_report.json.")
    return MockLLMAdapter(), True


# ---------------------------------------------------------------------------
# Run loop (extracted so integration tests can call it directly)
# ---------------------------------------------------------------------------

def run_export(
    *,
    cases: list[tuple[str, str, str]],
    base_url: str,
    base_url_source: str,
    api_key: str,
    model: str,
    allow_mock: bool,
    out_dir: Optional[Path] = None,
    out_report: Path = DEFAULT_REPORT_PATH,
    timeout_s: Optional[float] = None,                # float seconds (T-tranche)
    max_retries: Optional[int] = None,
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S,
    health_timeout_source: str = "default",
    cases_file_path: Optional[str] = None,
    cases_schema_version: Optional[str] = None,
    adapter_override=None,            # for tests: inject a fake adapter
    used_mock_override: Optional[bool] = None,
    circuit_override=None,            # for tests (T-tranche-17): inject a shared CircuitBreaker so tests can carry tripped state across phase-based run_export calls. Default None → fresh CircuitBreaker() as before.
    live_default_dir: Path = OUT_DIR,
    request_timeout_source: str = "default",          # T-tranche
    total_deadline_s: Optional[float] = None,         # T-tranche
    # T-tranche 2 (cooldown externalization)
    transport_retry_cooldown_s: float = DEFAULT_TRANSPORT_RETRY_COOLDOWN_S,
    scheduler_cooldown_heavy_s: float = DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S,
    scheduler_cooldown_light_s: float = DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S,
    fallback_retry_delay_s: float = DEFAULT_FALLBACK_RETRY_DELAY_S,
    cooldown_source: str = "default",
) -> RunTelemetry:
    """Execute the full export against the resolved/injected adapter.

    Returns a ``RunTelemetry`` describing the run. Also writes:
      - ``<resolved_out_dir>/review_data.json``
      - ``<resolved_out_dir>/review_template.csv``
      - ``out_report``  (export_run_report.json)

    The ``cases`` parameter is a positional list of ``(domain, task, input)``
    tuples — there is no longer a hard-coded default. Use ``load_cases_file()``
    to obtain it from ``datasets/human_review_cases.json`` (or a custom file).

    Mock output isolation (2026-04-07)
    ----------------------------------
    If the effective ``used_mock`` flag is True (whether computed here via
    ``build_adapter`` or injected via ``used_mock_override``), the output
    directory is resolved by ``resolve_mock_safe_out_dir`` so that the run
    **cannot** write to the live default directory. Any explicit ``out_dir``
    that is not strictly inside the mock_runs subtree raises
    ``UnsafeMockOutputError`` *before* a single file is created.
    """
    if not cases:
        # belt-and-suspenders: caller should have already raised CasesFileError,
        # but enforce here too so we never silently run a zero-case export.
        raise ValueError("run_export(): cases list is empty")

    if adapter_override is not None:
        base_adapter = adapter_override
        used_mock = bool(used_mock_override) if used_mock_override is not None else False
    else:
        base_adapter, used_mock = build_adapter(
            base_url=base_url, api_key=api_key, model=model, allow_mock=allow_mock,
            health_timeout_s=health_timeout_s,
        )

    # Mock output isolation — MUST happen before any mkdir/write. If this
    # raises UnsafeMockOutputError, no file has been touched yet.
    resolved_out = resolve_mock_safe_out_dir(
        used_mock=used_mock,
        live_default_dir=live_default_dir,
        user_out_dir=out_dir,
    )
    out_dir = resolved_out.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    counting = CountingAdapter(inner=base_adapter)

    # Timeout resolution: CLI --timeout / run_export(timeout_s=...) overrides
    # every pool. None → default TimeoutPolicy (strict_json_s=120 etc).
    # This policy propagates via Dispatcher.timeouts → LLMClient.extract_slots
    # → VLLMHttpAdapter.generate → urllib.urlopen(timeout=...).
    timeouts_policy = build_timeout_policy(timeout_s)

    # Retry budget: CLI --max-retries → LLMClient.max_retries.
    # 0 = no retry (single attempt). None = default (1).
    resolved_max_retries = normalize_max_retries(max_retries)

    # T-tranche 2026-04-07: build a UnifiedTimeoutPolicy that owns the
    # request / health / retry-deadline trio explicitly. This is the single
    # policy object the rest of the run reads from.
    # T-tranche 2 2026-04-08: the same policy now also owns cooldown values.
    unified = UnifiedTimeoutPolicy(
        request_timeout_s=(
            float(timeout_s) if timeout_s is not None else DEFAULT_REQUEST_TIMEOUT_S
        ),
        request_timeout_source=request_timeout_source,
        health_timeout_s=float(health_timeout_s),
        health_timeout_source=health_timeout_source,
        max_retries=resolved_max_retries,
        total_deadline_s=total_deadline_s,
        transport_retry_cooldown_s=float(transport_retry_cooldown_s),
        scheduler_cooldown_heavy_s=float(scheduler_cooldown_heavy_s),
        scheduler_cooldown_light_s=float(scheduler_cooldown_light_s),
        fallback_retry_delay_s=float(fallback_retry_delay_s),
        cooldown_source=cooldown_source,
    )
    effective_total_deadline_s = unified.effective_total_deadline_s()

    health = HealthRegistry()
    # T-tranche-17: tests can inject a shared CircuitBreaker via
    # circuit_override to carry tripped state across phase-based calls.
    # Default path is unchanged (fresh breaker with default params).
    circuit = circuit_override if circuit_override is not None else CircuitBreaker()
    queue = QueueManager(max_concurrency=1, max_depth=20)
    # Scheduler is now policy-driven (T-tranche 2). The legacy positional
    # constructor still works for external callers; here we use policy=.
    scheduler = Scheduler(policy=unified)
    # LLMClient transport_retry_cooldown_s is also policy-driven now.
    llm = LLMClient(
        counting, health, circuit,
        max_retries=resolved_max_retries,
        transport_retry_cooldown_s=float(unified.transport_retry_cooldown_s),
        transport_retry_cooldown_source=unified.cooldown_source,
    )
    # Tell the LLMClient about the wall-clock budget so its retry loop can
    # bound itself. The Dispatcher reads this attribute via getattr().
    llm.total_deadline_s_default = effective_total_deadline_s
    router = Router()
    # T-tranche-10: construct a DegradedModeHandler from the same unified
    # policy and thread it into the dispatcher. The handler is only used
    # for explainability (record_wait_decision on failure exit sites);
    # runtime semantics are unchanged.
    fallback_handler = DegradedModeHandler(policy=unified)
    dispatcher = Dispatcher(
        llm_client=llm, queue=queue, scheduler=scheduler, timeouts=timeouts_policy,
        fallback=fallback_handler,
    )

    from datetime import datetime, UTC as _UTC
    telem = RunTelemetry(
        started_at=datetime.now(_UTC).isoformat(),
        used_mock=used_mock,
        resolved_base_url=base_url,
        resolved_base_url_source=base_url_source,
        resolved_model=model,
        request_timeout_s=timeout_s,
        health_timeout_s=float(health_timeout_s),
        health_timeout_source=health_timeout_source,
        max_retries=resolved_max_retries,
        cases_file_path=cases_file_path,
        cases_count=len(cases),
        output_dir=str(out_dir),
        output_dir_source=resolved_out.source,
        cases_schema_version=cases_schema_version,
        # T-tranche additive
        effective_request_timeout_s=unified.request_timeout_s,
        effective_health_timeout_s=unified.health_timeout_s,
        effective_total_deadline_s=effective_total_deadline_s,
        request_timeout_source=request_timeout_source,
        # T-tranche 2 additive (cooldown sub-section)
        configured_transport_retry_cooldown_s=unified.transport_retry_cooldown_s,
        configured_scheduler_cooldown_heavy_s=unified.scheduler_cooldown_heavy_s,
        configured_scheduler_cooldown_light_s=unified.scheduler_cooldown_light_s,
        configured_fallback_retry_delay_s=unified.fallback_retry_delay_s,
        cooldown_source=unified.cooldown_source,
        policy_source_summary={
            "base_url_source": base_url_source,
            "request_timeout_source": request_timeout_source,
            "health_timeout_source": health_timeout_source,
            "cooldown_source": unified.cooldown_source,
        },
    )

    review_data: list[dict] = []

    for i, (domain, task, text) in enumerate(cases, 1):
        case_id = f"HR-{i:03d}"
        print(f"  [{i:>2}/{len(cases)}] {domain}.{task}: \"{text[:30]}\"...")

        counting.reset_case()
        case_start = time.time()
        req = TaskRequest(domain=domain, task_name=task, user_input=text)
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        case_end = time.time()

        # idempotent re-evaluation if dispatcher path didn't attach layered_judgment
        layered = result.layered_judgment
        if layered is None:
            judgment = evaluate_task_contract(
                task_type=req.task_type,
                user_input=text,
                payload=result.slots,
                schema_validated=(result.slots is not None),
                artifact_id=case_id,
            )
            layered = judgment.to_dict()

        entry = {
            "case_id": case_id,
            "domain": domain,
            "task": task,
            "input": text,
            "raw_llm_output": result.raw_text or "",
            "parsed_slots": result.slots,
            "auto_status": result.status,
            "auto_validated": bool(layered.get("auto_validated", False)),
            "final_judgment": layered.get("final_judgment"),
            "severity": layered.get("severity"),
            "failure_categories": layered.get("failure_categories", []),
            "rationale": layered.get("rationale"),
            "recommended_action": layered.get("recommended_action"),
            "layered_judgment": layered,
            "latency_ms": result.latency_ms,
        }
        review_data.append(entry)

        # Per-case telemetry
        result_dict = result.to_dict() if hasattr(result, "to_dict") else {
            "status": result.status, "latency_ms": result.latency_ms,
            "layered_judgment": layered,
        }
        ct = case_telemetry_from_result(
            case_id=case_id, domain=domain, task=task,
            start_ts=case_start, end_ts=case_end,
            counters=counting.snapshot(),
            task_result_dict=result_dict,
            used_mock=used_mock,
            resolved_base_url=base_url,
            resolved_model=model,
        )
        telem.add_case(ct)

        status = "OK" if result.status == "done" else result.status
        gate = "VALID" if entry["auto_validated"] else f"REJECT[{layered.get('final_judgment')}]"
        retries_str = ""
        if ct.transport_retry_count or ct.parse_retry_count:
            retries_str = f" retries[transport={ct.transport_retry_count} parse={ct.parse_retry_count}]"
        budget_str = " BUDGET_EXHAUSTED" if ct.budget_exhausted else ""
        print(f"         {status} {gate} ({result.latency_ms}ms){retries_str}{budget_str}")

    # finalize summary
    telem.finalize()

    # write review_data.json
    (out_dir / "review_data.json").write_text(
        json.dumps(review_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # CSV template
    with open(out_dir / "review_template.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "case_id", "domain", "task", "input",
            "auto_status", "auto_validated", "final_judgment", "severity",
            "failure_categories", "parsed_slots_summary",
            "human_verdict", "reason",
        ])
        for entry in review_data:
            slots_summary = (
                json.dumps(entry["parsed_slots"], ensure_ascii=False)[:80]
                if entry["parsed_slots"] else ""
            )
            writer.writerow([
                entry["case_id"], entry["domain"], entry["task"], entry["input"],
                entry["auto_status"], entry["auto_validated"],
                entry.get("final_judgment", ""), entry.get("severity", ""),
                ",".join(entry.get("failure_categories", []) or []),
                slots_summary, "", "",
            ])

    # write run report
    write_run_report(telem, out_report)

    # console summary
    s = telem.summary
    print(f"\nExported {len(review_data)} cases to {out_dir}")
    print(f"  review_data.json     : full input/output + layered judgment")
    print(f"  review_template.csv  : fill in human_verdict + reason columns")
    print(f"  export_run_report.json: telemetry @ {out_report}")
    print(f"  output_dir           : {out_dir} (source={resolved_out.source})")
    print(f"  cases source         : {cases_file_path or '(in-memory)'} "
          f"(schema_version={cases_schema_version or '(legacy)'}, {len(cases)} cases)")
    print(f"  resolved base_url    : {base_url} (source={base_url_source})")
    print(f"  resolved model       : {model or '(unset)'}")
    print(f"  used_mock            : {used_mock}")
    print(f"  request_timeout_s    : {timeout_s if timeout_s is not None else '(default policy)'} (source={request_timeout_source})")
    print(f"  health_timeout_s     : {health_timeout_s} (source={health_timeout_source})")
    print(f"  max_retries          : {resolved_max_retries}")
    print(f"  effective_total_deadline_s: {effective_total_deadline_s}")
    print(f"  cooldown[transport_retry/sched_heavy/sched_light/fallback] : "
          f"{unified.transport_retry_cooldown_s}/"
          f"{unified.scheduler_cooldown_heavy_s}/"
          f"{unified.scheduler_cooldown_light_s}/"
          f"{unified.fallback_retry_delay_s} (source={unified.cooldown_source})")
    print(f"  Run done             : {s.succeeded_cases}/{s.total_cases}")
    print(f"  Layered VALID        : {s.pass_count}/{s.total_cases}  (strict gate)")
    print(f"  Layered FAIL         : {s.fail_count}/{s.total_cases}")
    print(f"  Layered NEEDS_REVIEW : {s.needs_review_count}/{s.total_cases}")
    print(f"  Latency p50/p95/max  : {s.p50_latency_ms}/{s.p95_latency_ms}/{s.max_latency_ms} ms")
    print(f"  Latency >10/>30/>60s : {s.over_10s_count}/{s.over_30s_count}/{s.over_60s_count}")
    print(f"  Retries total        : transport={s.total_transport_retries} parse={s.total_parse_retries}")
    print(f"  Budget exhausted     : {telem.total_budget_exhausted}/{s.total_cases}")
    print(f"  Total cooldown_ms    : {telem.total_cooldown_ms}")
    print(f"  Cases w/ clamped CD  : {telem.total_clamped_cooldowns}/{s.total_cases}")
    if telem.health_failure_reason:
        print(f"  Health failure reason: {telem.health_failure_reason}")

    return telem


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    args = parse_cli(argv)

    settings = AppSettings.from_env()

    # base URL resolution: CLI > env > settings (env was already merged into settings.from_env)
    # We pass settings_base_url separately so the precedence note in docs holds even if
    # env was unset (settings.from_env then carries the dataclass default).
    try:
        ep = resolve_base_url(
            cli_base_url=args.base_url,
            env=os.environ,
            settings_base_url=settings.llm.base_url,
            settings_api_key=settings.llm.api_key,
            settings_model=settings.llm.model,
        )
    except BaseURLResolutionError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 2

    allow_mock = args.allow_mock or mock_allowed(env=os.environ)

    # health probe timeout (separate from request timeout)
    health_timeout_s, health_timeout_source = parse_health_timeout(env=os.environ)

    # cases file (CLI --cases-file > default datasets path)
    cases_path = Path(args.cases_file) if args.cases_file else DEFAULT_CASES_PATH
    try:
        cases = load_cases_file(cases_path)
    except CasesFileError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 4
    cases_schema_version = peek_cases_schema_version(cases_path)

    # max retries (None → default 1, 0 allowed, negative clamped to 0)
    resolved_max_retries = normalize_max_retries(args.max_retries)

    # output directory (CLI --out-dir > resolve_mock_safe_out_dir default)
    # resolve_mock_safe_out_dir runs again inside run_export() for defense-in-depth.
    user_out_dir = Path(args.out_dir) if args.out_dir else None

    print("=" * 60)
    print("Human Review Export")
    print("=" * 60)
    print(f"  resolved base_url : {ep.base_url}  (source={ep.source})")
    print(f"  resolved model    : {ep.model or '(unset)'}")
    print(f"  allow_mock        : {allow_mock}")
    print(f"  request timeout   : {args.timeout if args.timeout is not None else '(default TimeoutPolicy)'}")
    print(f"  health timeout    : {health_timeout_s}s (source={health_timeout_source})")
    print(f"  max_retries       : {resolved_max_retries}")
    print(f"  cases file        : {cases_path}  "
          f"(schema_version={cases_schema_version or '(legacy)'}, {len(cases)} cases)")
    print(f"  --out-dir         : {user_out_dir or '(auto)'}")

    try:
        run_export(
            cases=cases,
            base_url=ep.base_url,
            base_url_source=ep.source,
            api_key=ep.api_key,
            model=ep.model,
            allow_mock=allow_mock,
            out_dir=user_out_dir,
            out_report=Path(args.out_report),
            timeout_s=args.timeout,
            request_timeout_source=("cli" if args.timeout is not None else "default"),
            max_retries=args.max_retries,
            health_timeout_s=health_timeout_s,
            health_timeout_source=health_timeout_source,
            cases_file_path=str(cases_path),
            cases_schema_version=cases_schema_version,
        )
    except LiveLLMUnavailableError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 3
    except UnsafeMockOutputError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
