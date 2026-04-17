"""
circuit_breaker.py - 연쇄 실패 차단

상태: CLOSED(정상) → OPEN(차단) → HALF_OPEN(시험)
"""
from __future__ import annotations

import time


class CircuitBreaker:
    """LLM/서버 연쇄 실패 시 자동 차단"""

    def __init__(self, fail_threshold: int = 3, reset_timeout_s: float = 60.0):
        self._fail_threshold = fail_threshold
        self._reset_timeout_s = reset_timeout_s
        self._failures = 0
        self._state = "closed"       # closed, open, half_open
        self._last_failure_time = 0.0

    @property
    def state(self) -> str:
        if self._state == "open":
            if time.time() - self._last_failure_time > self._reset_timeout_s:
                self._state = "half_open"
        return self._state

    def allow(self) -> bool:
        """요청 허용 여부"""
        s = self.state
        if s == "closed":
            return True
        if s == "half_open":
            return True  # 시험 1건 허용
        return False  # open

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self._fail_threshold:
            self._state = "open"

    def force_open(self) -> None:
        self._state = "open"
        self._last_failure_time = time.time()

    def force_close(self) -> None:
        self._state = "closed"
        self._failures = 0

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "failures": self._failures,
            "threshold": self._fail_threshold,
            "reset_timeout_s": self._reset_timeout_s,
        }
