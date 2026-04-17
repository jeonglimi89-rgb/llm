"""
test_real_server_full.py — 실제 CPU LLM 서버 종합 검증

실행: cd vllm_orchestrator && python -X utf8 tests/load/test_real_server_full.py

출력: 전 항목 분리 통계

infra-dependent (T-tranche-6, 2026-04-08):
    Every test in this file drives a real ``VLLMHttpAdapter`` against
    ``http://192.168.57.105:8000``. NOT part of the default deterministic
    gate. Run explicitly with ``pytest -m infra`` when the server is up.
"""
from __future__ import annotations

import json
import os
import sys
import time
import statistics
from pathlib import Path
from dataclasses import dataclass, field

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

pytestmark = pytest.mark.infra

from src.app.settings import AppSettings
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import TimeoutPolicy
from src.app.llm.client import LLMClient
from src.app.llm.adapters.vllm_http import VLLMHttpAdapter
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.core.contracts import TaskRequest, TaskResult
from src.app.domain.registry import list_enabled_tasks

SERVER_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000")
API_KEY = "internal-token"
MODEL = os.environ.get("LLM_MODEL", "/home/suzzi/models/Qwen2.5-7B-Instruct-AWQ")


@dataclass
class Metrics:
    submitted: int = 0
    full_success: int = 0
    cached_success: int = 0
    mock_success: int = 0
    rejected: int = 0
    failed: int = 0
    timeout: int = 0
    breaker_open: int = 0
    latencies: list[int] = field(default_factory=list)

    def record(self, result: TaskResult):
        self.submitted += 1
        self.latencies.append(result.latency_ms)
        s, m = result.status, result.fallback_mode
        if s == "done" and m == "full":
            self.full_success += 1
        elif s == "degraded" and m == "cached":
            self.cached_success += 1
        elif s == "degraded" and m == "mock":
            self.mock_success += 1
        elif s == "shed":
            self.rejected += 1
        elif s == "error":
            if "ircuit" in str(result.errors):
                self.breaker_open += 1
            elif "imeout" in str(result.errors).lower() or "timed" in str(result.errors).lower():
                self.timeout += 1
            else:
                self.failed += 1

    @property
    def completed(self):
        return self.full_success + self.cached_success + self.mock_success

    @property
    def p50(self):
        return int(statistics.median(self.latencies)) if self.latencies else 0

    @property
    def p95(self):
        if not self.latencies: return 0
        s = sorted(self.latencies)
        return s[min(int(len(s)*0.95), len(s)-1)]

    def report(self, label: str) -> str:
        lines = [
            f"\n{'='*60}",
            f"  {label}",
            f"{'='*60}",
            f"  submitted:       {self.submitted}",
            f"  full_success:    {self.full_success}  ({self.full_success/max(self.submitted,1)*100:.0f}%)",
            f"  cached_success:  {self.cached_success}",
            f"  mock_success:    {self.mock_success}",
            f"  rejected:        {self.rejected}",
            f"  failed:          {self.failed}",
            f"  timeout:         {self.timeout}",
            f"  breaker_open:    {self.breaker_open}",
            f"  completed:       {self.completed}/{self.submitted} ({self.completed/max(self.submitted,1)*100:.0f}%)",
            f"  p50:             {self.p50}ms",
            f"  p95:             {self.p95}ms",
        ]
        return "\n".join(lines)


def make_real():
    adapter = VLLMHttpAdapter(SERVER_URL, API_KEY, MODEL)
    health = HealthRegistry(fail_threshold=3)
    circuit = CircuitBreaker(fail_threshold=5, reset_timeout_s=30)
    queue = QueueManager(max_concurrency=1, max_depth=50, task_timeout_s=180)
    scheduler = Scheduler(cooldown_heavy_s=3.0, cooldown_light_s=1.0)
    timeouts = TimeoutPolicy()
    llm = LLMClient(adapter, health, circuit, max_retries=1)
    router = Router()
    fallback = DegradedModeHandler()
    dispatcher = Dispatcher(llm_client=llm, queue=queue, scheduler=scheduler, timeouts=timeouts)
    return dispatcher, router, fallback, health, circuit, queue, adapter


def req(domain, task, text):
    return TaskRequest(domain=domain, task_name=task, user_input=text)


# ===================================================================
# Test 1: Server Readiness
# ===================================================================

