"""
test_real_orchestrator.py — vllm_orchestrator + 실제 CPU LLM 서버 검증

핵심 질문: queue/breaker/scheduler가 실서버 부하에서 실제로 효과 있는가?

비교 대상: 이전 runtime_llm_gateway 연속 40건 = 24/40 (60%)
목표: vllm_orchestrator에서 동일 조건 연속 실행 시 개선 증명

infra-dependent (T-tranche-6, 2026-04-08):
    Every test in this file drives a real ``VLLMHttpAdapter`` against
    ``http://192.168.57.105:8000``. NOT part of the default deterministic
    gate. Run explicitly with ``pytest -m infra`` when the server is up.
"""
import sys, json, time, statistics
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

SERVER_URL = "http://192.168.57.105:8000"


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
            errs = str(result.errors).lower()
            if "ircuit" in errs:
                self.breaker_open += 1
            elif "timeout" in errs or "timed" in errs:
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
        return f"""
{'='*60}
  {label}
{'='*60}
  submitted:       {self.submitted}
  full_success:    {self.full_success}  ({self.full_success/max(self.submitted,1)*100:.0f}%)
  cached_success:  {self.cached_success}
  mock_success:    {self.mock_success}
  rejected:        {self.rejected}
  failed:          {self.failed}
  timeout:         {self.timeout}
  breaker_open:    {self.breaker_open}
  completed:       {self.completed}/{self.submitted} ({self.completed/max(self.submitted,1)*100:.0f}%)
  p50:             {self.p50}ms
  p95:             {self.p95}ms"""


def make_orchestrator():
    """vllm_orchestrator 풀 스택 생성 (실서버 연결)"""
    adapter = VLLMHttpAdapter(SERVER_URL, "internal-token", "qwen2.5-0.5b-instruct")
    health = HealthRegistry(fail_threshold=5)
    circuit = CircuitBreaker(fail_threshold=5, reset_timeout_s=30)
    queue = QueueManager(max_concurrency=1, max_depth=50, task_timeout_s=180)
    scheduler = Scheduler(cooldown_heavy_s=3.0, cooldown_light_s=1.0)
    timeouts = TimeoutPolicy()
    llm = LLMClient(adapter, health, circuit, max_retries=1)
    router = Router()
    fallback = DegradedModeHandler()
    dispatcher = Dispatcher(llm_client=llm, queue=queue, scheduler=scheduler, timeouts=timeouts)
    return dispatcher, router, fallback, health, circuit, queue


def req(domain, task, text):
    return TaskRequest(domain=domain, task_name=task, user_input=text)


# ===================================================================
# Test 1: 서버 연결
# ===================================================================

def test_server():
    print("[1] SERVER CHECK")
    adapter = VLLMHttpAdapter(SERVER_URL, "internal-token", "qwen2.5-0.5b-instruct")
    ok = adapter.is_available()
    print(f"  available: {ok}")
    assert ok, "Server not available"


# ===================================================================
# Test 2: Smoke 4건
# ===================================================================

def test_smoke():
    print("\n[2] SMOKE (4 domains)")
    d, r, fb, h, c, q = make_orchestrator()
    m = Metrics()

    cases = [
        ("builder", "requirement_parse", "2층 주택 거실 크게"),
        ("cad", "constraint_parse", "방수 샤워필터"),
        ("minecraft", "edit_parse", "정면 창문 넓게"),
        ("animation", "shot_parse", "노을빛 클로즈업"),
    ]
    for domain, task, text in cases:
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        ok = "OK" if result.status == "done" else result.status
        print(f"  {domain}.{task}: {ok} ({result.latency_ms}ms)")

    print(m.report("SMOKE"))
    return m


# ===================================================================
# Test 3: Serial 10건
# ===================================================================

