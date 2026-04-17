"""builder.py — Builder AI 전용 엔드포인트

외부 도면 생성, 내부 도면 생성, 외부-내부 정합성 검증.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/builder", tags=["builder"])

_orchestrated_pipeline = None


def init(pipeline):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


def _execute(user_input: str, context: dict) -> tuple[dict, int]:
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")
    if not user_input.strip():
        raise HTTPException(400, "user_input is required")
    t0 = time.perf_counter()
    result = _orchestrated_pipeline.execute(user_input, context)
    latency_ms = round((time.perf_counter() - t0) * 1000)
    return result.to_dict(), latency_ms


@router.post("/exterior")
def generate_exterior(body: dict = Body(...)):
    """자연어 → 건물 외부 도면 생성.

    Request:
        { "user_input": "2층 모던 주택 외관 설계", "context": {} }
    Response:
        { "plan": {...}, "metadata": {...} }
    """
    result_dict, latency_ms = _execute(
        body.get("user_input", ""),
        {**body.get("context", {}), "_force_domain": "builder"},
    )
    slots = result_dict.get("output") or {}
    return {
        "plan": slots,
        "metadata": {
            "domain": "builder",
            "task_family": "exterior_drawing",
            "latency_ms": latency_ms,
            "evaluation": result_dict.get("evaluation"),
            "fail_loud": result_dict.get("fail_loud", False),
            "fail_loud_reason": result_dict.get("fail_loud_reason", ""),
            "warnings": result_dict.get("schema_validation", {}).get("issues", []) if result_dict.get("schema_validation") else [],
        },
    }


@router.post("/interior")
def generate_interior(body: dict = Body(...)):
    """자연어 → 건물 내부 도면 생성.

    Request:
        { "user_input": "거실 25평, 주방 12평, 침실 2개", "floors": 2, "context": {} }
    """
    user_input = body.get("user_input", "")
    floors = body.get("floors")
    rooms = body.get("rooms")

    if floors:
        user_input = f"{floors}층 {user_input}"
    if rooms:
        user_input = f"{user_input} 방 구성: {json.dumps(rooms, ensure_ascii=False)}"

    result_dict, latency_ms = _execute(
        user_input,
        {**body.get("context", {}), "_force_domain": "builder"},
    )
    slots = result_dict.get("output") or {}
    return {
        "plan": slots,
        "metadata": {
            "domain": "builder",
            "task_family": "interior_drawing",
            "latency_ms": latency_ms,
            "evaluation": result_dict.get("evaluation"),
            "fail_loud": result_dict.get("fail_loud", False),
            "fail_loud_reason": result_dict.get("fail_loud_reason", ""),
        },
    }


@router.post("/plan")
def generate_plan(body: dict = Body(...)):
    """자연어 → 전체 건축 계획 (외부 + 내부).

    Request:
        { "user_input": "2층 단독주택 설계해줘", "context": {} }
    """
    result_dict, latency_ms = _execute(
        body.get("user_input", ""),
        {**body.get("context", {}), "_force_domain": "builder"},
    )
    slots = result_dict.get("output") or {}
    return {
        "plan": slots,
        "variant_plan": result_dict.get("variant_plan"),
        "command_graph": result_dict.get("command_graph"),
        "metadata": {
            "domain": "builder",
            "latency_ms": latency_ms,
            "evaluation": result_dict.get("evaluation"),
            "fail_loud": result_dict.get("fail_loud", False),
            "fail_loud_reason": result_dict.get("fail_loud_reason", ""),
        },
    }
