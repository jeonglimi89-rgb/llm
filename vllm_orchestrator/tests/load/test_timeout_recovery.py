"""timeout 발생 후 시스템 회복 검증

load-marked (T-tranche-6, 2026-04-08): deterministic FakeLLM-backed load
test, excluded from the default gate for speed. Run explicitly with
``pytest -m load``.
"""
import sys, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSlow, FakeLLMSuccess
from tests.fixtures.helpers import make_test_dispatcher, make_request, LoadMetrics

pytestmark = pytest.mark.load


def test_timeout_then_recovery():
    """slow LLM 3회 → 정상 LLM 전환 → 회복"""
    print("\n=== LOAD: Timeout → Recovery ===")

    # Phase 1: slow adapter (timeout 유도)
    slow = FakeLLMSlow(delay_s=0.5)
    dispatcher, router, fallback, health, circuit, queue = make_test_dispatcher(slow, max_retries=0)

    metrics = LoadMetrics()

    # slow 요청 3건
    for i in range(3):
        req = make_request("cad", "constraint_parse", f"slow {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        metrics.record(result)

    print(f"  After slow phase: circuit={circuit.state}, health_llm={health.is_healthy('llm')}")
    slow_success = metrics.full_success

    # Phase 2: 정상 adapter로 교체 (실제 운영에서는 서버 복구 상황)
    fast = FakeLLMSuccess()
    from src.app.llm.client import LLMClient
    dispatcher.llm = LLMClient(fast, health, circuit, max_retries=1)

    # circuit이 open이면 reset
    if circuit.state == "open":
        time.sleep(0.6)  # half_open 대기

    # 정상 요청 5건
    for i in range(5):
        req = make_request("builder", "requirement_parse", f"recover {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        metrics.record(result)

    print(f"  After recovery phase: circuit={circuit.state}")
    print(metrics.report())

    # Assertions
    assert metrics.full_success > slow_success, "No recovery after timeout"
    assert circuit.state == "closed", f"Circuit not recovered: {circuit.state}"
    assert queue.snapshot()["running"] == 0, "Worker stuck after timeout"
    print(f"\n  PASSED: recovered {metrics.full_success - slow_success} tasks after timeout")


if __name__ == "__main__":
    test_timeout_then_recovery()
