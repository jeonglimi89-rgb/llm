"""공통 헬퍼: Container 빌더, 메트릭 수집기"""
from __future__ import annotations

import tempfile
import statistics
from pathlib import Path
from dataclasses import dataclass, field

from src.app.settings import AppSettings
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import TimeoutPolicy
from src.app.llm.client import LLMClient
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.core.contracts import TaskRequest


def make_test_dispatcher(adapter, max_retries: int = 1, queue_depth: int = 10) -> tuple:
    """테스트용 dispatcher + 관련 객체 생성"""
    health = HealthRegistry(fail_threshold=3)
    circuit = CircuitBreaker(fail_threshold=3, reset_timeout_s=0.5)
    queue = QueueManager(max_concurrency=1, max_depth=queue_depth, task_timeout_s=120)
    scheduler = Scheduler(cooldown_heavy_s=0.5, cooldown_light_s=0.1)  # 테스트용 짧은 쿨다운
    timeouts = TimeoutPolicy()
    llm_client = LLMClient(adapter, health, circuit, max_retries=max_retries)
    router = Router()
    fallback = DegradedModeHandler()

    dispatcher = Dispatcher(
        llm_client=llm_client,
        queue=queue,
        scheduler=scheduler,
        timeouts=timeouts,
    )

    return dispatcher, router, fallback, health, circuit, queue


def make_request(domain: str, task_name: str, text: str = "test input") -> TaskRequest:
    return TaskRequest(domain=domain, task_name=task_name, user_input=text)


@dataclass
class LoadMetrics:
    """load 테스트 분리 통계"""
    submitted: int = 0
    full_success: int = 0
    cached_success: int = 0
    mock_success: int = 0
    rejected: int = 0
    failed: int = 0
    timeout: int = 0
    breaker_open: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    queue_waits_ms: list[int] = field(default_factory=list)

    def record(self, result):
        self.submitted += 1
        self.latencies_ms.append(result.latency_ms)
        self.queue_waits_ms.append(result.queue_wait_ms)

        status = result.status
        mode = result.fallback_mode

        if status == "done" and mode == "full":
            self.full_success += 1
        elif status == "degraded" and mode == "cached":
            self.cached_success += 1
        elif status == "degraded" and mode == "mock":
            self.mock_success += 1
        elif status == "shed":
            self.rejected += 1
        elif status == "error":
            if "Circuit" in str(result.errors):
                self.breaker_open += 1
            elif "timeout" in str(result.errors).lower():
                self.timeout += 1
            else:
                self.failed += 1

    @property
    def total_completed(self) -> int:
        return self.full_success + self.cached_success + self.mock_success

    @property
    def non_fatal_rate(self) -> float:
        return self.total_completed / self.submitted if self.submitted else 0

    @property
    def full_success_rate(self) -> float:
        return self.full_success / self.submitted if self.submitted else 0

    @property
    def p50_ms(self) -> int:
        return int(statistics.median(self.latencies_ms)) if self.latencies_ms else 0

    @property
    def p95_ms(self) -> int:
        if not self.latencies_ms:
            return 0
        s = sorted(self.latencies_ms)
        return s[int(len(s) * 0.95)]

    def report(self) -> str:
        lines = [
            f"  submitted:        {self.submitted}",
            f"  full_success:     {self.full_success}  ({self.full_success_rate:.1%})",
            f"  cached_success:   {self.cached_success}",
            f"  mock_success:     {self.mock_success}",
            f"  rejected:         {self.rejected}",
            f"  failed:           {self.failed}",
            f"  timeout:          {self.timeout}",
            f"  breaker_open:     {self.breaker_open}",
            f"  non_fatal_rate:   {self.non_fatal_rate:.1%}",
            f"  p50:              {self.p50_ms}ms",
            f"  p95:              {self.p95_ms}ms",
        ]
        return "\n".join(lines)
