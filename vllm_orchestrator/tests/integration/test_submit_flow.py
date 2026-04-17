"""E2E submit flow: request → queue → llm → result"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSuccess
from tests.fixtures.helpers import make_test_dispatcher, make_request


def test_basic_submit():
    print("  [1] Basic submit flow")
    adapter = FakeLLMSuccess()
    dispatcher, router, fallback, health, circuit, queue = make_test_dispatcher(adapter)

    req = make_request("builder", "requirement_parse", "2층 주택 거실 크게")
    spec = router.resolve(req)
    result = dispatcher.dispatch(req, spec)

    assert result.request_id.startswith("req_"), f"bad request_id: {result.request_id}"
    assert result.task_id.startswith("task_"), f"bad task_id: {result.task_id}"
    assert result.status == "done", f"status={result.status}, errors={result.errors}"
    assert result.slots is not None, "slots is None"
    assert result.latency_ms >= 0, "latency negative"
    assert queue.total_completed == 1
    assert health.is_healthy("llm")
    print(f"    OK: status={result.status}, slots={result.slots}, latency={result.latency_ms}ms")


def test_all_18_tasks_submit():
    print("  [2] All 18 operational tasks submit")
    from src.app.domain.registry import list_enabled_tasks
    adapter = FakeLLMSuccess()
    dispatcher, router, fallback, health, circuit, queue = make_test_dispatcher(adapter)

    tasks = list_enabled_tasks()
    passed = 0
    for tt in tasks:
        parts = tt.split(".", 1)
        req = make_request(parts[0], parts[1], "test input")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        if result.status == "done":
            passed += 1
        else:
            print(f"    FAIL: {tt} → {result.status}: {result.errors}")

    assert passed == len(tasks), f"{passed}/{len(tasks)}"
    print(f"    OK: {passed}/{len(tasks)} tasks completed")


if __name__ == "__main__":
    print("=== Integration: Submit Flow ===")
    test_basic_submit()
    test_all_18_tasks_submit()
    print("PASSED")
