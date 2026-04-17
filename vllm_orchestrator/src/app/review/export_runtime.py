"""
review/export_runtime.py — human-review export 운영 고정화 helpers

목적
----
scripts/export_human_review.py 가 그동안 가지고 있던 운영 위험 3가지를 작은
reusable 모듈로 분리해서 export 가 환경 안전 + 관측 가능하게 만든다.

다루는 위험
1. **하드코딩 endpoint**
   기존: ``VLLMHttpAdapter("http://192.168.57.105:8000", ...)`` 라인 한 줄.
   WSL2 IP 가 바뀌면 그대로 깨짐. ``resolve_base_url(...)`` 가 CLI > env >
   settings > legitimate repo default 순서로 명시적 precedence 를 강제한다.
   유효 source 가 하나도 없으면 명확한 에러로 실패한다 — *조용한 fallback 없음*.

2. **silent mock fallback**
   기존: live 가 안 잡히면 ``[WARN] LLM not available, using mock`` 한 줄
   찍고 MockLLMAdapter 로 전환. 이러면 export 가 거짓 성공한다.
   ``mock_allowed(...)`` 가 default False, 명시적 opt-in (env / CLI) 만 허용.
   opt-in 없이 live 가 죽으면 ``LiveLLMUnavailableError`` 로 큰 소리로 실패.

3. **얇은 telemetry**
   기존: case 별 latency_ms 만. retry, transport failure, parse failure,
   percentile 등 운영 지표 전무. ``CountingAdapter`` 가 .generate() 호출 횟수
   와 exception 횟수를 비침습적으로 카운트하고, ``CaseTelemetry`` /
   ``RunTelemetry`` 가 case-level + run-level 집계를 dataclass 로 표현한다.
   ``write_run_report(...)`` 가 ``runtime/human_review/export_run_report.json``
   으로 머신 리더블 artifact 를 떨어뜨린다.

이 모듈은 LLMClient / dispatcher / strict gate 의 동작을 일체 바꾸지 않는다.
adapter wrapper + CLI + 헬퍼 함수만 추가한다.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional

from ..execution.timeouts import (
    TimeoutPolicy,
    UnifiedTimeoutPolicy,
    DEFAULT_HEALTH_TIMEOUT_S as _UNIFIED_DEFAULT_HEALTH_TIMEOUT_S,
    DEFAULT_REQUEST_TIMEOUT_S as _UNIFIED_DEFAULT_REQUEST_TIMEOUT_S,
)
from ..settings import TimeoutSettings


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BaseURLResolutionError(RuntimeError):
    """resolve_base_url 가 사용 가능한 source 를 하나도 못 찾았을 때."""


class LiveLLMUnavailableError(RuntimeError):
    """live 서버 접속 실패인데 mock opt-in 도 없을 때."""


class CasesFileError(RuntimeError):
    """cases file 로딩 실패 — 파일 없음 / JSON parse 실패 / 빈 리스트 / 잘못된 타입 / schema_version mismatch."""


class UnsafeMockOutputError(RuntimeError):
    """mock run 이 live 산출물 경로(또는 그 하위)로 쓰려고 할 때.

    운영 사고 방지용 guard. 자세한 규칙은 resolve_mock_safe_out_dir 의
    docstring 참조.
    """


# ---------------------------------------------------------------------------
# Phase 1 — Endpoint resolution
# ---------------------------------------------------------------------------

# 정확히 명시 — 거짓 fallback 만들지 않는다
SETTINGS_DEFAULT_BASE_URL = "http://localhost:8000"


@dataclass
class ResolvedEndpoint:
    """resolve_base_url 의 반환값 — 어디서 왔는지 출처까지 같이."""
    base_url: str
    source: str        # "cli" | "env" | "settings" | "default"
    api_key: str
    model: str

    def to_dict(self) -> dict:
        return asdict(self)


def _is_usable_url(url: Optional[str]) -> bool:
    if url is None:
        return False
    s = url.strip()
    if not s:
        return False
    return s.startswith("http://") or s.startswith("https://")


def resolve_base_url(
    *,
    cli_base_url: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    settings_base_url: Optional[str] = None,
    settings_api_key: Optional[str] = None,
    settings_model: Optional[str] = None,
) -> ResolvedEndpoint:
    """LLM endpoint 를 결정론적 precedence 로 결정한다.

    Precedence
    ----------
        1. cli_base_url     — explicit ``--base-url`` flag
        2. env LLM_BASE_URL — explicit env var
        3. settings_base_url — AppSettings.from_env().llm.base_url 같은 repo-native source
                              (이 source 가 실제로 ``http://localhost:8000`` 같은
                              합리적 default 를 갖고 있으면 그것을 사용)
        4. fail loud         — usable source 없음 → BaseURLResolutionError

    Notes
    -----
    - "default" 라는 source 는 settings 안에 이미 있는 경우만 인정한다. 모듈
      자체가 별도 hardcoded fallback 을 들고 있지 않는다.
    - api_key, model 은 env (LLM_API_KEY / LLM_MODEL) > settings 순서로 조립.
    """
    env = env if env is not None else os.environ

    # 1. CLI
    if _is_usable_url(cli_base_url):
        url, source = cli_base_url.strip(), "cli"
    else:
        # 2. ENV
        env_url = env.get("LLM_BASE_URL")
        if _is_usable_url(env_url):
            url, source = env_url.strip(), "env"
        elif _is_usable_url(settings_base_url):
            # 3. SETTINGS (이미 ENV 를 안 본 경우의 settings 객체 — 또는 default 만 들고 있는 settings)
            url, source = settings_base_url.strip(), "settings"
        else:
            checked = []
            if cli_base_url is not None:
                checked.append(f"cli={cli_base_url!r}")
            checked.append(f"env LLM_BASE_URL={env.get('LLM_BASE_URL')!r}")
            checked.append(f"settings_base_url={settings_base_url!r}")
            raise BaseURLResolutionError(
                "no usable LLM base URL. Checked sources in precedence order: "
                + " | ".join(checked)
                + ". Set --base-url, LLM_BASE_URL, or AppSettings.llm.base_url."
            )

    api_key = (env.get("LLM_API_KEY") or settings_api_key or "").strip()
    model = (env.get("LLM_MODEL") or settings_model or "").strip()

    return ResolvedEndpoint(base_url=url, source=source, api_key=api_key, model=model)


# ---------------------------------------------------------------------------
# Health probe timeout — separate from request timeout
# ---------------------------------------------------------------------------

# T-tranche 2026-04-07: re-export from execution.timeouts to keep a single
# source-of-truth. Backward compatible — older callers that imported
# ``DEFAULT_HEALTH_TIMEOUT_S`` from this module still get the same value.
DEFAULT_HEALTH_TIMEOUT_S: float = _UNIFIED_DEFAULT_HEALTH_TIMEOUT_S
_HEALTH_TIMEOUT_FLOOR: float = 0.1


def parse_health_timeout(env: Optional[dict[str, str]] = None) -> tuple[float, str]:
    """Resolve the health probe timeout (seconds) from env.

    Returns ``(timeout_s, source)`` where ``source`` is one of:
      - ``"default"``  — env var unset → DEFAULT_HEALTH_TIMEOUT_S (5.0)
      - ``"env"``      — LLM_HEALTH_TIMEOUT parsed cleanly
      - ``"env_clamped"`` — LLM_HEALTH_TIMEOUT was 0/negative → clamped to floor (0.1)
      - ``"default_invalid"`` — LLM_HEALTH_TIMEOUT not parseable → fell back to default

    Health probe timeout is **separate** from request timeout (--timeout) so
    that operators can give a short request budget without making the live
    health probe itself impossible. Default 5s preserves prior behavior.
    """
    env = env if env is not None else os.environ
    raw = env.get("LLM_HEALTH_TIMEOUT")
    if raw is None or raw.strip() == "":
        return (DEFAULT_HEALTH_TIMEOUT_S, "default")
    try:
        v = float(raw.strip())
    except (TypeError, ValueError):
        return (DEFAULT_HEALTH_TIMEOUT_S, "default_invalid")
    if v <= 0:
        return (_HEALTH_TIMEOUT_FLOOR, "env_clamped")
    return (v, "env")


# ---------------------------------------------------------------------------
# CASES file loader (datasets/human_review_cases.json)
# ---------------------------------------------------------------------------

DEFAULT_CASES_FILENAME = "human_review_cases.json"
SUPPORTED_CASES_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})


def load_cases_file(path: Path) -> list[tuple[str, str, str]]:
    """Load HR export cases from a JSON file.

    Accepted formats (decided by JSON top-level type)
    -------------------------------------------------

    **Wrapper form (canonical, REQUIRED for repo-default dataset)**::

        {
          "schema_version": "1.0",      # MUST be present and in SUPPORTED_CASES_SCHEMA_VERSIONS
          "cases": [
            {"domain": ..., "task": ..., "input": ...},
            ...
          ]
        }

    **Legacy compatibility forms (allowed for custom / ad-hoc files only)**::

        [{"domain": ..., "task": ..., "input": ...}, ...]
        [["builder", "requirement_parse", "..."], ...]

    Legacy bare-list / 3-tuple forms are still accepted for custom cases files
    (e.g. throwaway test fixtures) but they **cannot** carry a schema_version
    and therefore bypass version checking. The repo-default
    ``datasets/human_review_cases.json`` uses the wrapper form and is fully
    version-checked.

    Order is preserved exactly. The returned list is always
    ``list[tuple[str,str,str]]`` so it is a drop-in replacement for the
    legacy hard-coded ``CASES`` constant.

    Raises ``CasesFileError`` on:
      - file not found / unreadable
      - invalid JSON
      - top-level not list / dict
      - dict missing ``"cases"`` key
      - dict missing ``"schema_version"`` key (wrapper form)
      - dict ``schema_version`` not in ``SUPPORTED_CASES_SCHEMA_VERSIONS``
      - empty case list
      - any case missing required fields or wrong type
    """
    if not path.exists():
        raise CasesFileError(f"cases file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CasesFileError(f"cases file unreadable: {path}: {e}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CasesFileError(f"cases file is not valid JSON ({path}): {e}")

    if isinstance(data, dict):
        # Wrapper form — enforce schema_version.
        if "cases" not in data:
            raise CasesFileError(
                f"cases file dict must contain 'cases' key ({path}); "
                f"got keys: {sorted(data.keys())}"
            )
        if "schema_version" not in data:
            raise CasesFileError(
                f"cases file dict must contain 'schema_version' key ({path}); "
                f"supported versions: {sorted(SUPPORTED_CASES_SCHEMA_VERSIONS)}"
            )
        sv = data.get("schema_version")
        if not isinstance(sv, str) or sv not in SUPPORTED_CASES_SCHEMA_VERSIONS:
            raise CasesFileError(
                f"cases file schema_version={sv!r} is not supported ({path}); "
                f"supported versions: {sorted(SUPPORTED_CASES_SCHEMA_VERSIONS)}"
            )
        items = data["cases"]
    elif isinstance(data, list):
        items = data
    else:
        raise CasesFileError(
            f"cases file top-level must be list or dict ({path}); got {type(data).__name__}"
        )

    if not isinstance(items, list):
        raise CasesFileError(f"cases file 'cases' value must be a list ({path})")
    if len(items) == 0:
        raise CasesFileError(f"cases file is empty — refusing to run zero-case export ({path})")

    out: list[tuple[str, str, str]] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            d = item.get("domain")
            t = item.get("task")
            x = item.get("input")
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            d, t, x = item[0], item[1], item[2]
        else:
            raise CasesFileError(
                f"cases file entry [{i}] must be a dict with domain/task/input "
                f"or a 3-element list ({path}); got {type(item).__name__}"
            )
        if not (isinstance(d, str) and d) or not (isinstance(t, str) and t) or not (isinstance(x, str) and x):
            raise CasesFileError(
                f"cases file entry [{i}] missing/invalid domain/task/input ({path}); "
                f"got domain={d!r} task={t!r} input={x!r}"
            )
        out.append((d, t, x))

    return out


def peek_cases_schema_version(path: Path) -> Optional[str]:
    """Return the ``schema_version`` of a cases file if it's the wrapper form.

    For legacy bare-list / tuple-list files this returns ``None``. The call
    does not re-validate the whole file (use ``load_cases_file`` for that);
    it just reads the top-level ``schema_version`` if present so that the
    telemetry can surface it. Any read/parse error is swallowed and becomes
    ``None`` — the caller is expected to still call ``load_cases_file`` which
    will raise the real ``CasesFileError`` with a precise message.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        sv = data.get("schema_version")
        if isinstance(sv, str):
            return sv
    return None


