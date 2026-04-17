"""Unit tests for normalized error categories"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.core.error_categories import ErrorCategory, classify_error


def test_classify_timeout():
    assert classify_error("Connection timed out") == ErrorCategory.TIMEOUT
    assert classify_error("Request timeout after 60s") == ErrorCategory.TIMEOUT
    print("  [OK] timeout")


def test_classify_network():
    assert classify_error("Connection refused") == ErrorCategory.NETWORK
    assert classify_error("Network unreachable") == ErrorCategory.NETWORK
    print("  [OK] network")


def test_classify_validation():
    assert classify_error("Schema validation failed") == ErrorCategory.VALIDATION
    assert classify_error("Invalid JSON parse") == ErrorCategory.VALIDATION
    print("  [OK] validation")


def test_classify_breaker():
    assert classify_error("Circuit breaker open") == ErrorCategory.BREAKER_OPEN
    print("  [OK] breaker")


def test_classify_throttled():
    assert classify_error("Queue full") == ErrorCategory.THROTTLED
    assert classify_error("Rate limit exceeded") == ErrorCategory.THROTTLED
    print("  [OK] throttled")


def test_classify_unavailable():
    assert classify_error("Engine unavailable") == ErrorCategory.ENGINE_UNAVAILABLE
    print("  [OK] unavailable")


def test_classify_none():
    assert classify_error("") == ErrorCategory.NONE
    assert classify_error(None or "") == ErrorCategory.NONE
    print("  [OK] none")


def test_classify_unknown():
    assert classify_error("strange weird thing") == ErrorCategory.UNKNOWN
    print("  [OK] unknown fallback")


TESTS = [
    test_classify_timeout, test_classify_network, test_classify_validation,
    test_classify_breaker, test_classify_throttled, test_classify_unavailable,
    test_classify_none, test_classify_unknown,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Error Categories Unit Tests")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
    print(f"\nResults: {passed}/{len(TESTS)} passed")
    if passed == len(TESTS):
        print("ALL ERROR CATEGORY TESTS PASSED!")
