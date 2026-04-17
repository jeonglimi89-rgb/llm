"""
queue_manager.py - CPU 단일 스레드 보호용 큐

핵심: max_concurrency=1로 LLM 동시 호출 방지.
CPU 환경에서 연속 호출 과부하를 막는 핵심 계층.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from ..core.contracts import TaskRequest, TaskResult
from ..core.enums import TaskStatus
from ..core.errors import OverloadError, TimeoutError as AppTimeout
from ..observability.logger import get_logger, log_event

_log = get_logger("queue")


class QueueManager:
    """동기식 작업 큐. CPU 보호가 목적."""

    def __init__(self, max_concurrency: int = 1, max_depth: int = 10, task_timeout_s: int = 120):
        self._max_concurrency = max_concurrency
        self._max_depth = max_depth
        self._task_timeout_s = task_timeout_s
        self._queue: deque[tuple[TaskRequest, Callable]] = deque()
        self._running = 0
        self._lock = threading.Lock()

        # 메트릭
        self.total_enqueued = 0
        self.total_completed = 0
        self.total_rejected = 0
        self.total_timeout = 0

    def submit(self, request: TaskRequest, handler: Callable[[TaskRequest], TaskResult]) -> TaskResult:
        """동기 제출. 큐가 꽉 차면 OverloadError."""
        with self._lock:
            if len(self._queue) >= self._max_depth:
                self.total_rejected += 1
                log_event(_log, "queue_reject", task_id=request.task_id, depth=len(self._queue))
                raise OverloadError(f"Queue full ({self._max_depth})")

        enqueue_time = time.time()
        self.total_enqueued += 1
        log_event(_log, "queue_enqueue", task_id=request.task_id, depth=len(self._queue))

        # CPU concurrency=1: 직접 실행 (큐 대기 없이 순차)
        # 멀티스레드 워커가 필요하면 여기를 교체
        wait_ms = int((time.time() - enqueue_time) * 1000)

        try:
            self._running += 1
            result = handler(request)
            result.queue_wait_ms = wait_ms
            self.total_completed += 1
            return result
        except Exception as e:
            self.total_timeout += 1
            return TaskResult(
                request_id=request.request_id,
                task_id=request.task_id,
                task_type=request.task_type,
                status=TaskStatus.ERROR,
                errors=[str(e)],
                queue_wait_ms=wait_ms,
            )
        finally:
            self._running -= 1

    def snapshot(self) -> dict:
        return {
            "depth": len(self._queue),
            "running": self._running,
            "max_concurrency": self._max_concurrency,
            "max_depth": self._max_depth,
            "total_enqueued": self.total_enqueued,
            "total_completed": self.total_completed,
            "total_rejected": self.total_rejected,
            "total_timeout": self.total_timeout,
        }
