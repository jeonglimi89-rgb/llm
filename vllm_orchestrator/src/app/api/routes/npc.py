"""npc.py - POST /npc/generate, POST /npc/dialogue

NPC 캐릭터 생성 엔드포인트.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/npc", tags=["npc"])

_orchestrated_pipeline = None


def init(pipeline):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


@router.post("/generate")
def generate_character(body: dict = Body(...)):
    """자연어 → NPC 캐릭터 정의.

    Request:
        { "user_input": "수상한 약초 상인", "context": {} }

    Response:
        {
            "character": { "name": "...", "role": "...", ... },
            "metadata": { ... }
        }
    """
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")

    user_input = body.get("user_input", "")
    if not user_input.strip():
        raise HTTPException(400, "user_input is required")

    context = body.get("context", {})

    t0 = time.perf_counter()
    result = _orchestrated_pipeline.execute(user_input, context)
    latency_ms = round((time.perf_counter() - t0) * 1000)

    result_dict = result.to_dict()
    slots = result_dict.get("slots") or {}

    return {
        "character": slots,
        "metadata": {
            "domain": "npc",
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
        },
    }


@router.post("/dialogue")
def generate_dialogue(body: dict = Body(...)):
    """기존 캐릭터 기반 대사 생성.

    Request:
        {
            "user_input": "전투 중 대사를 만들어줘",
            "character_summary": { "name": "...", "role": "...", "personality": "..." },
            "context": {}
        }
    """
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")

    user_input = body.get("user_input", "")
    character = body.get("character_summary", {})
    context = body.get("context", {})

    import json
    enriched = user_input
    if character:
        enriched = f"Character: {json.dumps(character, ensure_ascii=False)}\nRequest: {user_input}"

    t0 = time.perf_counter()
    result = _orchestrated_pipeline.execute(enriched, context)
    latency_ms = round((time.perf_counter() - t0) * 1000)

    result_dict = result.to_dict()
    slots = result_dict.get("slots") or {}

    return {
        "dialogue": slots.get("dialogue", []),
        "context": slots.get("context", ""),
        "metadata": {
            "domain": "npc",
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
        },
    }
