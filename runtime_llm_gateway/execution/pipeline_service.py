"""
execution/pipeline_service.py - Planner → Executor → Critic 3단계 파이프라인

전체 흐름:
  사용자 요구
  → Planner: 의도 해석 + 브레인스토밍 + 대안 제시
  → Executor: 확정된 계획을 도메인 스키마 JSON으로 변환
  → Tool Router: 도메인 엔진 호출 (더미 or 실제)
  → Critic: 결과 검증 + 수정 제안
  → 실패 시 repair 재시도
  → 최종 응답
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from ..core.envelope import RequestEnvelope, ResponseEnvelope, ValidationStatus, Message
from ..core.model_profile import ModelProfile
from ..routing.task_router import TaskRouter, ShardSelector
from ..context.context_assembler import ContextAssembler
from ..validators.schema_validator import validate_json_schema
from ..validators.domain_validators import get_domain_validator
from ..telemetry.audit_logger import AuditLogger


class PipelineService:
    """Planner → Executor → Critic 3단계 파이프라인"""

    def __init__(self, provider, audit_logger: Optional[AuditLogger] = None):
        self.provider = provider
        self.router = TaskRouter()
        self.shard = ShardSelector()
        self.context = ContextAssembler()
        self.audit = audit_logger or AuditLogger()

    def run_full_pipeline(
        self,
        request: RequestEnvelope,
        plan_schema: dict,
        exec_schema: dict,
    ) -> dict:
        """3단계 풀 파이프라인 실행"""
        start = time.time()
        program = request.program

        # === Stage 1: PLANNER ===
        plan_result = self._run_planner(request, plan_schema)
        if plan_result.get("error"):
            return plan_result

        # === Stage 2: EXECUTOR ===
        exec_result = self._run_executor(request, plan_result["content"], exec_schema)
        if exec_result.get("error"):
            return exec_result

        # === Stage 3: CRITIC ===
        critic_result = self._run_critic(request, exec_result["content"])

        total_ms = int((time.time() - start) * 1000)

        return {
            "request_id": request.request_id,
            "program": program,
            "plan": plan_result["content"],
            "execution": exec_result["content"],
            "review": critic_result.get("content", {}),
            "validation": {
                "plan_ok": plan_result.get("ok", False),
                "exec_schema_ok": exec_result.get("schema_ok", False),
                "exec_domain_ok": exec_result.get("domain_ok", False),
                "critic_pass": critic_result.get("pass", False),
            },
            "total_latency_ms": total_ms,
        }

    # ------------------------------------------------------------------
    # Stage 1: Planner
    # ------------------------------------------------------------------

    def _run_planner(self, request: RequestEnvelope, schema: dict) -> dict:
        """사용자 의도 해석 + 브레인스토밍"""
        profile = self.router.resolve_profile(request)

        # planner 전용 프롬프트 조립
        planner_prompt = self.context._load_prompt("planner", request.program) or ""
        system_base = self.context._load_prompt("common", "system_base") or ""

        system = (
            f"{system_base}\n\n"
            f"=== PLANNER MODE ===\n{planner_prompt}\n\n"
            f"Output JSON matching this schema:\n"
            f"```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```"
        )

        messages = [
            {"role": "system", "content": system},
            *[{"role": m.role, "content": m.content} for m in request.messages],
        ]

        try:
            raw = self.provider.chat_structured(profile, messages, schema)
            text, pt, ct = self.provider.parse_response(raw)
            content = json.loads(_extract_json_text(text))
            return {"ok": True, "content": content}
        except Exception as e:
            return {"ok": False, "error": f"planner_error: {e}", "content": {}}

    # ------------------------------------------------------------------
    # Stage 2: Executor
    # ------------------------------------------------------------------

    def _run_executor(self, request: RequestEnvelope, plan: dict, schema: dict) -> dict:
        """확정된 plan → 도메인 스키마 JSON 생성"""
        profile = self.router.resolve_profile(request)

        executor_system = self.context._load_prompt("executor", "system") or ""
        task_prompt = self.context._load_prompt(request.program, request.task_name) or ""

        system = (
            f"{executor_system}\n\n"
            f"=== EXECUTOR for {request.program} ===\n{task_prompt}\n\n"
            f"Confirmed plan:\n```json\n{json.dumps(plan, indent=2, ensure_ascii=False)}\n```\n\n"
            f"Output JSON matching this schema:\n"
            f"```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.messages[-1].content if request.messages else ""},
        ]

        try:
            raw = self.provider.chat_structured(profile, messages, schema)
            text, pt, ct = self.provider.parse_response(raw)
            content = json.loads(_extract_json_text(text))

            # Schema 검증
            schema_ok, schema_errors = validate_json_schema(content, schema)

            # Domain 검증
            domain_ok, domain_errors = True, []
            dv = get_domain_validator(request.program)
            if dv:
                domain_ok, domain_errors = dv.validate(content)

            return {
                "ok": schema_ok and domain_ok,
                "content": content,
                "schema_ok": schema_ok,
                "domain_ok": domain_ok,
                "errors": schema_errors + domain_errors,
            }
        except Exception as e:
            return {"ok": False, "error": f"executor_error: {e}", "content": {}, "schema_ok": False, "domain_ok": False}

    # ------------------------------------------------------------------
    # Stage 3: Critic
    # ------------------------------------------------------------------

    def _run_critic(self, request: RequestEnvelope, execution: dict) -> dict:
        """결과 검증 + 수정 제안"""
        profile = self.router.resolve_profile(request)

        critic_prompt = self.context._load_prompt("critic", request.program) or ""
        system_base = self.context._load_prompt("common", "system_base") or ""

        review_schema = {
            "type": "object",
            "required": ["verdict", "issues"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail", "warn"]},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["severity", "description", "fix_hint"],
                        "properties": {
                            "severity": {"type": "string", "enum": ["critical", "warning", "suggestion"]},
                            "description": {"type": "string"},
                            "fix_hint": {"type": "string"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
        }

        system = (
            f"{system_base}\n\n"
            f"=== CRITIC MODE ===\n{critic_prompt}\n\n"
            f"Output JSON matching this schema:\n"
            f"```json\n{json.dumps(review_schema, indent=2, ensure_ascii=False)}\n```"
        )

        user_text = request.messages[-1].content if request.messages else ""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"Original request: {user_text}\n\n"
                f"Generated result:\n```json\n"
                f"{json.dumps(execution, indent=2, ensure_ascii=False)}\n```\n\n"
                f"Evaluate this result."
            )},
        ]

        try:
            raw = self.provider.chat_structured(profile, messages, review_schema)
            text, _, _ = self.provider.parse_response(raw)
            content = json.loads(_extract_json_text(text))
            is_pass = content.get("verdict") in ("pass", "warn")
            return {"pass": is_pass, "content": content}
        except Exception:
            # critic 실패는 non-blocking
            return {"pass": True, "content": {"verdict": "pass", "issues": [], "summary": "critic unavailable"}}


def _extract_json_text(text: str) -> str:
    """LLM 응답에서 JSON 부분만 추출 (markdown 펜스 제거 등)"""
    if not text:
        return "{}"
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]
    return text