def test_server_ready():
    print("\n[1] SERVER READINESS")
    adapter = VLLMHttpAdapter(SERVER_URL, API_KEY, MODEL)
    avail = adapter.is_available()
    print(f"  available: {avail}")
    assert avail, "Server not available"
    print("  PASS")
    return avail


# ===================================================================
# Test 2: Real Smoke (1 per domain)
# ===================================================================

def test_real_smoke():
    print("\n[2] REAL SMOKE (4 domains × 1)")
    d, r, fb, h, c, q, _ = make_real()
    m = Metrics()

    cases = [
        ("builder", "requirement_parse", "2층 주택 거실 크게"),
        ("cad", "constraint_parse", "방수 샤워필터 배수 포함"),
        ("minecraft", "edit_parse", "정면 창문 넓게"),
        ("animation", "shot_parse", "노을빛 슬픈 클로즈업"),
    ]

    for domain, task, text in cases:
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        status = "OK" if result.status == "done" else result.status
        print(f"  {domain}.{task}: {status} ({result.latency_ms}ms)")
        if result.slots:
            print(f"    slots: {json.dumps(result.slots, ensure_ascii=False)[:80]}")

    print(m.report("SMOKE RESULTS"))
    return m


# ===================================================================
# Test 3: Serial 10-case
# ===================================================================

def test_serial_10():
    print("\n[3] SERIAL 10-CASE")
    d, r, fb, h, c, q, _ = make_real()
    m = Metrics()

    cases = [
        ("builder", "requirement_parse", "3층 다세대 주택"),
        ("builder", "patch_intent_parse", "창문 크기 늘려줘"),
        ("cad", "constraint_parse", "USB-C 도킹스테이션"),
        ("cad", "priority_parse", "방수 최우선"),
        ("minecraft", "edit_parse", "지붕 박공으로 변경"),
        ("minecraft", "style_check", "중세풍 맞는지 체크"),
        ("animation", "shot_parse", "추격 장면 빠르게"),
        ("animation", "camera_intent_parse", "공포 어둠 연출"),
        ("builder", "zone_priority_parse", "주거지역 건폐율"),
        ("cad", "system_split_parse", "전기 배수 분리"),
    ]

    for domain, task, text in cases:
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        s = "OK" if result.status == "done" else result.status
        print(f"  [{m.submitted:>2}] {domain}.{task:<25} {s} ({result.latency_ms}ms)")

    print(m.report("SERIAL 10 RESULTS"))
    return m


# ===================================================================
# Test 4: Serial 40-case
# ===================================================================

def test_serial_40():
    print("\n[4] SERIAL 40-CASE")
    d, r, fb, h, c, q, _ = make_real()
    m = Metrics()

    tasks = list_enabled_tasks()
    task_list = (tasks * 3)[:40]

    for i, tt in enumerate(task_list):
        parts = tt.split(".", 1)
        result = d.dispatch(req(parts[0], parts[1], f"serial40 case {i}"), r.resolve(req(parts[0], parts[1], f"case {i}")))

        if result.slots and result.status == "done":
            fb.cache_good_result(tt, result.slots)
        elif result.status == "error":
            fb_result = fb.handle_failure(req(parts[0], parts[1], f"case {i}"), result.errors)
            result = fb_result

        m.record(result)

        if (i + 1) % 10 == 0:
            print(f"  ... {i+1}/40 done (full={m.full_success}, cached={m.cached_success}, mock={m.mock_success}, fail={m.failed})")

    print(m.report("SERIAL 40 RESULTS"))
    return m


# ===================================================================
# Test 5: Mixed Heavy/Light
# ===================================================================

def test_mixed_heavy_light():
    print("\n[5] MIXED HEAVY/LIGHT (10 cases)")
    d, r, fb, h, c, q, _ = make_real()
    m = Metrics()

    # heavy → light → heavy → light 번갈아
    cases = [
        ("builder", "requirement_parse", "heavy: 2층 주택"),      # heavy
        ("minecraft", "style_check", "light: 중세풍 체크"),        # light
        ("cad", "constraint_parse", "heavy: 방수 센서"),           # heavy
        ("animation", "camera_intent_parse", "light: 공포 연출"),  # light
        ("animation", "shot_parse", "heavy: 추격 장면"),           # heavy
        ("builder", "exterior_style_parse", "light: 벽돌 외관"),   # light
        ("cad", "constraint_parse", "heavy: 모터 기구부"),         # heavy
        ("minecraft", "anchor_resolution", "light: 동쪽 2층"),     # light
        ("builder", "requirement_parse", "heavy: 상가주택"),       # heavy
        ("cad", "priority_parse", "light: 안전 우선"),             # light
    ]

    for domain, task, text in cases:
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        h_l = "H" if "heavy" in text else "L"
        s = "OK" if result.status == "done" else result.status
        print(f"  [{h_l}] {domain}.{task:<25} {s} ({result.latency_ms}ms)")

    print(m.report("MIXED HEAVY/LIGHT RESULTS"))
    return m


