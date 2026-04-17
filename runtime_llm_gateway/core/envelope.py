"""
core/envelope.py - 요청/응답 표준 봉투

모든 Gateway 통신은 이 봉투를 통해 이루어진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4
from datetime import datetime, UTC


@dataclass
class Message:
    role: str  # system, user, assistant
    content: str


@dataclass
class RequestEnvelope:
    """Gateway 수신 표준 요청"""
    task_type: str                                # e.g. "builder.requirement_parse"
    project_id: str
    session_id: str
    messages: list[Message]
    schema_id: str                                # e.g. "builder.requirement_v1"

    request_id: str = field(default_factory=lambda: f"req_{uuid4().hex[:12]}")
    user_id: Optional[str] = None
    priority: str = "normal"                      # low, normal, high
    latency_budget_ms: int = 2500
    max_retries: int = 1
    context_flags: dict[str, bool] = field(default_factory=dict)
    extra_inputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def program(self) -> str:
        """task_type에서 프로그램명 추출: 'builder.requirement_parse' -> 'builder'"""
        return self.task_type.split(".")[0] if "." in self.task_type else self.task_type

    @property
    def task_name(self) -> str:
        """task_type에서 태스크명 추출: 'builder.requirement_parse' -> 'requirement_parse'"""
        parts = self.task_type.split(".", 1)
        return parts[1] if len(parts) > 1 else parts[0]

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_type": self.task_type,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "schema_id": self.schema_id,
            "priority": self.priority,
            "latency_budget_ms": self.latency_budget_ms,
            "max_retries": self.max_retries,
            "context_flags": self.context_flags,
            "extra_inputs": self.extra_inputs,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RequestEnvelope:
        msgs = [Message(**m) for m in d.get("messages", [])]
        return cls(
            request_id=d.get("request_id", f"req_{uuid4().hex[:12]}"),
            task_type=d["task_type"],
            project_id=d["project_id"],
            session_id=d["session_id"],
            user_id=d.get("user_id"),
            messages=msgs,
            schema_id=d["schema_id"],
            priority=d.get("priority", "normal"),
            latency_budget_ms=d.get("latency_budget_ms", 2500),
            max_retries=d.get("max_retries", 1),
            context_flags=d.get("context_flags", {}),
            extra_inputs=d.get("extra_inputs", {}),
            metadata=d.get("metadata", {}),
        )


@dataclass
class ValidationStatus:
    schema_ok: bool = False
    domain_ok: bool = False
    repair_attempted: bool = False
    repair_success: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_ok": self.schema_ok,
            "domain_ok": self.domain_ok,
            "repair_attempted": self.repair_attempted,
            "repair_success": self.repair_success,
            "errors": self.errors,
        }


@dataclass
class ResponseEnvelope:
    """Gateway 응답 표준 봉투"""
    request_id: str
    task_type: str
    provider: str                    # "vllm"
    model_profile: str               # "strict-json-pool"
    resolved_model: str              # 실제 모델명

    structured_content: Optional[dict[str, Any]] = None
    raw_text: Optional[str] = None
    validation: ValidationStatus = field(default_factory=ValidationStatus)

    latency_ms: int = 0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    route_shard: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_type": self.task_type,
            "provider": self.provider,
            "model_profile": self.model_profile,
            "resolved_model": self.resolved_model,
            "structured_content": self.structured_content,
            "raw_text": self.raw_text,
            "validation": self.validation.to_dict(),
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "route_shard": self.route_shard,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @property
    def success(self) -> bool:
        return (
            self.error_code is None
            and self.validation.schema_ok
            and self.validation.domain_ok
        )
