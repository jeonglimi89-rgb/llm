"""
core/error_categories.py - 정규화된 에러 카테고리

오케스트레이터/엔진/도메인 경로 전반에서 일관된 에러 분류.
"""
from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """모든 컴포넌트가 공유하는 에러 카테고리"""
    NONE = "none"
    TIMEOUT = "timeout"
    NETWORK = "network"
    VALIDATION = "validation"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    RETRY_EXHAUSTED = "retry_exhausted"
    BREAKER_OPEN = "breaker_open"
    THROTTLED = "throttled"
    UNKNOWN = "unknown"


def classify_error(error_text: str) -> ErrorCategory:
    """에러 텍스트를 카테고리로 분류"""
    if not error_text:
        return ErrorCategory.NONE
    e = error_text.lower()
    if "timeout" in e or "timed out" in e:
        return ErrorCategory.TIMEOUT
    if "circuit" in e or "breaker" in e:
        return ErrorCategory.BREAKER_OPEN
    if "retry" in e and ("exhaust" in e or "exceed" in e):
        return ErrorCategory.RETRY_EXHAUSTED
    if "throttl" in e or "rate limit" in e or "queue full" in e:
        return ErrorCategory.THROTTLED
    if "connection" in e or "network" in e or "refused" in e or "unreachable" in e:
        return ErrorCategory.NETWORK
    if "validation" in e or "schema" in e or "parse" in e or "invalid" in e:
        return ErrorCategory.VALIDATION
    if "unavailable" in e or "not found" in e or "no such" in e:
        return ErrorCategory.ENGINE_UNAVAILABLE
    return ErrorCategory.UNKNOWN
