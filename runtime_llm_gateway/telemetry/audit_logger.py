"""
telemetry/audit_logger.py - 감사 로그 + 메트릭

모든 Gateway 요청/응답을 JSONL로 기록.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from ..core.envelope import RequestEnvelope, ResponseEnvelope


class AuditLogger:
    """JSONL 기반 감사 로그"""

    def __init__(self, log_dir: str | None = None):
        if log_dir is None:
            log_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "audit")
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._metrics: dict[str, int] = {
            "total": 0,
            "success": 0,
            "failure": 0,
            "repair_attempted": 0,
            "repair_success": 0,
        }
        self._task_metrics: dict[str, dict] = {}  # task_type별

    def log(self, request: RequestEnvelope, response: ResponseEnvelope) -> None:
        """감사 로그 1건 기록"""
        entry = {
            "request_id": request.request_id,
            "task_type": request.task_type,
            "project_id": request.project_id,
            "session_id": request.session_id,
            "model_profile": response.model_profile,
            "resolved_model": response.resolved_model,
            "route_shard": response.route_shard,
            "schema_ok": response.validation.schema_ok,
            "domain_ok": response.validation.domain_ok,
            "repair_attempted": response.validation.repair_attempted,
            "repair_success": response.validation.repair_success,
            "latency_ms": response.latency_ms,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "error_code": response.error_code,
            "created_at": datetime.now(UTC).isoformat(),
        }

        # 파일 기록
        log_file = os.path.join(self.log_dir, "gateway_audit.jsonl")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 메트릭 업데이트
        self._metrics["total"] += 1
        if response.success:
            self._metrics["success"] += 1
        else:
            self._metrics["failure"] += 1
        if response.validation.repair_attempted:
            self._metrics["repair_attempted"] += 1
        if response.validation.repair_success:
            self._metrics["repair_success"] += 1

        # task_type별 메트릭
        tt = request.task_type
        if tt not in self._task_metrics:
            self._task_metrics[tt] = {"count": 0, "total_latency_ms": 0, "failures": 0}
        tm = self._task_metrics[tt]
        tm["count"] += 1
        tm["total_latency_ms"] += response.latency_ms
        if not response.success:
            tm["failures"] += 1

    def get_metrics(self) -> dict[str, Any]:
        return {
            **self._metrics,
            "success_rate": (
                self._metrics["success"] / self._metrics["total"]
                if self._metrics["total"] > 0 else 0.0
            ),
        }

    def get_task_metrics(self) -> dict[str, dict]:
        """task_type별 메트릭 (Prometheus /metrics용)"""
        result = {}
        for tt, tm in self._task_metrics.items():
            result[tt] = {
                "count": tm["count"],
                "failures": tm["failures"],
                "avg_latency_ms": int(tm["total_latency_ms"] / tm["count"]) if tm["count"] else 0,
            }
        return result
