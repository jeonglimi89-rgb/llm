"""tasks.py - POST /tasks/submit, POST /tasks/orchestrate"""
from __future__ import annotations

import json as _json

from fastapi import APIRouter, Body, HTTPException

from ...core.contracts import TaskRequest
from ...core.errors import ValidationError, OverloadError


def _compose_user_input_with_context(user_input: str, context: dict) -> str:
    """If context has meaningful hints, append them as a structured block so
    the LLM can see them through the user message. Safe/additive: existing
    tasks that don't consume context see only a trailing tagged block.

    Kept lightweight and deterministic (no nested structures expanded beyond
    one level). Called only from /tasks/submit.
    """
    if not isinstance(context, dict) or not context:
        return user_input
    # Flatten only scalar and simple list/dict values; cap length per value.
    hints: list[str] = []
    for k, v in context.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, (str, int, float, bool)):
            hints.append(f"{k}={v}")
        else:
            try:
                hints.append(f"{k}={_json.dumps(v, ensure_ascii=False)[:180]}")
            except Exception:
                hints.append(f"{k}={str(v)[:180]}")
    if not hints:
        return user_input
    return f"{user_input}\n\n[Context]\n" + "\n".join(hints)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# container에서 주입
_orchestration_router = None
_dispatcher = None
_fallback = None
_orchestrated_pipeline = None


def init(orch_router, dispatcher, fallback):
    global _orchestration_router, _dispatcher, _fallback
    _orchestration_router = orch_router
    _dispatcher = dispatcher
    _fallback = fallback


def init_orchestrated(pipeline):
    """OrchestratedPipeline 주입 (Container 에서 호출)."""
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


@router.post("/submit")
def submit_task(body: dict = Body(...)):
    """태스크 제출 → 동기 실행 → 결과 반환"""
    import time as _time
    t0 = _time.time()
    raw_context = body.get("context", {}) or {}
    composed_input = _compose_user_input_with_context(
        body.get("user_input", ""), raw_context
    )
    request = TaskRequest(
        domain=body.get("domain", ""),
        task_name=body.get("task_name", ""),
        user_input=composed_input,
        priority=body.get("priority", "normal"),
        session_id=body.get("session_id", ""),
        project_id=body.get("project_id", ""),
        context=raw_context,
    )

    try:
        spec = _orchestration_router.resolve(request)
    except ValidationError as e:
        raise HTTPException(400, e.message)

    result = _dispatcher.dispatch(request, spec)

    # 성공 시 fallback 캐시 갱신
    if result.slots and _fallback:
        _fallback.cache_good_result(request.task_type, result.slots)

    # Prometheus 요청-레벨 관측
    try:
        from ...observability.metrics import observe_request
        observe_request(
            request.task_type,
            str(result.status),
            _time.time() - t0,
            bool(getattr(result, "cache_hit", False)),
        )
    except Exception:
        pass

    return result.to_dict()


@router.post("/orchestrate")
def orchestrate_task(body: dict = Body(...)):
    """자연어 입력 → 도메인 분류 → 전문 처리 → 결과 반환.

    기존 /submit 과 달리 domain/task_name 을 사용자가 지정할 필요 없음.
    DomainClassifier 가 자동 분류하고 enriched prompt 로 실행.
    """
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")

    user_input = body.get("user_input", "")
    if not user_input.strip():
        raise HTTPException(400, "user_input is required")

    context = body.get("context", {})

    result = _orchestrated_pipeline.execute(user_input, context)
    return result.to_dict()
