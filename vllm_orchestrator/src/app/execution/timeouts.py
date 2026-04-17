"""
timeouts.py — 통합 timeout 정책 (Unified Timeout Policy)

배경
----
2026-04-07 (T-tranche) 이전:
  - request timeout, health probe timeout, retry budget 이 서로 다른 모듈에
    흩어져 있고, 일부는 magic number (예: ``vllm_http.py`` 의 5초) 였음.
  - retry 는 ``max_retries`` count 에만 묶여 있어서 짧은 timeout + retry
    조합이 사용자 기대를 한참 넘는 wall-clock 으로 이어질 수 있었음.

이 모듈의 책임
-------------
1. **단일 source-of-truth** 으로 전체 timeout/retry 정책을 노출한다.
2. ``DEFAULT_HEALTH_TIMEOUT_S`` 같은 default 상수의 *유일한 정의처* 가 된다.
   다른 모듈은 여기서 import 만 한다.
3. ``TimeoutPolicy`` 는 float 초 단위로 동작한다 (이전엔 int 였음).
4. ``UnifiedTimeoutPolicy`` 는 위 둘에 더해 retry budget / total deadline
   까지 묶어서 carry 한다. ``LLMClient`` 가 이 policy 를 직접 받아 쓰면 정책
   판단 분기가 모듈 안에 들어오지 않는다.

Invariants
----------
- health probe timeout 은 unified timeout policy 에서 유도된다 (≠ adapter 안 magic number).
- retry 는 ``max_retries`` 만이 아니라 total deadline budget 에도 구속된다.
- timeout/retry exhaustion 은 항상 분류된 reason 으로 telemetry 에 노출된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from ..settings import TimeoutSettings


# ---------------------------------------------------------------------------
# Single source of truth — default constants
# ---------------------------------------------------------------------------

DEFAULT_HEALTH_TIMEOUT_S: float = 5.0
DEFAULT_HEALTH_TIMEOUT_FLOOR_S: float = 0.1
DEFAULT_REQUEST_TIMEOUT_S: float = 120.0
DEFAULT_MAX_RETRIES: int = 1

# Cooldown / retry-delay / fallback-wait defaults (T-tranche-2 2026-04-08).
# Single source of truth for every cooldown-class wait in the runtime.
DEFAULT_TRANSPORT_RETRY_COOLDOWN_S: float = 2.0
DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S: float = 2.0
DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S: float = 0.5
DEFAULT_FALLBACK_RETRY_DELAY_S: float = 2.0


# ---------------------------------------------------------------------------
# WaitDecision — single record describing one cooldown / sleep slot
# ---------------------------------------------------------------------------

# Normalized wait kinds (lower_snake). Operators / telemetry should treat
# these as enum-like.
WAIT_KIND_TRANSPORT_RETRY = "transport_retry"
WAIT_KIND_SCHEDULER_HEAVY = "scheduler_heavy"
WAIT_KIND_SCHEDULER_LIGHT = "scheduler_light"
WAIT_KIND_FALLBACK_RETRY  = "fallback_retry"

# Normalized skip / clamp reasons.
WAIT_SKIP_REASON_NONE                = ""
WAIT_SKIP_REASON_ZERO_CONFIGURED     = "zero_configured"
WAIT_SKIP_REASON_NO_DEADLINE         = "no_deadline"
WAIT_SKIP_REASON_BUDGET_EXHAUSTED    = "budget_exhausted"
WAIT_SKIP_REASON_CLAMPED_TO_BUDGET   = "clamped_to_budget"
WAIT_SKIP_REASON_ALREADY_ELAPSED     = "already_elapsed"


@dataclass
class WaitDecision:
    """Single cooldown/sleep slot — captures *configured* vs *applied* values.

    Every cooldown / retry-delay / scheduler cooloff produces one
    ``WaitDecision``. The aggregate (sum of ``applied_s`` across decisions)
    is what runs as actual wall-clock sleep. The aggregate of ``configured_s``
    minus ``applied_s`` is what the budget guard saved.

    Fields
    ------
    kind          : "transport_retry" | "scheduler_heavy" | "scheduler_light" | "fallback_retry"
    configured_s  : the value the policy *wanted* to wait (float seconds)
    applied_s     : what was actually slept (after clamp/skip; float seconds)
    clamped       : True if applied_s < configured_s but applied_s > 0
    skipped       : True if applied_s == 0 and configured_s > 0 (the wait was *not* honored)
    skip_reason   : "" | one of WAIT_SKIP_REASON_*
    source        : where the configured value came from ("default" | "policy" | "constructor_default")
    """
    kind: str
    configured_s: float
    applied_s: float
    clamped: bool = False
    skipped: bool = False
    skip_reason: str = WAIT_SKIP_REASON_NONE
    source: str = "default"

    def to_dict(self) -> dict:
        return asdict(self)


def clamp_wait_to_budget(
    *,
    kind: str,
    configured_s: float,
    total_deadline_s: Optional[float],
    elapsed_s: float,
    headroom_s: float = 0.0,
    source: str = "default",
) -> WaitDecision:
    """Decide how much of ``configured_s`` to actually sleep, given the budget.

    Rules
    -----
    - ``configured_s <= 0`` → skipped, reason ``zero_configured``.
    - ``total_deadline_s is None`` → no budget enforcement; ``applied_s = configured_s``,
      reason ``no_deadline`` is set as a *source* tag (not a skip — applied_s
      remains the configured value). The caller can read ``source`` /
      ``skip_reason`` to distinguish "no deadline → use full configured"
      from "deadline applied → fully honored".
    - ``available = total_deadline_s - elapsed_s - headroom_s``:
        - ``available <= 0`` → skipped, reason ``budget_exhausted``.
        - ``available < configured_s`` → clamped, ``applied_s = available``,
          reason ``clamped_to_budget``.
        - else → fully honored, ``applied_s = configured_s``.

    Notes
    -----
    - ``headroom_s`` is the amount of budget that **must** remain after the
      wait — typically the upcoming attempt's ``request_timeout_s``. This is
      how the helper enforces "leave at least one full attempt window".
    - The function is pure: it does no sleeping. The caller is responsible
      for ``time.sleep(decision.applied_s)``.
    """
    if configured_s <= 0:
        return WaitDecision(
            kind=kind,
            configured_s=float(configured_s),
            applied_s=0.0,
            skipped=True,
            skip_reason=WAIT_SKIP_REASON_ZERO_CONFIGURED,
            source=source,
        )
    if total_deadline_s is None:
        return WaitDecision(
            kind=kind,
            configured_s=float(configured_s),
            applied_s=float(configured_s),
            source=source,
        )
    available = total_deadline_s - elapsed_s - headroom_s
    if available <= 0:
        return WaitDecision(
            kind=kind,
            configured_s=float(configured_s),
            applied_s=0.0,
            skipped=True,
            skip_reason=WAIT_SKIP_REASON_BUDGET_EXHAUSTED,
            source=source,
        )
    if available < configured_s:
        return WaitDecision(
            kind=kind,
            configured_s=float(configured_s),
            applied_s=float(available),
            clamped=True,
            skip_reason=WAIT_SKIP_REASON_CLAMPED_TO_BUDGET,
            source=source,
        )
    return WaitDecision(
        kind=kind,
        configured_s=float(configured_s),
        applied_s=float(configured_s),
        source=source,
    )


# ---------------------------------------------------------------------------
# TimeoutPolicy — pool-keyed timeouts (float seconds)
# ---------------------------------------------------------------------------

class TimeoutPolicy:
    """Pool-keyed request timeouts in **float seconds**.

    Backward compatible: existing callers that pass ``int`` settings still
    work because Python widens int → float automatically. Existing callers
    that compare returned values with ``int`` literals also still work.
    """

    def __init__(self, settings: Optional[TimeoutSettings] = None):
        s = settings or TimeoutSettings()
        self._map: dict[str, float] = {
            "strict_json":  float(s.strict_json_s),
            "fast_chat":    float(s.fast_chat_s),
            "long_context": float(s.long_context_s),
            "creative_json": float(getattr(s, "creative_json_s", 45.0)),
            "embedding":    float(s.embedding_s),
        }
        self._hard: float = float(s.hard_kill_s)

    def get_timeout(self, pool_type: str) -> float:
        """풀 유형별 timeout (float seconds). 모르는 풀이면 strict_json 으로 fallback."""
        return self._map.get(pool_type, self._map["strict_json"])

    @property
    def hard_timeout(self) -> float:
        return self._hard


# ---------------------------------------------------------------------------
# UnifiedTimeoutPolicy — request + health + retry budget in one structure
# ---------------------------------------------------------------------------

@dataclass
class UnifiedTimeoutPolicy:
    """단일 정책 객체로 request / health / retry / cooldown 을 모두 carry.

    Fields (request / health / retry — T-tranche 1)
    ------------------------------------------------
    request_timeout_s        : float — 단일 LLM 호출 timeout (urllib timeout 까지 전달)
    request_timeout_source   : str   — "default" | "cli" | "env" | "settings" | "test"
    health_timeout_s         : float — health probe timeout (request 와 분리)
    health_timeout_source    : str   — "default" | "env" | "env_clamped" | "default_invalid"
    max_retries              : int   — LLMClient 가 사용할 retry 횟수 budget
    total_deadline_s         : float | None — 명시적 wall-clock budget. None 이면
                                              아래 ``effective_total_deadline_s`` 가
                                              ``(1 + max_retries) * request_timeout_s`` 로 도출.
    pool_type                : str   — request timeout 이 적용되는 풀 (default "strict_json")

    Fields (cooldown sub-section — T-tranche 2 2026-04-08)
    -------------------------------------------------------
    transport_retry_cooldown_s : float — LLMClient transport-fail 후 retry 전 sleep
    scheduler_cooldown_heavy_s : float — Scheduler heavy task 사이 sleep
    scheduler_cooldown_light_s : float — Scheduler light task 사이 sleep
    fallback_retry_delay_s     : float — DegradedModeHandler retry/cooldown 단위
    cooldown_source            : str   — 위 4개의 출처 ("default" | "settings" | "env" | "test")

    Invariants
    ----------
    - request_timeout_s, health_timeout_s, cooldown 4종 모두 양의 float (생성 시 clamp).
    - max_retries 는 ≥ 0.
    - effective_total_deadline_s() 는 항상 ≥ request_timeout_s — retry 를 0회로
      줄이더라도 최소 한 번의 attempt 는 보장한다.
    - **cooldown 값은 함수 안 magic number 가 아니라 이 정책에서 유도된다.**
    - **cooldown / retry / fallback wait 는 모두 total_deadline_s 안쪽으로
      ``clamp_wait_to_budget`` 을 거쳐 적용된다 — 정책 자체는 stateless 하며
      runtime 에서 elapsed/headroom 을 받아 ``WaitDecision`` 으로 분해된다.**
    """
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    request_timeout_source: str = "default"
    health_timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S
    health_timeout_source: str = "default"
    max_retries: int = DEFAULT_MAX_RETRIES
    total_deadline_s: Optional[float] = None
    pool_type: str = "strict_json"
    # Cooldown sub-section (T-tranche 2)
    transport_retry_cooldown_s: float = DEFAULT_TRANSPORT_RETRY_COOLDOWN_S
    scheduler_cooldown_heavy_s: float = DEFAULT_SCHEDULER_COOLDOWN_HEAVY_S
    scheduler_cooldown_light_s: float = DEFAULT_SCHEDULER_COOLDOWN_LIGHT_S
    fallback_retry_delay_s: float = DEFAULT_FALLBACK_RETRY_DELAY_S
    cooldown_source: str = "default"

    def __post_init__(self) -> None:
        # clamp into safe positive ranges
        if self.request_timeout_s is None or self.request_timeout_s <= 0:
            self.request_timeout_s = DEFAULT_REQUEST_TIMEOUT_S
        else:
            self.request_timeout_s = float(self.request_timeout_s)
        if self.health_timeout_s is None or self.health_timeout_s <= 0:
            self.health_timeout_s = DEFAULT_HEALTH_TIMEOUT_FLOOR_S
        else:
            self.health_timeout_s = float(self.health_timeout_s)
        if self.max_retries is None or self.max_retries < 0:
            self.max_retries = 0
        # Clamp cooldown values: 0 OK (means "no wait"), negative → 0
        if self.transport_retry_cooldown_s is None or self.transport_retry_cooldown_s < 0:
            self.transport_retry_cooldown_s = 0.0
        else:
            self.transport_retry_cooldown_s = float(self.transport_retry_cooldown_s)
        if self.scheduler_cooldown_heavy_s is None or self.scheduler_cooldown_heavy_s < 0:
            self.scheduler_cooldown_heavy_s = 0.0
        else:
            self.scheduler_cooldown_heavy_s = float(self.scheduler_cooldown_heavy_s)
        if self.scheduler_cooldown_light_s is None or self.scheduler_cooldown_light_s < 0:
            self.scheduler_cooldown_light_s = 0.0
        else:
            self.scheduler_cooldown_light_s = float(self.scheduler_cooldown_light_s)
        if self.fallback_retry_delay_s is None or self.fallback_retry_delay_s < 0:
            self.fallback_retry_delay_s = 0.0
        else:
            self.fallback_retry_delay_s = float(self.fallback_retry_delay_s)

    def effective_total_deadline_s(self) -> float:
        """Wall-clock budget for *all* attempts of a single case combined.

        If the caller didn't specify ``total_deadline_s``, derive it as
        ``(1 + max_retries) * request_timeout_s`` so that a single attempt is
        always allowed even when retries are 0. The returned value is at least
        ``request_timeout_s``.
        """
        if self.total_deadline_s is not None and self.total_deadline_s > 0:
            return max(float(self.total_deadline_s), self.request_timeout_s)
        return float((1 + max(0, self.max_retries)) * self.request_timeout_s)

    def to_legacy_timeout_policy(self) -> TimeoutPolicy:
        """Build a pool-keyed ``TimeoutPolicy`` that the legacy ``Dispatcher``
        can consume. Every pool gets the same per-call request timeout."""
        t = self.request_timeout_s
        s = TimeoutSettings(
            strict_json_s=t,
            fast_chat_s=t,
            long_context_s=t,
            embedding_s=t,
            hard_kill_s=max(t, 1.0),
        )
        return TimeoutPolicy(s)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["effective_total_deadline_s"] = self.effective_total_deadline_s()
        return d
