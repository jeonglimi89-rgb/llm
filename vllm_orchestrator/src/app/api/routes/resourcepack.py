"""resourcepack.py - POST /resourcepack/generate

마인크래프트 리소스팩 스타일 생성 엔드포인트.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/resourcepack", tags=["resourcepack"])

_orchestrated_pipeline = None


def init(pipeline):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


@router.post("/generate")
def generate_style(body: dict = Body(...)):
    """자연어 → 리소스팩 스타일 정의.

    Request:
        { "user_input": "이끼 낀 중세 폐허 느낌", "context": {} }

    Response:
        {
            "style": { "name": "...", "palette": [...], "textures": [...] },
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
        "style": slots,
        "metadata": {
            "domain": "resourcepack",
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
        },
    }
