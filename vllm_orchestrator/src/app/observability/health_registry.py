"""컴포넌트별 health 상태 추적"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentHealth:
    name: str
    healthy: bool = True
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    _latency_sum: float = 0.0


class HealthRegistry:
    """모든 컴포넌트 health를 한 곳에서 추적"""

    def __init__(self, fail_threshold: int = 3):
        self._components: dict[str, ComponentHealth] = {}
        self._fail_threshold = fail_threshold

    def register(self, name: str) -> None:
        if name not in self._components:
            self._components[name] = ComponentHealth(name=name)

    def record_success(self, name: str, latency_ms: float) -> None:
        c = self._get(name)
        c.healthy = True
        c.last_success = time.time()
        c.consecutive_failures = 0
        c.total_calls += 1
        c._latency_sum += latency_ms
        c.avg_latency_ms = c._latency_sum / c.total_calls

    def record_failure(self, name: str, reason: str = "") -> None:
        c = self._get(name)
        c.last_failure = time.time()
        c.consecutive_failures += 1
        c.total_calls += 1
        c.total_failures += 1
        if c.consecutive_failures >= self._fail_threshold:
            c.healthy = False

    def is_healthy(self, name: str) -> bool:
        return self._get(name).healthy

    def is_system_healthy(self) -> bool:
        if not self._components:
            return True
        return all(c.healthy for c in self._components.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "system_healthy": self.is_system_healthy(),
            "components": {
                name: {
                    "healthy": c.healthy,
                    "consecutive_failures": c.consecutive_failures,
                    "total_calls": c.total_calls,
                    "total_failures": c.total_failures,
                    "avg_latency_ms": round(c.avg_latency_ms, 1),
                }
                for name, c in self._components.items()
            },
        }

    def _get(self, name: str) -> ComponentHealth:
        if name not in self._components:
            self.register(name)
        return self._components[name]
