"""Fallback chain: full → cached → mock → reject"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.fake_llm import FakeLLMSuccess, FakeLLMFailure
from tests.fixtures.helpers import make_test_dispatcher, make_request
from src.app.fallback.degraded_modes import DegradedModeHandler


def test_full_success_no_fallback():
    print("  [1] Full success — no fallback triggered")
    adapter = FakeLLMSuccess()
    dispatcher, router, fallback, _, _, _ = make_test_dispatcher(adapter)

    req = make_request("builder", "requirement_parse", "2층 주택")
    spec = router.resolve(req)
    result = dispatcher.dispatch(req, spec)

    assert result.status == "done"
    assert result.fallback_mode == "full"
    assert result.slots is not None
    print(f"    OK: full success, no fallback")


def test_cached_fallback():
    print("  [2] Cached fallback after failure")
    fallback = DegradedModeHandler(enable_cached=True, enable_mock=True)

    # 먼저 캐시에 good result 넣기
    fallback.cache_good_result("builder.requirement_parse", {"cached_data": True})

    req = make_request("builder", "requirement_parse", "실패 테스트")
    result = fallback.handle_failure(req, ["llm failed"])

    assert result.status == "degraded"
    assert result.fallback_mode == "cached"
    assert result.slots["cached_data"] is True
    print(f"    OK: cached fallback, slots={result.slots}")


def test_mock_fallback_when_no_cache():
    print("  [3] Mock fallback when no cache")
    fallback = DegradedModeHandler(enable_cached=True, enable_mock=True)

    req = make_request("cad", "constraint_parse", "캐시 없음")
    result = fallback.handle_failure(req, ["llm failed"])

    assert result.status == "degraded"
    assert result.fallback_mode == "mock"
    assert result.slots["mock"] is True
    print(f"    OK: mock fallback (no cache)")


def test_reject_when_all_disabled():
    print("  [4] Reject when cached+mock disabled")
    fallback = DegradedModeHandler(enable_cached=False, enable_mock=False)

    req = make_request("animation", "shot_parse", "전부 비활성")
    result = fallback.handle_failure(req, ["everything down"])

    assert result.status == "error"
    assert result.fallback_mode == "reject"
    print(f"    OK: reject fallback")


def test_fallback_modes_are_distinguishable():
    print("  [5] All 4 modes produce distinct results")
    modes_seen = set()

    # full
    adapter = FakeLLMSuccess()
    dispatcher, router, _, _, _, _ = make_test_dispatcher(adapter)
    req = make_request("cad", "constraint_parse", "test")
    result = dispatcher.dispatch(req, router.resolve(req))
    modes_seen.add(result.fallback_mode)

    # cached, mock, reject
    fb = DegradedModeHandler()
    fb.cache_good_result("x.y", {"c": 1})
    r_cached = fb.handle_failure(make_request("x", "y"), [])
    modes_seen.add(r_cached.fallback_mode)

    r_mock = DegradedModeHandler(enable_cached=False).handle_failure(make_request("a", "b"), [])
    modes_seen.add(r_mock.fallback_mode)

    r_reject = DegradedModeHandler(enable_cached=False, enable_mock=False).handle_failure(make_request("c", "d"), [])
    modes_seen.add(r_reject.fallback_mode)

    assert len(modes_seen) == 4, f"Expected 4 distinct modes, got {modes_seen}"
    print(f"    OK: 4 distinct modes: {modes_seen}")


if __name__ == "__main__":
    print("=== Integration: Fallback Chain ===")
    test_full_success_no_fallback()
    test_cached_fallback()
    test_mock_fallback_when_no_cache()
    test_reject_when_all_disabled()
    test_fallback_modes_are_distinguishable()
    print("PASSED")