# ---------------------------------------------------------------------------
# Max-retries normalization (CLI --max-retries → LLMClient.max_retries)
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 1


def normalize_max_retries(value: Optional[int]) -> int:
    """Normalize a CLI ``--max-retries`` value to a non-negative int.

    Rules:
      - ``None``    → DEFAULT_MAX_RETRIES (1)  — preserves prior behavior
      - negative   → clamped to 0  (consistent with "0 = no retry, 1 attempt only")
      - non-int    → DEFAULT_MAX_RETRIES (1)
      - 0          → 0   (no retries; failure on first error)
      - N (>0)     → N

    The integer returned here is passed directly to ``LLMClient(max_retries=N)``,
    which means LLMClient will perform at most ``1 + N`` attempts per case.
    """
    if value is None:
        return DEFAULT_MAX_RETRIES
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RETRIES
    if n < 0:
        return 0
    return n


# ---------------------------------------------------------------------------
# Mock output isolation — prevent mock runs from clobbering live artifacts
# ---------------------------------------------------------------------------

# Subdirectory under the live out_dir where every isolated mock run is written.
MOCK_RUNS_SUBDIR = "mock_runs"

# Timestamp format for individual mock run directories.
_MOCK_RUN_TS_FMT = "%Y%m%d_%H%M%S"


