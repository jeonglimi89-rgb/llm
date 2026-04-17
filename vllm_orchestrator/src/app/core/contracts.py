"""contracts.py - TaskRequest / TaskResult dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4


@dataclass
class TaskRequest:
    domain: str = ""
    task_name: str = ""
    user_input: str = ""
    priority: str = "normal"
    session_id: str = ""
    project_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"req_{uuid4().hex[:12]}")
    task_id: str = field(default_factory=lambda: f"task_{uuid4().hex[:12]}")

    @property
    def task_type(self) -> str:
        return f"{self.domain}.{self.task_name}" if self.domain and self.task_name else (self.domain or self.task_name or "")


@dataclass
class TaskResult:
    """태스크 결과 표준.

    NOTE 2026-04-06: ``validated`` 의 의미가 강화됐다. 기존에는 dispatcher 가
    JSON 파싱 성공만으로 ``validated=True`` 를 박았으나, 이제는 layered review
    gate (review/task_contracts.evaluate_task_contract) 의 ``auto_validated``
    결과만이 True 가 될 수 있다. 동시에 ``layered_judgment`` 에 5개 게이트
    상세를 함께 노출한다.

    NOTE 2026-04-07 (T-tranche): per-call retry decision 과 health probe 결과를
    additive 로 노출한다. 기존 reader 는 깨지지 않는다.
    """
    request_id: str
    task_id: str
    task_type: str
    status: str = "done"                    # TaskStatus value
    fallback_mode: str = "full"             # FallbackMode value

    slots: Optional[dict[str, Any]] = None  # 추출된 슬롯
    raw_text: Optional[str] = None          # LLM 원문
    validated: bool = False                 # 강화: layered gate auto_validated
    layered_judgment: Optional[dict[str, Any]] = None  # 5게이트 상세 (review.layered)
    errors: list[str] = field(default_factory=list)

    latency_ms: int = 0
    queue_wait_ms: int = 0
    retries: int = 0
    # Additive — T-tranche 2026-04-07
    retry_decision: Optional[dict[str, Any]] = None      # LLMClient.RetryDecision dict
    health_probe_result: Optional[dict[str, Any]] = None  # adapter.HealthProbeResult dict
    # Cache layer (2026-04-16): True 면 LLM 호출 없이 cache에서 재구성됨
    cache_hit: bool = False
    # Self-critique layer (2026-04-16): LLM critic 결과 + repair 정보
    critic_report: Optional[dict[str, Any]] = None
    repair_applied: bool = False
    # Multi-variant sampling (2026-04-17): 3개 variant 생성 후 best 선택 시 기록
    variant_report: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status,
            "fallback_mode": self.fallback_mode,
            "slots": self.slots,
            "validated": self.validated,
            "layered_judgment": self.layered_judgment,
            "errors": self.errors,
            "latency_ms": self.latency_ms,
            "queue_wait_ms": self.queue_wait_ms,
            "retries": self.retries,
            "retry_decision": self.retry_decision,
            "health_probe_result": self.health_probe_result,
            "cache_hit": self.cache_hit,
            "critic_report": self.critic_report,
            "repair_applied": self.repair_applied,
            "variant_report": self.variant_report,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskResult":
        """Reconstruct TaskResult from its to_dict() output (for cache layer)."""
        return cls(
            request_id=d.get("request_id", ""),
            task_id=d.get("task_id", ""),
            task_type=d.get("task_type", ""),
            status=d.get("status", "done"),
            fallback_mode=d.get("fallback_mode", "full"),
            slots=d.get("slots"),
            raw_text=d.get("raw_text"),
            validated=bool(d.get("validated", False)),
            layered_judgment=d.get("layered_judgment"),
            errors=list(d.get("errors") or []),
            latency_ms=int(d.get("latency_ms") or 0),
            queue_wait_ms=int(d.get("queue_wait_ms") or 0),
            retries=int(d.get("retries") or 0),
            retry_decision=d.get("retry_decision"),
            health_probe_result=d.get("health_probe_result"),
            cache_hit=bool(d.get("cache_hit", False)),
            critic_report=d.get("critic_report"),
            repair_applied=bool(d.get("repair_applied", False)),
            variant_report=d.get("variant_report"),
        )