def test_serial_10():
    print("\n[3] SERIAL 10")
    d, r, fb, h, c, q = make_orchestrator()
    m = Metrics()

    cases = [
        ("builder", "requirement_parse", "3층 다세대 주택"),
        ("builder", "patch_intent_parse", "창문 크기 늘려줘"),
        ("cad", "constraint_parse", "USB-C 도킹스테이션"),
        ("cad", "priority_parse", "방수 최우선"),
        ("minecraft", "edit_parse", "지붕 박공으로"),
        ("minecraft", "style_check", "중세풍 체크"),
        ("animation", "shot_parse", "추격 빠르게"),
        ("animation", "camera_intent_parse", "공포 어둠"),
        ("builder", "zone_priority_parse", "주거지역 건폐율"),
        ("cad", "system_split_parse", "전기 배수 분리"),
    ]
    for i, (domain, task, text) in enumerate(cases, 1):
        result = d.dispatch(req(domain, task, text), r.resolve(req(domain, task, text)))
        m.record(result)
        ok = "OK" if result.status == "done" else result.status
        print(f"  [{i:>2}] {domain}.{task:<25} {ok} ({result.latency_ms}ms)")

    print(m.report("SERIAL 10"))
    return m


# ===================================================================
# Test 4: Serial 40건 (핵심 — 이전 60% vs 현재 ?)
# ===================================================================

def test_serial_40():
    print("\n[4] SERIAL 40 (연속, 간격 없이)")
    d, r, fb, h, c, q = make_orchestrator()
    m = Metrics()

    tasks = list_enabled_tasks()
    task_list = (tasks * 3)[:40]

    start_all = time.time()
    for i, tt in enumerate(task_list):
        parts = tt.split(".", 1)
        result = d.dispatch(req(parts[0], parts[1], f"serial40 #{i}"), r.resolve(req(parts[0], parts[1], f"#{i}")))

        # fallback 캐시
        if result.slots and result.status == "done":
            fb.cache_good_result(tt, result.slots)
        elif result.status == "error":
            fb_result = fb.handle_failure(req(parts[0], parts[1], f"#{i}"), result.errors)
            result = fb_result

        m.record(result)
        if (i+1) % 10 == 0:
            elapsed = int((time.time() - start_all))
            print(f"  ... {i+1}/40 ({elapsed}s) full={m.full_success} cached={m.cached_success} mock={m.mock_success} fail={m.failed} timeout={m.timeout}")

    total_time = int(time.time() - start_all)
    print(m.report("SERIAL 40"))
    print(f"  total_time: {total_time}s")
    print(f"  circuit: {c.snapshot()}")
    print(f"  queue: {q.snapshot()}")

    # === 비교 ===
    print(f"\n  === COMPARISON ===")
    print(f"  이전 (runtime_llm_gateway 연속): 24/40 (60%)")
    print(f"  현재 (vllm_orchestrator 연속):   {m.full_success}/40 ({m.full_success/40*100:.0f}%)")
    print(f"  개선: +{m.full_success - 24} full success")
    print(f"  degraded: {m.cached_success + m.mock_success}")
    print(f"  non-fatal: {m.completed}/40 ({m.completed/40*100:.0f}%)")

    return m


# ===================================================================
# Test 5: Breaker/Health 상태 확인
# ===================================================================

def test_system_state():
    print("\n[5] SYSTEM STATE AFTER LOAD")
    d, r, fb, h, c, q = make_orchestrator()

    # 5건 실행 후 상태
    for i in range(5):
        result = d.dispatch(req("minecraft", "edit_parse", f"state #{i}"), r.resolve(req("minecraft", "edit_parse", "test")))

    print(f"  circuit: {c.state}")
    print(f"  health_llm: {h.is_healthy('llm')}")
    print(f"  queue_running: {q.snapshot()['running']}")
    assert q.snapshot()["running"] == 0, "Worker stuck"
    print("  PASS: no stuck workers")


# ===================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("VLLM_ORCHESTRATOR + REAL CPU LLM SERVER")
    print("=" * 60)

    test_server()
    smoke = test_smoke()
    s10 = test_serial_10()
    s40 = test_serial_40()
    test_system_state()

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    for label, m in [("smoke", smoke), ("serial_10", s10), ("serial_40", s40)]:
        print(f"  {label}: full={m.full_success}/{m.submitted} cached={m.cached_success} mock={m.mock_success} fail={m.failed} timeout={m.timeout} p50={m.p50}ms")
    print("=" * 60)
