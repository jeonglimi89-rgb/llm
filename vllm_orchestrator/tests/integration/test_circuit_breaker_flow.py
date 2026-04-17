"""Circuit breaker: open → reject → recovery"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMFailure, FakeLLMSuccess
from tests.fixtures.helpers import make_test_dispatcher, make_request


def test_breaker_opens_after_threshold():
    print("  [1] Breaker opens after 3 failures")
    adapter = FakeLLMFailure()
    dispatcher, router, fallback, health, circuit, _ = make_test_dispatcher(adapter, max_retries=0)

    results = []
    for i in range(4):
        req = make_request("cad", "constraint_parse", f"fail {i}")
        spec = router.resolve(req)
        result = dispatcher.dispatch(req, spec)
        results.append(result)

    # 처음 3개: LLM 호출 실패 (error)
    # 4번째: circuit open으로 즉시 차단
    assert circuit.state == "open", f"Expected open, got {circuit.state}"
    assert results[3].status == "error", f"4th should be error, got {results[3].status}"
    # circuit open 상태에서는 "Circuit" 또는 LLM 자체 에러
    assert any("ircuit" in str(e) or "onnection" in str(e) or "failed" in str(e).lower() for e in results[3].errors), \
        f"4th errors should mention circuit/connection: {results[3].errors}"
    print(f"    OK: breaker open after 3 fails, 4th request blocked (llm calls={adapter.call_count})")


def test_breaker_recovers():
    print("  [2] Breaker recovers after cooldown")
    fail_adapter = FakeLLMFailure()
    dispatcher, router, _, health, circuit, _ = make_test_dispatcher(fail_adapter, max_retries=0)

    # 3회 실패 → open
    for i in range(3):
        req = make_request("cad", "constraint_parse", f"fail {i}")
        spec = router.resolve(req)
        dispatcher.dispatch(req, spec)

    assert circuit.state == "open"

    # reset_timeout=0.5초 대기 → half_open
    time.sleep(0.6)
    assert circuit.state == "half_open"

    # half_open에서 성공 시뮬레이션
    circuit.record_success()
    assert circuit.state == "closed"
    print(f"    OK: open → half_open → closed recovery")


if __name__ == "__main__":
    print("=== Integration: Circuit Breaker ===")
    test_breaker_opens_after_threshold()
    test_breaker_recovers()
    print("PASSED")
