"""
execution/gateway_service.py - Runtime LLM Gateway 핵심 파이프라인

전체 흐름:
  RequestEnvelope 수신
  → TaskRouter로 ModelProfile 선택
  → ShardSelector로 shard 결정
  → ContextAssembler로 프롬프트 조립
  → SchemaRegistry에서 스키마 로드
  → Provider로 LLM 호출
  → JSON 파싱
  → Schema 검증
  → Domain 검증
  → 실패 시 repair 재시도 (1회)
  → ResponseEnvelope 반환
  → 감사 로그 + 메트릭 기록
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from .output_stabilizer import stabilize_output

from ..core.envelope import RequestEnvelope, ResponseEnvelope, ValidationStatus
from ..core.model_profile import ModelProfile
from ..routing.task_router import TaskRouter, ShardSelector
from ..context.context_assembler import ContextAssembler
from ..validators.schema_validator import validate_json_schema
from ..validators.domain_validators import get_domain_validator
from ..telemetry.audit_logger import AuditLogger


_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class RuntimeGatewayService:
    """4개 프로그램 공통 LLM Gateway"""

    def __init__(
        self,
        provider,
        router: Optional[TaskRouter] = None,
        shard_selector: Optional[ShardSelector] = None,
        context_assembler: Optional[ContextAssembler] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.provider = provider
        self.router = router or TaskRouter()
        self.shard_selector = shard_selector or ShardSelector()
        self.context = context_assembler or ContextAssembler()
        self.audit = audit_logger or AuditLogger()

    def process(self, request: RequestEnvelope) -> ResponseEnvelope:
        """메인 파이프라인"""
        start = time.time()

        # 1. 라우팅
        profile = self.router.resolve_profile(request)
        shard = self.shard_selector.select(request.project_id, request.session_id, profile.pool_name)

        # 2. 스키마 로드
        schema = self._load_schema(request.schema_id)

        # 3. 프롬프트 조립
        messages = self.context.build_messages(request, schema)

        # 4. LLM 호출
        try:
            if profile.structured_only:
                raw_response = self.provider.chat_structured(profile, messages, schema)
            else:
                raw_response = self.provider.chat_freeform(profile, messages)
        except Exception as e:
            return self._error_response(request, profile, shard, start, "PROVIDER_ERROR", str(e))

        raw_text, prompt_tokens, completion_tokens = self.provider.parse_response(raw_response)

        # 5. 출력 안정화 (extract → syntax repair → schema-aware repair)
        parsed = None
        if profile.structured_only:
            parsed, cleaned_text, repair_log = stabilize_output(raw_text, schema)
            if parsed is None:
                # 안정화 실패 → LLM repair 시도
                if profile.enable_repair and request.max_retries > 0:
                    return self._repair_and_retry(
                        request, profile, shard, messages, raw_text, schema,
                        repair_log, start, prompt_tokens, completion_tokens
                    )
                return self._error_response(request, profile, shard, start, "SCHEMA_PARSE_ERROR", "; ".join(repair_log))
        else:
            # freeform: text만 반환
            resp = ResponseEnvelope(
                request_id=request.request_id,
                task_type=request.task_type,
                provider=self.provider.provider_name,
                model_profile=profile.profile_id,
                resolved_model=profile.resolved_model,
                raw_text=raw_text,
                validation=ValidationStatus(schema_ok=True, domain_ok=True),
                latency_ms=int((time.time() - start) * 1000),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                route_shard=shard,
            )
            self.audit.log(request, resp)
            return resp

        # 6. Schema 검증
        schema_ok, schema_errors = validate_json_schema(parsed, schema)

        if not schema_ok and profile.enable_repair and request.max_retries > 0:
            return self._repair_and_retry(
                request, profile, shard, messages, raw_text, schema,
                schema_errors, start, prompt_tokens, completion_tokens
            )

        # 7. Domain 검증
        domain_ok = True
        domain_errors: list[str] = []
        domain_validator = get_domain_validator(request.program)
        if domain_validator and parsed:
            domain_ok, domain_errors = domain_validator.validate(parsed)

        if not domain_ok and profile.enable_repair and request.max_retries > 0:
            all_errors = schema_errors + domain_errors
            return self._repair_and_retry(
                request, profile, shard, messages, raw_text, schema,
                all_errors, start, prompt_tokens, completion_tokens
            )

        # 8. 성공 응답
        validation = ValidationStatus(
            schema_ok=schema_ok,
            domain_ok=domain_ok,
            errors=schema_errors + domain_errors,
        )

        resp = ResponseEnvelope(
            request_id=request.request_id,
            task_type=request.task_type,
            provider=self.provider.provider_name,
            model_profile=profile.profile_id,
            resolved_model=profile.resolved_model,
            structured_content=parsed,
            raw_text=raw_text,
            validation=validation,
            latency_ms=int((time.time() - start) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            route_shard=shard,
        )

        self.audit.log(request, resp)
        return resp

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def _repair_and_retry(
        self,
        request: RequestEnvelope,
        profile: ModelProfile,
        shard: str,
        original_messages: list[dict],
        raw_text: str,
        schema: dict,
        errors: list[str],
        start_time: float,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
    ) -> ResponseEnvelope:
        """1회 repair 재시도"""
        repair_messages = self.context.build_repair_messages(
            original_messages, raw_text, errors, schema
        )

        try:
            raw_response = self.provider.chat_structured(profile, repair_messages, schema)
        except Exception as e:
            return self._error_response(request, profile, shard, start_time, "REPAIR_PROVIDER_ERROR", str(e))

        repair_text, pt, ct = self.provider.parse_response(raw_response)

        parsed, _, repair_log2 = stabilize_output(repair_text, schema)
        if parsed is None:
            return self._error_response(
                request, profile, shard, start_time, "REPAIR_PARSE_ERROR",
                f"Repair also failed: {'; '.join(repair_log2)}",
            )

        schema_ok, schema_errors = validate_json_schema(parsed, schema)
        domain_ok, domain_errors = True, []
        dv = get_domain_validator(request.program)
        if dv and parsed:
            domain_ok, domain_errors = dv.validate(parsed)

        validation = ValidationStatus(
            schema_ok=schema_ok,
            domain_ok=domain_ok,
            repair_attempted=True,
            repair_success=schema_ok and domain_ok,
            errors=schema_errors + domain_errors,
        )

        resp = ResponseEnvelope(
            request_id=request.request_id,
            task_type=request.task_type,
            provider=self.provider.provider_name,
            model_profile=profile.profile_id,
            resolved_model=profile.resolved_model,
            structured_content=parsed if (schema_ok and domain_ok) else None,
            raw_text=repair_text,
            validation=validation,
            latency_ms=int((time.time() - start_time) * 1000),
            prompt_tokens=(prompt_tokens or 0) + (pt or 0),
            completion_tokens=(completion_tokens or 0) + (ct or 0),
            route_shard=shard,
        )

        self.audit.log(request, resp)
        return resp

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def _load_schema(self, schema_id: str) -> dict:
        """schemas/{schema_id}.schema.json 로드"""
        path = _SCHEMA_DIR / f"{schema_id}.schema.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        # fallback: minimal schema
        return {"type": "object"}

    def _error_response(
        self,
        request: RequestEnvelope,
        profile: ModelProfile,
        shard: str,
        start_time: float,
        error_code: str,
        error_message: str,
    ) -> ResponseEnvelope:
        resp = ResponseEnvelope(
            request_id=request.request_id,
            task_type=request.task_type,
            provider=self.provider.provider_name,
            model_profile=profile.profile_id,
            resolved_model=profile.resolved_model,
            validation=ValidationStatus(),
            latency_ms=int((time.time() - start_time) * 1000),
            route_shard=shard,
            error_code=error_code,
            error_message=error_message,
        )
        self.audit.log(request, resp)
        return resp


# ---------------------------------------------------------------------------
# JSON 추출 유틸
# ---------------------------------------------------------------------------

import re as _re

def _extract_json(text: str) -> str:
    """LLM 응답에서 JSON 부분만 추출.

    처리 케이스:
    - 순수 JSON: {"key": "value"}
    - Markdown 펜스: ```json\\n{...}\\n```
    - 앞뒤 설명 텍스트 + JSON
    """
    if not text:
        raise ValueError("Empty text")

    text = text.strip()

    # 1. markdown 펜스 제거
    if "```" in text:
        match = _re.search(r"```(?:json)?\s*\n?(.*?)```", text, _re.DOTALL)
        if match:
            text = match.group(1).strip()

    # 2. 순수 JSON 시도
    if text.startswith("{") or text.startswith("["):
        return text

    # 3. 텍스트 안에 JSON 객체 찾기
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]

    # 4. 배열 찾기
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return text[start:end]

    raise ValueError(f"No JSON found in: {text[:100]}...")
