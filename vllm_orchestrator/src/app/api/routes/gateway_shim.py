"""gateway_shim.py — Compatibility shim for runtime_llm_gateway envelope format.

Builder app's scene_chat_service sends POST /api/gateway/process with:
  {
    "task_type": "builder.scene_chat",
    "project_id": "...",
    "session_id": "...",
    "messages": [{"role":"user","content":"..."}, ...],
    "schema_id": "builder/scene_action_v1",
    "priority": "normal",
    "latency_budget_ms": 15000,
    "max_retries": 1,
    "extra_inputs": {}
  }

Response it expects:
  {
    "structured_content": {"actions": [...], "reply": "..."},
    "raw_text": "..."
  }

This shim converts envelope → orchestrator pipeline → compatible response.
"""
from __future__ import annotations

import time
from typing import Any
from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/gateway", tags=["gateway-shim"])

_orchestrated_pipeline = None


def init(pipeline):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


def _extract_user_input(messages: list[dict]) -> str:
    """Get the last user message content as user_input."""
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _build_history_context(messages: list[dict]) -> dict:
    """Keep conversation history in context for the pipeline."""
    history = [
        {"role": m.get("role", ""), "content": str(m.get("content", ""))}
        for m in (messages or [])[:-1]
        if m.get("role") in ("user", "assistant", "system")
    ]
    return {"conversation_history": history[-6:]} if history else {}


@router.post("/process")
def gateway_process(body: dict = Body(...)):
    """Compatibility endpoint: envelope → pipeline → structured response."""
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")

    messages = body.get("messages", [])
    user_input = _extract_user_input(messages)
    if not user_input.strip():
        raise HTTPException(400, "no user message in envelope")

    task_type = body.get("task_type", "")
    extra = body.get("extra_inputs", {}) or {}
    context = _build_history_context(messages)
    context.update(extra)

    # Force domain based on task_type prefix
    if task_type:
        prefix = task_type.split(".", 1)[0]
        if prefix in ("builder", "minecraft", "animation", "cad"):
            context["_force_domain"] = prefix
        # Force specific task
        parts = task_type.split(".", 1)
        if len(parts) == 2:
            context["_force_task"] = parts[1]

    t0 = time.perf_counter()
    result = _orchestrated_pipeline.execute(user_input, context)
    latency_ms = round((time.perf_counter() - t0) * 1000)
    result_dict = result.to_dict()

    slots = result_dict.get("output") or {}
    # Prefer task_result.slots if present
    try:
        if result.task_result and getattr(result.task_result, "slots", None):
            slots = result.task_result.slots or slots
    except Exception:
        pass

    # Build Builder-compatible response: structured_content must contain
    # {actions, reply} for scene_chat, or any other schema for other tasks.
    # If slots already has actions/reply, pass through. Otherwise wrap.
    structured: dict[str, Any]
    if isinstance(slots, dict) and ("actions" in slots or "reply" in slots):
        structured = {
            "actions": slots.get("actions", []),
            "reply": slots.get("reply", ""),
            **{k: v for k, v in slots.items() if k not in ("actions", "reply")},
        }
    else:
        # Wrap slots as structured content; actions empty, reply = summary
        structured = {
            "actions": [],
            "reply": slots.get("summary", "") if isinstance(slots, dict) else "",
            "slots": slots,
        }

    fail_loud = result_dict.get("fail_loud", False)
    raw_text = ""
    if fail_loud:
        raw_text = result_dict.get("fail_loud_reason", "")

    return {
        "structured_content": structured,
        "raw_text": raw_text,
        "request_id": body.get("project_id", "") + "_" + str(int(time.time() * 1000)),
        "task_type": task_type,
        "latency_ms": latency_ms,
        "validation": {
            "schema_ok": not fail_loud,
            "domain_ok": not fail_loud,
            "errors": [raw_text] if raw_text else [],
        },
    }


@router.get("/health")
def gateway_health():
    return {"status": "alive", "shim": "gateway_shim"}
