"""stream.py — Server-Sent Events endpoint for progressive task results.

POST /tasks/submit/stream — streams the pipeline phases as SSE events:
  event: cache_check      → {"hit": bool, "stats": {...}}
  event: intent_analyzed  → intent_report (creative_demand, variant_count, ...)
  event: llm_start        → {"variant_count": N, "phase": "generation"}
  event: llm_done         → {"latency_ms": ..., "variant_count": N, "nodes": N}
  event: critic_done      → critic_report (quality, issues, repair_needed)
  event: repair_applied   → {"reason": "...", "repair_hint": "..."}  (optional)
  event: final            → full TaskResult.to_dict()
  event: error            → {"message": "..."}

SSE 포맷: `event: <name>\ndata: <json>\n\n`
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Body, HTTPException
from starlette.responses import StreamingResponse

from ...core.contracts import TaskRequest
from ...core.errors import ValidationError


router = APIRouter(prefix="/tasks", tags=["tasks-stream"])


# Container injection (main.py 에서 설정)
_orchestration_router = None
_dispatcher = None
_fallback = None


def init(orch_router, dispatcher, fallback):
    global _orchestration_router, _dispatcher, _fallback
    _orchestration_router = orch_router
    _dispatcher = dispatcher
    _fallback = fallback


def _sse_event(event: str, data: Any) -> str:
    """Format one SSE event."""
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        payload = json.dumps({"_error": "serialization_failed"})
    return f"event: {event}\ndata: {payload}\n\n"


async def _run_pipeline_async(body: dict) -> AsyncGenerator[str, None]:
    """Run dispatcher.dispatch() in a thread and emit progressive SSE events
    by tailing the orchestrator log (or via in-memory callback).

    Simpler approach for MVP: emit structural events derived from the final
    result. This gives user visibility into which phases fired even though
    they all return at the end. Upgrade path: callback-based streaming
    would require LLMClient/Dispatcher refactoring.
    """
    t0 = time.time()

    if _orchestration_router is None or _dispatcher is None:
        yield _sse_event("error", {"message": "pipeline not initialized"})
        return

    raw_context = body.get("context", {}) or {}
    user_input = body.get("user_input", "")

    # Compose context hints into user message (parity with /tasks/submit)
    from .tasks import _compose_user_input_with_context
    composed_input = _compose_user_input_with_context(user_input, raw_context)

    request = TaskRequest(
        domain=body.get("domain", ""),
        task_name=body.get("task_name", ""),
        user_input=composed_input,
        priority=body.get("priority", "normal"),
        session_id=body.get("session_id", ""),
        project_id=body.get("project_id", ""),
        context=raw_context,
    )

    # Phase 1: resolve spec
    try:
        spec = _orchestration_router.resolve(request)
    except ValidationError as e:
        yield _sse_event("error", {"message": e.message, "status_code": 400})
        return

    yield _sse_event("start", {
        "task_type": request.task_type,
        "task_id": request.task_id,
        "request_id": request.request_id,
    })

    # Phase 2: cache probe (fast, synchronous)
    cache_hit = False
    if _dispatcher.request_cache is not None:
        try:
            cached = _dispatcher.request_cache.get(
                request.task_type, request.user_input, request.context
            )
            if cached is not None:
                cache_hit = True
                yield _sse_event("cache_check", {
                    "hit": True,
                    "stats": _dispatcher.request_cache.stats_dict(),
                })
                # Return cached result as final
                cached_copy = dict(cached)
                cached_copy["request_id"] = request.request_id
                cached_copy["task_id"] = request.task_id
                cached_copy["cache_hit"] = True
                yield _sse_event("final", cached_copy)
                return
        except Exception:
            pass

    yield _sse_event("cache_check", {
        "hit": False,
        "stats": _dispatcher.request_cache.stats_dict() if _dispatcher.request_cache else {},
    })

    # Phase 3: intent analysis (synchronous preview for UX)
    try:
        from ..domain.intent_analyzer import analyze_intent, is_creative_task
        if is_creative_task(request.task_type):
            intent_report = analyze_intent(user_input)
            yield _sse_event("intent_analyzed", intent_report.to_dict())
    except Exception:
        pass

    # Phase 4: run full dispatch in a thread, stream back phase events afterward
    yield _sse_event("llm_start", {"phase": "generation"})

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _dispatcher.dispatch, request, spec)
    except Exception as e:
        yield _sse_event("error", {"message": f"dispatch failed: {e}"})
        return

    # Emit derived events from the result (variant → critic → repair → final)
    result_dict = result.to_dict() if hasattr(result, "to_dict") else {}

    variant_report = result_dict.get("variant_report")
    if variant_report:
        yield _sse_event("variant_sampling_done", {
            "variant_count": variant_report.get("variant_count"),
            "selected_family": variant_report.get("selected_family"),
            "scores": {
                v.get("family"): v.get("score")
                for v in (variant_report.get("variants") or [])
            },
            "total_wall_ms": variant_report.get("total_wall_ms"),
        })

    yield _sse_event("llm_done", {
        "latency_ms": result_dict.get("latency_ms"),
        "nodes": len((result_dict.get("slots") or {}).get("nodes") or []),
    })

    critic_report = result_dict.get("critic_report")
    if critic_report:
        yield _sse_event("critic_done", {
            "overall_quality": critic_report.get("overall_quality"),
            "repair_needed": critic_report.get("repair_needed"),
            "issues": critic_report.get("issues"),
            "repair_hint": critic_report.get("repair_hint"),
            "critic_latency_ms": critic_report.get("critic_latency_ms"),
        })

    if result_dict.get("repair_applied"):
        yield _sse_event("repair_applied", {
            "applied": True,
            "from_critic": bool(critic_report and critic_report.get("repair_needed")),
        })

    # Final: full result
    yield _sse_event("final", result_dict)


@router.post("/submit/stream")
async def submit_task_stream(body: dict = Body(...)):
    """Streaming variant of /tasks/submit. Returns text/event-stream.

    Client usage (JS):
        const resp = await fetch("/tasks/submit/stream", { method:"POST", ...})
        const reader = resp.body.getReader()
        // parse SSE: each event is `event: X\ndata: Y\n\n`
    """
    if not body.get("user_input", "").strip():
        raise HTTPException(400, "user_input is required")

    async def generator():
        async for chunk in _run_pipeline_async(body):
            yield chunk

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 비활성 힌트
        },
    )