def _timestamp_dirname(now: Optional[datetime] = None) -> str:
    """Generate a compact filesystem-safe timestamp for a mock run directory."""
    now = now or datetime.now(UTC)
    return now.strftime(_MOCK_RUN_TS_FMT)


def _is_path_inside(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is equal to or lies below ``parent`` (resolved).

    Uses the "common path" trick rather than ``Path.is_relative_to`` to keep
    working on older Pythons and to handle Windows path cases robustly.
    """
    try:
        rc = child.resolve()
        rp = parent.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        rc.relative_to(rp)
        return True
    except ValueError:
        return False


@dataclass
class ResolvedOutDir:
    """Return value of resolve_mock_safe_out_dir.

    ``out_dir`` is the filesystem path that run_export should actually write
    under. ``source`` tags *why* that path was chosen so the run report can
    reproduce the decision afterwards.
    """
    out_dir: Path
    source: str   # "default_live" | "default_mock_isolated" | "user_override_mock_safe"


def resolve_mock_safe_out_dir(
    *,
    used_mock: bool,
    live_default_dir: Path,
    user_out_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> ResolvedOutDir:
    """Decide the output directory for a single export run.

    Rules
    -----
    - **live run** (``used_mock=False``):
        - ``user_out_dir`` → used as-is, tagged ``user_override_mock_safe``
          (live runs can reuse any directory the operator trusts; the whole
          point of this helper is to keep *mock* runs away from live data,
          not to over-constrain live runs).
        - no user override → ``live_default_dir``, tagged ``default_live``.

    - **mock run** (``used_mock=True``):
        - no user override → automatically routed to
          ``live_default_dir / MOCK_RUNS_SUBDIR / <timestamp>`` and tagged
          ``default_mock_isolated``. Guaranteed to be a fresh subdirectory
          every second, so mock data can never stomp on a live artifact.
        - user override → only accepted if the path is already "mock-safe",
          i.e. it lies **strictly under** the mock_runs subdirectory. Any
          other path (including ``live_default_dir`` itself, any other file
          under it, an unrelated directory that happens not to contain
          ``mock_runs`` in its name, etc.) → ``UnsafeMockOutputError``.

          This is the whole point: a mock run must never be able to write to
          the live review_data.json, no matter how the operator invoked it.

    Defense-in-depth: even if the caller ignored the returned path and wrote
    somewhere else, ``run_export`` calls this helper *and* re-runs the live
    path check before the first file is created.
    """
    live_default_dir = Path(live_default_dir)
    mock_root = live_default_dir / MOCK_RUNS_SUBDIR

    if not used_mock:
        # Live run — whatever the caller wants (or the live default).
        if user_out_dir is not None:
            return ResolvedOutDir(out_dir=Path(user_out_dir), source="user_override_mock_safe")
        return ResolvedOutDir(out_dir=live_default_dir, source="default_live")

    # Mock run.
    if user_out_dir is None:
        ts = _timestamp_dirname(now)
        return ResolvedOutDir(
            out_dir=mock_root / ts,
            source="default_mock_isolated",
        )

    # user supplied an explicit output dir for a mock run. Only accept it if
    # it lies strictly under the mock_runs subtree — otherwise hard-fail.
    user_path = Path(user_out_dir)

    # Forbid: exactly the live default dir.
    try:
        if user_path.resolve() == live_default_dir.resolve():
            raise UnsafeMockOutputError(
                f"mock run refuses to write to the live default directory "
                f"({live_default_dir}). Use --out-dir "
                f"{mock_root}/<timestamp> or omit --out-dir to auto-isolate."
            )
    except OSError:
        # resolve failures fall through to the general check below
        pass

    # Forbid: any path that is not under mock_runs/.
    if not _is_path_inside(user_path, mock_root):
        raise UnsafeMockOutputError(
            f"mock run refuses to write under {user_path}: path is not inside "
            f"the safe mock subtree {mock_root}. Use --out-dir {mock_root}/<name> "
            f"or omit --out-dir for auto-isolation."
        )

    return ResolvedOutDir(out_dir=user_path, source="user_override_mock_safe")


# ---------------------------------------------------------------------------
# Timeout override helper (CLI --timeout → real LLM call timeout)
# ---------------------------------------------------------------------------

_TIMEOUT_POLICY_FLOOR_S: float = 0.001  # 1ms — protect against zero/negative


def build_timeout_policy(timeout_s: Optional[float]) -> TimeoutPolicy:
    """CLI --timeout 값을 실제 ``TimeoutPolicy`` 로 변환 (float seconds).

    ``timeout_s`` is None → 기본 ``TimeoutSettings`` 그대로 (strict_json_s=120
    등 production-safe defaults). 기존 동작 100% 호환.

    ``timeout_s`` is a positive number (int or float) → 모든 pool
    (strict_json / fast_chat / long_context / embedding) 과 hard_kill 을 그
    값으로 강제. 이 정책이 ``Dispatcher(timeouts=...)`` 를 통해
    ``LLMClient.extract_slots(timeout_s=...)`` 경로까지 내려가고, 최종적으로
    ``VLLMHttpAdapter.generate(timeout_s=...)`` 의 ``urllib.urlopen(...,
    timeout=timeout_s)`` 까지 **float 그대로** 전달된다 (T-tranche 2026-04-07
    이전에는 int 로 절삭됐음).

    Notes
    -----
    - ``hard_kill_s`` 는 일관성 유지를 위해 같이 override.
    - 0 / 음수 입력은 ``_TIMEOUT_POLICY_FLOOR_S`` (1ms) 로 clamp — 테스트에서
      timeout 강제 시 사용.
    - **T-tranche** 가 명시적으로 정수 절삭 버그를 제거함. 이전에는
      ``int(timeout_s)`` 로 0.5 가 0 이 되어 floor=1 로 부풀려졌음. 이제는
      0.5 가 0.5 로 보존.
    """
    if timeout_s is None:
        return TimeoutPolicy()

    t = float(timeout_s)
    if t <= 0:
        t = _TIMEOUT_POLICY_FLOOR_S
    settings = TimeoutSettings(
        strict_json_s=t,
        fast_chat_s=t,
        long_context_s=t,
        embedding_s=t,
        hard_kill_s=max(t, _TIMEOUT_POLICY_FLOOR_S),
    )
    return TimeoutPolicy(settings)


# ---------------------------------------------------------------------------
# Phase 2 — Mock gating
# ---------------------------------------------------------------------------

def mock_allowed(*, cli_flag: bool = False, env: Optional[dict[str, str]] = None) -> bool:
    """Mock fallback 은 명시적 opt-in 만 허용.

    opt-in sources:
      - CLI flag ``--allow-mock`` (cli_flag=True)
      - env ``EXPORT_ALLOW_MOCK`` ∈ {"1", "true", "yes", "on"} (대소문자 무관)

    그 외에는 절대 False. 즉 기본은 *no mock*.
    """
    if cli_flag:
        return True
    env = env if env is not None else os.environ
    raw = (env.get("EXPORT_ALLOW_MOCK") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def require_live_or_explicit_mock(
    is_live_available: bool,
    *,
    allow_mock: bool,
    base_url: str,
) -> None:
    """live 가 안 잡혔는데 mock opt-in 도 없으면 LiveLLMUnavailableError 로 hard fail.

    이 함수가 정상 return 하면, 다음 중 하나가 보장됨:
      - is_live_available=True
      - allow_mock=True (사용자가 명시적으로 mock 을 원함)
    """
    if is_live_available:
        return
    if allow_mock:
        return
    raise LiveLLMUnavailableError(
        f"live LLM at {base_url} is not reachable, and mock fallback is "
        f"disabled by default. To run against a different server, set "
        f"--base-url or LLM_BASE_URL. To deliberately use the mock adapter, "
        f"opt in with --allow-mock or EXPORT_ALLOW_MOCK=1."
    )


# ---------------------------------------------------------------------------
# Phase 3 — Telemetry
# ---------------------------------------------------------------------------

@dataclass
class CountingAdapter:
    """LLM adapter 위에 씌우는 비침습적 카운팅 wrapper.

    .generate() 호출 횟수와 raise 한 exception 횟수를 case 단위로 추적한다.
    LLMClient 는 wrapping 되었는지 모르고, generate / is_available signature 를
    그대로 전달한다.

    사용법
    ------
        wrapped = CountingAdapter(real_adapter)
        for case in cases:
            wrapped.reset_case()
            ...dispatch...
            telem = wrapped.snapshot()  # CallCounters
    """
    inner: Any
    _generate_calls: int = 0
    _generate_exceptions: int = 0

    @property
    def provider_name(self) -> str:
        return f"counting:{getattr(self.inner, 'provider_name', 'unknown')}"

    def reset_case(self) -> None:
        self._generate_calls = 0
        self._generate_exceptions = 0

    def snapshot(self) -> "CallCounters":
        return CallCounters(
            generate_calls=self._generate_calls,
            generate_exceptions=self._generate_exceptions,
        )

    def is_available(self) -> bool:
        return self.inner.is_available()

    def generate(self, *args, **kwargs):
        self._generate_calls += 1
        try:
            return self.inner.generate(*args, **kwargs)
        except Exception:
            self._generate_exceptions += 1
            raise


@dataclass
class CallCounters:
    """단일 case 에 대한 raw counter snapshot."""
    generate_calls: int = 0
    generate_exceptions: int = 0

    @property
    def attempt_count(self) -> int:
        return self.generate_calls

    @property
    def transport_retry_count(self) -> int:
        # 모든 exception 을 transport retry 로 본다 (LLMClient 는 transport
        # 실패와 parse 실패를 둘 다 retry 하지만 parse 실패는 exception 이 아님)
        return self.generate_exceptions

    @property
    def parse_retry_count(self) -> int:
        # generate 호출이 N 번 일어났고 그 중 transport_failures 가 K 면,
        # 정상 응답이 (N - K) 번. 그 중 마지막 한 번만 성공이고 나머지는 parse
        # 단계에서 failed → retry 였을 가능성. 따라서 (N - K - 1) 을 parse retry
        # 라고 본다. 하한 0.
        return max(0, self.generate_calls - self.generate_exceptions - 1)


@dataclass
class CaseTelemetry:
    """단일 export case 의 실행 telemetry."""
    case_id: str
    domain: str
    task: str
    tool_name: Optional[str] = None
    start_ts: str = ""
    end_ts: str = ""
    latency_ms: int = 0
    attempt_count: int = 1
    transport_retry_count: int = 0
    parse_retry_count: int = 0
    final_status: str = "unknown"          # success | failed
    auto_validated: bool = False
    final_judgment: Optional[str] = None
    severity: Optional[str] = None
    failure_categories: list[str] = field(default_factory=list)
    used_mock: bool = False
    resolved_base_url: str = ""
    resolved_model: str = ""
    # ---- additive — T-tranche 2026-04-07 ---------------------------------
    attempts_used: int = 0                              # LLMClient.RetryDecision.attempts_used
    budget_exhausted: bool = False                      # RetryDecision.budget_exhausted
    total_elapsed_ms: int = 0                           # RetryDecision.total_elapsed_ms
    effective_request_timeout_s: Optional[float] = None # RetryDecision.effective_request_timeout_s
    retry_decision_reason: Optional[str] = None         # RetryDecision.retry_decision_reason
    health_failure_reason: Optional[str] = None         # adapter.HealthProbeResult.reason if non-OK
    # ---- additive — T-tranche 2 (2026-04-08, cooldown externalization) ---
    configured_transport_retry_cooldown_s: Optional[float] = None
    transport_retry_cooldown_source: Optional[str] = None
    cooldown_decisions: list[dict] = field(default_factory=list)  # WaitDecision dicts
    total_cooldown_ms: int = 0                          # sum of applied_s × 1000 across decisions
    cooldown_clamped: bool = False                      # any decision clamped or skipped due to budget
    cooldown_skip_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _percentile(values: list[int], p: float) -> int:
    """간단한 nearest-rank percentile (0~100). values 비어 있으면 0."""
    if not values:
        return 0
    s = sorted(values)
    if p <= 0:
        return s[0]
    if p >= 100:
        return s[-1]
    # nearest-rank
    k = max(1, math.ceil(p / 100.0 * len(s)))
    return s[k - 1]


@dataclass
class RunSummary:
    total_cases: int = 0
    succeeded_cases: int = 0
    failed_cases: int = 0
    pass_count: int = 0
    fail_count: int = 0
    needs_review_count: int = 0
    p50_latency_ms: int = 0
    p95_latency_ms: int = 0
    max_latency_ms: int = 0
    total_transport_retries: int = 0
    total_parse_retries: int = 0
    over_10s_count: int = 0
    over_30s_count: int = 0
    over_60s_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunTelemetry:
    """전체 export run telemetry.

    NOTE 2026-04-07 (additive only — schema not broken):
    Operational metadata fields are added at the end so existing JSON readers
    keep working. Old fields are unchanged.
    """
    started_at: str
    finished_at: str = ""
    used_mock: bool = False
    resolved_base_url: str = ""
    resolved_base_url_source: str = ""
    resolved_model: str = ""
    cases: list[CaseTelemetry] = field(default_factory=list)
    summary: RunSummary = field(default_factory=RunSummary)
    # ---- additive operational metadata (2026-04-07) -----------------------
    request_timeout_s: Optional[int] = None        # CLI --timeout (None=default)
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S
    health_timeout_source: str = "default"          # parse_health_timeout source
    max_retries: int = DEFAULT_MAX_RETRIES
    cases_file_path: Optional[str] = None           # absolute path of cases file used
    cases_count: int = 0
    # ---- additive operational metadata (2026-04-07 follow-up) -------------
    output_dir: Optional[str] = None                # absolute path used for review_data.json
    output_dir_source: str = "default_live"         # default_live | default_mock_isolated | user_override_mock_safe
    cases_schema_version: Optional[str] = None      # schema_version string from wrapper form (or None for legacy)
    # ---- additive operational metadata (T-tranche 2026-04-07) -------------
    effective_request_timeout_s: Optional[float] = None  # what dispatcher actually passed to extract_slots
    effective_health_timeout_s: Optional[float] = None   # what the adapter actually used for /health
    effective_total_deadline_s: Optional[float] = None   # UnifiedTimeoutPolicy.effective_total_deadline_s()
    request_timeout_source: str = "default"              # default | cli | env | settings | test
    total_attempts_used: int = 0                         # sum across all cases
    total_budget_exhausted: int = 0                      # how many cases exited via budget exhaustion
    health_failure_reason: Optional[str] = None          # most recent classified non-OK health probe reason
    # ---- additive — T-tranche 2 (2026-04-08, cooldown externalization) ---
    configured_transport_retry_cooldown_s: Optional[float] = None
    configured_scheduler_cooldown_heavy_s: Optional[float] = None
    configured_scheduler_cooldown_light_s: Optional[float] = None
    configured_fallback_retry_delay_s: Optional[float] = None
    cooldown_source: str = "default"                     # single source string for the cooldown sub-section
    total_cooldown_ms: int = 0                           # sum of applied cooldowns across all cases
    total_clamped_cooldowns: int = 0                     # how many cases had any clamped/skipped cooldown
    policy_source_summary: Optional[dict] = None         # one-shot snapshot of all sources for explainability

    def add_case(self, c: CaseTelemetry) -> None:
        self.cases.append(c)

    def finalize(self) -> None:
        """summary 집계."""
        self.finished_at = datetime.now(UTC).isoformat()
        latencies = [c.latency_ms for c in self.cases]
        s = self.summary
        s.total_cases = len(self.cases)
        s.succeeded_cases = sum(1 for c in self.cases if c.final_status == "success")
        s.failed_cases = sum(1 for c in self.cases if c.final_status == "failed")
        s.pass_count = sum(1 for c in self.cases if c.final_judgment == "pass")
        s.fail_count = sum(1 for c in self.cases if c.final_judgment == "fail")
        s.needs_review_count = sum(1 for c in self.cases if c.final_judgment == "needs_review")
        s.p50_latency_ms = _percentile(latencies, 50)
        s.p95_latency_ms = _percentile(latencies, 95)
        s.max_latency_ms = max(latencies, default=0)
        s.total_transport_retries = sum(c.transport_retry_count for c in self.cases)
        s.total_parse_retries = sum(c.parse_retry_count for c in self.cases)
        s.over_10s_count = sum(1 for l in latencies if l >= 10_000)
        s.over_30s_count = sum(1 for l in latencies if l >= 30_000)
        s.over_60s_count = sum(1 for l in latencies if l >= 60_000)
        # T-tranche additive aggregates
        self.total_attempts_used = sum(c.attempts_used for c in self.cases)
        self.total_budget_exhausted = sum(1 for c in self.cases if c.budget_exhausted)
        # last non-OK health failure reason wins (rolling)
        for c in self.cases:
            if c.health_failure_reason:
                self.health_failure_reason = c.health_failure_reason
        # T-tranche 2 aggregates: cooldown
        self.total_cooldown_ms = sum(int(c.total_cooldown_ms or 0) for c in self.cases)
        self.total_clamped_cooldowns = sum(1 for c in self.cases if c.cooldown_clamped)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "used_mock": self.used_mock,
            "resolved_base_url": self.resolved_base_url,
            "resolved_base_url_source": self.resolved_base_url_source,
            "resolved_model": self.resolved_model,
            "summary": self.summary.to_dict(),
            "cases": [c.to_dict() for c in self.cases],
            # additive operational metadata (2026-04-07)
            "request_timeout_s": self.request_timeout_s,
            "health_timeout_s": self.health_timeout_s,
            "health_timeout_source": self.health_timeout_source,
            "max_retries": self.max_retries,
            "cases_file_path": self.cases_file_path,
            "cases_count": self.cases_count,
            # additive operational metadata (2026-04-07 follow-up)
            "output_dir": self.output_dir,
            "output_dir_source": self.output_dir_source,
            "cases_schema_version": self.cases_schema_version,
            # additive operational metadata (T-tranche 2026-04-07)
            "effective_request_timeout_s": self.effective_request_timeout_s,
            "effective_health_timeout_s": self.effective_health_timeout_s,
            "effective_total_deadline_s": self.effective_total_deadline_s,
            "request_timeout_source": self.request_timeout_source,
            "total_attempts_used": self.total_attempts_used,
            "total_budget_exhausted": self.total_budget_exhausted,
            "health_failure_reason": self.health_failure_reason,
            # additive operational metadata (T-tranche 2 2026-04-08)
            "configured_transport_retry_cooldown_s": self.configured_transport_retry_cooldown_s,
            "configured_scheduler_cooldown_heavy_s": self.configured_scheduler_cooldown_heavy_s,
            "configured_scheduler_cooldown_light_s": self.configured_scheduler_cooldown_light_s,
            "configured_fallback_retry_delay_s": self.configured_fallback_retry_delay_s,
            "cooldown_source": self.cooldown_source,
            "total_cooldown_ms": self.total_cooldown_ms,
            "total_clamped_cooldowns": self.total_clamped_cooldowns,
            "policy_source_summary": self.policy_source_summary,
        }


def write_run_report(report: RunTelemetry, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Helper for the export script: build a single CaseTelemetry from a TaskResult
# ---------------------------------------------------------------------------

def case_telemetry_from_result(
    *,
    case_id: str,
    domain: str,
    task: str,
    start_ts: float,
    end_ts: float,
    counters: CallCounters,
    task_result_dict: dict,
    used_mock: bool,
    resolved_base_url: str,
    resolved_model: str,
) -> CaseTelemetry:
    """dispatcher 가 만든 TaskResult.to_dict() + counter snapshot → CaseTelemetry.

    T-tranche 2026-04-07: 새 ``retry_decision`` 과 ``health_probe_result`` dict
    가 task_result_dict 에 있으면 각 필드를 분해해서 telemetry 에 carry.
    """
    layered = task_result_dict.get("layered_judgment") or {}
    auto_validated = bool(layered.get("auto_validated", False))
    final_judgment = layered.get("final_judgment")
    severity = layered.get("severity")
    failure_categories = list(layered.get("failure_categories") or [])

    status = task_result_dict.get("status", "unknown")
    # success: dispatcher 가 done 으로 끝났고 schema 단계가 통과한 경우
    final_status = "success" if status == "done" else "failed"

    # T-tranche additive — pull retry_decision / health_probe_result if present
    rd = task_result_dict.get("retry_decision") or {}
    hp = task_result_dict.get("health_probe_result") or {}
    health_failure_reason = None
    if hp and not hp.get("available", True):
        health_failure_reason = hp.get("reason")

    # T-tranche 2: cooldown decisions list (LLMClient transport retry cooldowns
    # + scheduler cooldown, merged by dispatcher) and aggregates.
    cooldown_decisions = list(rd.get("cooldown_decisions") or [])
    total_cooldown_ms = int(rd.get("total_cooldown_ms", 0) or 0)
    cooldown_clamped = any(
        bool(d.get("clamped")) or bool(d.get("skipped"))
        for d in cooldown_decisions
    )
    cooldown_skip_reasons = [
        d.get("skip_reason") for d in cooldown_decisions
        if d.get("skip_reason")
    ]

    return CaseTelemetry(
        case_id=case_id,
        domain=domain,
        task=task,
        tool_name=f"{domain}.{task}",
        start_ts=datetime.fromtimestamp(start_ts, UTC).isoformat(),
        end_ts=datetime.fromtimestamp(end_ts, UTC).isoformat(),
        latency_ms=int(task_result_dict.get("latency_ms", 0)),
        attempt_count=counters.attempt_count,
        transport_retry_count=counters.transport_retry_count,
        parse_retry_count=counters.parse_retry_count,
        final_status=final_status,
        auto_validated=auto_validated,
        final_judgment=final_judgment,
        severity=severity,
        failure_categories=failure_categories,
        used_mock=used_mock,
        resolved_base_url=resolved_base_url,
        resolved_model=resolved_model,
        # T-tranche additive
        attempts_used=int(rd.get("attempts_used", counters.attempt_count) or 0),
        budget_exhausted=bool(rd.get("budget_exhausted", False)),
        total_elapsed_ms=int(rd.get("total_elapsed_ms", 0) or 0),
        effective_request_timeout_s=rd.get("effective_request_timeout_s"),
        retry_decision_reason=rd.get("retry_decision_reason"),
        health_failure_reason=health_failure_reason,
        # T-tranche 2 additive (cooldown)
        configured_transport_retry_cooldown_s=rd.get("transport_retry_cooldown_s"),
        transport_retry_cooldown_source=rd.get("transport_retry_cooldown_source"),
        cooldown_decisions=cooldown_decisions,
        total_cooldown_ms=total_cooldown_ms,
        cooldown_clamped=cooldown_clamped,
        cooldown_skip_reasons=cooldown_skip_reasons,
    )
