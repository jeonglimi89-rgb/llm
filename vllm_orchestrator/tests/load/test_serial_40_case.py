"""
load/test_serial_40_case.py - 핵심 부하 테스트

기존 문제: 연속 40건 실행 시 60% pass (Minecraft/Animation 전멸)
이번 목표: queue + scheduler + breaker + fallback으로 개선 입증

** full success와 degraded success를 반드시 분리 보고 **

load-marked (T-tranche-6, 2026-04-08): deterministic FakeLLM-backed load
test, excluded from the default gate for speed. Run explicitly with
``pytest -m load``.
"""
import sys, json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSuccess, FakeLLMSlow, FakeLLMBrokenJSON
from tests.fixtures.helpers import make_test_dispatcher, make_request, LoadMetrics
from src.app.domain.registry import list_enabled_tasks

pytestmark = pytest.mark.load


def test_serial_40_mock():
    """Mock LLM으로 40건 연속 — 구조적 안정성 검증"""
    print("\n=== LOAD: Serial 40 (Mock LLM) ===")
    adapter = FakeLLMSuccess()
    dispatcher, router, fallback, health, circuit, queue = make_test_dispatcher(adapter)

    metrics = LoadMetrics()
    tasks = list_enabled_tasks()

    # 40건: 18 tasks × ~2회 + 나머지
    task_list = (tasks * 3)[:40]

    for i, tt in enumerate(task_list):
        parts = tt.split(".", 1)
        req = make_request(parts[0], parts[1], f"load test case {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)

        # fallback 캐시 갱신
        if result.slots and result.status == "done":
            fallback.cache_good_result(tt, result.slots)

        metrics.record(result)

    print(metrics.report())
    print(f"\n  queue: {queue.snapshot()}")
    print(f"  circuit: {circuit.snapshot()}")
    print(f"  health: {health.snapshot()['components'].get('llm', {})}")

    # Assertions
    assert metrics.full_success == 40, f"Expected 40 full success, got {metrics.full_success}"
    assert metrics.rejected == 0, f"Unexpected rejections: {metrics.rejected}"
    assert metrics.failed == 0, f"Unexpected failures: {metrics.failed}"
    assert circuit.state == "closed", f"Circuit should be closed, got {circuit.state}"
    print("\n  PASSED: 40/40 full success, 0 degraded, 0 rejected")


def test_serial_mixed_quality():
    """일부 실패하는 LLM으로 40건 — fallback 분리 통계 검증"""
    print("\n=== LOAD: Serial 40 (Mixed quality LLM) ===")

    # 10번마다 깨진 JSON 반환하는 adapter
    class MixedAdapter:
        provider_name = "mixed"
        call_count = 0

        def generate(self, messages, max_tokens=512, temperature=0.1, timeout_s=120):
            self.call_count += 1
            if self.call_count % 10 == 0:
                return {"text": "BROKEN {invalid json", "prompt_tokens": 10, "completion_tokens": 5}
            import json
            return {
                "text": json.dumps({"ok": True, "n": self.call_count}),
                "prompt_tokens": 50, "completion_tokens": 20,
            }

        def is_available(self):
            return True

    adapter = MixedAdapter()
    dispatcher, router, fallback, health, circuit, queue = make_test_dispatcher(adapter, max_retries=0)

    metrics = LoadMetrics()
    tasks = list_enabled_tasks()
    task_list = (tasks * 3)[:40]

    for i, tt in enumerate(task_list):
        parts = tt.split(".", 1)
        req = make_request(parts[0], parts[1], f"mixed test {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)

        if result.slots and result.status == "done":
            fallback.cache_good_result(tt, result.slots)
        elif result.status == "error" and fallback:
            # fallback 시도
            fb_result = fallback.handle_failure(req, result.errors)
            result = fb_result

        metrics.record(result)

    print(metrics.report())

    # 핵심: full과 degraded 분리
    print(f"\n  === SEPARATED STATISTICS ===")
    print(f"  TRUE full LLM success: {metrics.full_success}/40 ({metrics.full_success_rate:.1%})")
    print(f"  Degraded (cached+mock): {metrics.cached_success + metrics.mock_success}/40")
    print(f"  Failed/rejected: {metrics.failed + metrics.rejected}/40")

    # full success가 절반 이상이어야
    assert metrics.full_success >= 20, f"full success too low: {metrics.full_success}"
    # degraded가 전체의 절반 넘으면 경고
    degraded_total = metrics.cached_success + metrics.mock_success
    if degraded_total > 20:
        print(f"  WARNING: degraded ({degraded_total}) > half of total — quality concern")

    print("\n  PASSED")


if __name__ == "__main__":
    test_serial_40_mock()
    test_serial_mixed_quality()
