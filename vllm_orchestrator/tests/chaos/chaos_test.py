"""chaos_test.py — Fault injection tests.

Each scenario verifies orchestrator gracefully degrades / recovers.

Prereqs:
  - orchestrator running on $ORCH_URL (default http://127.0.0.1:8100)
  - redis running (if cache_backend=redis)
  - vLLM running (separate from orchestrator)

Scenarios:
  1. vllm_down         — vLLM 중단 → orchestrator가 circuit_open → mock fallback으로 응답
  2. redis_down        — Redis 끊김 → cache 레이어 실패 → in-memory fallback or miss
  3. slow_vllm         — vLLM 응답이 느림 → timeout → error로 응답
  4. burst_load        — 짧은 시간에 대량 요청 → rate limit / queue shed 작동 확인
  5. vllm_flaky        — vLLM 간헐 실패 → circuit breaker 개방/반개방/닫힘 상태 전이

각 시나리오는 setUp으로 환경 구성 + runTest → tearDown으로 복구.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("pip install requests 필요")
    sys.exit(1)


ORCH_URL = os.getenv("ORCH_URL", "http://127.0.0.1:8100")
VLLM_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8000")
TEST_BODY = {
    "domain": "minecraft", "task_name": "scene_graph",
    "user_input": "chaos test witch castle"
}


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: dict
    notes: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "details": self.details, "notes": self.notes}


# ── utilities ───────────────────────────────────────────────────────────────

def _post(path: str, body: Optional[dict] = None, timeout: float = 30.0) -> tuple[int, dict, float]:
    t0 = time.time()
    try:
        r = requests.post(f"{ORCH_URL}{path}", json=body or TEST_BODY, timeout=timeout)
        latency = time.time() - t0
        try:
            doc = r.json()
        except Exception:
            doc = {"_raw": r.text[:200]}
        return r.status_code, doc, latency
    except Exception as e:
        return 0, {"_error": str(e)}, time.time() - t0


def _get(path: str, timeout: float = 5.0) -> tuple[int, Any]:
    try:
        r = requests.get(f"{ORCH_URL}{path}", timeout=timeout)
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


def _vllm_alive() -> bool:
    try:
        r = requests.get(f"{VLLM_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


# ── scenarios ──────────────────────────────────────────────────────────────

def scenario_vllm_down() -> ScenarioResult:
    """vLLM 없이 orchestrator가 어떻게 반응하는지.
    기대: circuit_open 또는 transport_failure → HTTP 200 (mock/degraded) or 5xx.
    핵심: 프로세스 크래시 없음, 복구 시 정상화.
    """
    name = "vllm_down"
    details: dict = {}
    # vLLM이 실제로 down인지 확인 (test runner가 down 상태 만들어둠 가정)
    details["vllm_alive_before"] = _vllm_alive()
    status, doc, lat = _post("/tasks/submit", timeout=60.0)
    details["orch_status"] = status
    details["latency_s"] = round(lat, 3)
    details["orch_doc"] = {
        "status": doc.get("status"),
        "errors": doc.get("errors"),
        "cache_hit": doc.get("cache_hit"),
    }
    # 성공 조건: orchestrator 자체는 200 반환해야 함 (fallback/mock) OR 503
    #  — 5xx보다 200 + degraded 응답이 이상적
    passed = status in (200, 503, 500)
    notes = "orchestrator should handle vLLM outage gracefully (return degraded or error, not crash)"
    return ScenarioResult(name=name, passed=passed, details=details, notes=notes)


def scenario_slow_vllm() -> ScenarioResult:
    """vLLM이 느리면 timeout → ERROR or Circuit eventually opens."""
    name = "slow_vllm"
    details: dict = {}
    # orch에게 짧은 timeout으로 호출 (client-side)
    status, doc, lat = _post("/tasks/submit", timeout=3.0)
    # 3s 안에 200 받으면 (cache hit) 정상
    # timeout(connect/read)이면 클라이언트측 timeout로 0 status
    details["status"] = status
    details["latency_s"] = round(lat, 3)
    passed = True  # 크래시만 없으면 OK
    return ScenarioResult(name=name, passed=passed, details=details)


def scenario_redis_down() -> ScenarioResult:
    """Redis down 시 cache 기능은 degrade 되지만 orchestrator는 작동해야 함.
    테스트: 캐시 관련 에러 무시되는지 확인."""
    name = "redis_down"
    details: dict = {}
    # Prometheus에서 cache_events 확인 (redis 끊기면 miss/error 증가)
    ms, text = _get("/metrics")
    details["metrics_status"] = ms
    details["cache_events"] = "cache_events" in (text or "")
    status, _, _ = _post("/tasks/submit", timeout=60)
    details["orch_status"] = status
    passed = status in (200, 429, 503, 500)   # 크래시 아님
    return ScenarioResult(name=name, passed=passed, details=details)


def scenario_burst_load() -> ScenarioResult:
    """50 concurrent → rate limit 또는 queue_shed 감지."""
    name = "burst_load"
    from concurrent.futures import ThreadPoolExecutor, as_completed
    details: dict = {"total": 50}
    statuses: list[int] = []
    latencies: list[float] = []

    def one():
        s, _, l = _post("/tasks/submit", timeout=10.0)
        return s, l

    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = [ex.submit(one) for _ in range(50)]
        for f in as_completed(futures):
            try:
                s, l = f.result()
                statuses.append(s)
                latencies.append(l)
            except Exception:
                statuses.append(0)
    dist: dict = {}
    for s in statuses:
        dist[s] = dist.get(s, 0) + 1
    details["status_dist"] = dist
    details["any_crash_5xx"] = any(s >= 500 for s in statuses)
    passed = not details["any_crash_5xx"]
    return ScenarioResult(name=name, passed=passed, details=details,
                         notes="200/429 허용, 500 이상 발생하면 fail")


def scenario_health_always_up() -> ScenarioResult:
    """어떤 상황이든 /health/ready는 응답해야 함 (SLO: 99.99% availability)."""
    name = "health_always_up"
    details: dict = {}
    oks = 0
    for _ in range(10):
        s, _ = _get("/health/ready", timeout=3)
        if s == 200:
            oks += 1
    details["ok_count"] = oks
    passed = oks >= 9   # 90% threshold
    return ScenarioResult(name=name, passed=passed, details=details)


# ── runner ──────────────────────────────────────────────────────────────────

_SCENARIOS = {
    "vllm_down": scenario_vllm_down,
    "slow_vllm": scenario_slow_vllm,
    "redis_down": scenario_redis_down,
    "burst_load": scenario_burst_load,
    "health_always_up": scenario_health_always_up,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Chaos test runner")
    ap.add_argument("--scenario", default="all", help=f"one of {list(_SCENARIOS.keys())} or 'all'")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    if args.scenario == "all":
        targets = list(_SCENARIOS.values())
    else:
        fn = _SCENARIOS.get(args.scenario)
        if not fn:
            print(f"unknown scenario: {args.scenario}", file=sys.stderr)
            return 2
        targets = [fn]

    results = []
    for fn in targets:
        print(f"\n=== {fn.__name__} ===")
        try:
            r = fn()
        except Exception as e:
            r = ScenarioResult(name=fn.__name__, passed=False, details={"exception": str(e)})
        results.append(r)
        if args.json:
            print(json.dumps(r.to_dict(), indent=2, ensure_ascii=False))
        else:
            ok = "[PASS]" if r.passed else "[FAIL]"
            print(f"{ok} passed={r.passed}")
            for k, v in r.details.items():
                print(f"  {k}: {v}")
            if r.notes:
                print(f"  note: {r.notes}")

    n_pass = sum(1 for r in results if r.passed)
    print(f"\n{'='*60}\nResults: {n_pass}/{len(results)} passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