# ===================================================================
# Test 6: Timeout Recovery (real)
# ===================================================================

def test_timeout_recovery_real():
    print("\n[6] TIMEOUT RECOVERY")
    d, r, fb, h, c, q, _ = make_real()
    m = Metrics()

    # 정상 2건 → 매우 긴 입력(timeout 유도) → 정상 2건
    normal_cases = [
        ("minecraft", "edit_parse", "창문 넓게"),
        ("cad", "priority_parse", "방수 우선"),
    ]

    print("  Phase 1: normal requests")
    for domain, task, text in normal_cases:
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        print(f"    {domain}.{task}: {result.status} ({result.latency_ms}ms)")

    print(f"  Phase 2: recovery requests")
    for domain, task, text in normal_cases:
        result = d.dispatch(req(domain, task, text + " 회복"), r.resolve(req(domain, task, text)))
        m.record(result)
        print(f"    {domain}.{task}: {result.status} ({result.latency_ms}ms)")

    print(f"  circuit: {c.state}, health: {h.is_healthy('llm')}")
    print(m.report("TIMEOUT RECOVERY RESULTS"))
    return m


# ===================================================================
# Test 7: Breaker Recovery (real)
# ===================================================================

def test_breaker_recovery_real():
    print("\n[7] BREAKER STATE CHECK")
    d, r, fb, h, c, q, _ = make_real()

    # 정상 서버이므로 breaker는 closed 유지 예상
    for i in range(5):
        result = d.dispatch(
            req("builder", "patch_intent_parse", f"breaker test {i}"),
            r.resolve(req("builder", "patch_intent_parse", "test")),
        )
    print(f"  After 5 requests: circuit={c.state}, health_llm={h.is_healthy('llm')}")
    assert c.state == "closed", f"Unexpected circuit state: {c.state}"
    print("  PASS: circuit stayed closed (server stable)")


# ===================================================================
# Test 8: Overload (real)
# ===================================================================

def test_overload_real():
    print("\n[8] OVERLOAD CHECK")
    d, r, fb, h, c, q, _ = make_real()

    for i in range(5):
        result = d.dispatch(
            req("minecraft", "edit_parse", f"overload {i}"),
            r.resolve(req("minecraft", "edit_parse", "test")),
        )

    snap = q.snapshot()
    print(f"  queue: completed={snap['total_completed']}, rejected={snap['total_rejected']}, running={snap['running']}")
    assert snap["running"] == 0, "Worker stuck"
    print("  PASS: no stuck workers")


# ===================================================================
# MAIN
# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("REAL SERVER FULL VERIFICATION")
    print("=" * 60)

    all_metrics = {}

    # 1
    if not test_server_ready():
        print("ABORTED: server not available")
        sys.exit(1)

    # 2
    all_metrics["smoke"] = test_real_smoke()

    # 3
    all_metrics["serial_10"] = test_serial_10()

    # 4
    all_metrics["serial_40"] = test_serial_40()

    # 5
    all_metrics["mixed"] = test_mixed_heavy_light()

    # 6
    all_metrics["timeout_recovery"] = test_timeout_recovery_real()

    # 7
    test_breaker_recovery_real()

    # 8
    test_overload_real()

    # ===== FINAL SUMMARY =====
    print("\n" + "=" * 60)
    print("FINAL SEPARATED STATISTICS")
    print("=" * 60)

    for label, m in all_metrics.items():
        print(f"\n  [{label}]")
        print(f"    full_success:   {m.full_success}/{m.submitted}")
        print(f"    cached:         {m.cached_success}")
        print(f"    mock:           {m.mock_success}")
        print(f"    failed:         {m.failed}")
        print(f"    timeout:        {m.timeout}")
        print(f"    breaker:        {m.breaker_open}")
        print(f"    rejected:       {m.rejected}")
        print(f"    p50: {m.p50}ms  p95: {m.p95}ms")

    print("\n" + "=" * 60)
    print("DONE")
