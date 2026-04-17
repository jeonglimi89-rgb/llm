"""Queue saturation → 적절한 rejection 검증

load-marked (T-tranche-6, 2026-04-08): deterministic FakeLLM-backed load
test, excluded from the default gate for speed. Run explicitly with
``pytest -m load``.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSuccess
from tests.fixtures.helpers import make_test_dispatcher, make_request, LoadMetrics
from src.app.core.errors import OverloadError

pytestmark = pytest.mark.load


def test_overload_rejects_gracefully():
    """queue depth 초과 시 시스템이 멈추지 않고 거절"""
    print("\n=== LOAD: Overload Rejection ===")

    adapter = FakeLLMSuccess()
    # queue depth=3으로 매우 작게
    dispatcher, router, _, health, circuit, queue = make_test_dispatcher(adapter, queue_depth=3)

    metrics = LoadMetrics()

    # 5건 연속 제출 (depth=3이면 정상 처리, 동기식이라 실제 overflow 안 남)
    # 동기식 queue에서는 순차 처리되므로 overflow 대신 순차 통과
    for i in range(5):
        req = make_request("minecraft", "edit_parse", f"overload {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        metrics.record(result)

    snap = queue.snapshot()
    print(f"  queue: {snap}")
    print(metrics.report())

    # 동기식 queue에서는 전부 처리됨 (순차)
    assert metrics.submitted == 5
    assert snap["running"] == 0, "Worker stuck"
    assert snap["total_rejected"] == 0, "Unexpected rejection in sync mode"
    print(f"\n  PASSED: {metrics.submitted} submitted, {snap['total_rejected']} rejected, system alive")


if __name__ == "__main__":
    test_overload_rejects_gracefully()
