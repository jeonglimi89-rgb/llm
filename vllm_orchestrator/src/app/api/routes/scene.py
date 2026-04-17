"""scene.py - POST /scene/generate — 3D Core 전용 엔드포인트

자연어 입력 → animation 도메인 오케스트레이션 → LLMShotGraph 호환 JSON 반환.

파이프라인:
  1. shot_parse (LLM) → 기본 슬롯 추출
  2. camera_intent_parse (LLM) → 카메라 의도
  3. lighting_intent_parse (LLM) → 조명 의도
  4. solve_shot (tool) → deterministic 파라미터 결정
  5. creative_direction (LLM, temp=0.7) → 창의적 연출 디테일
  6. to_shot_graph() → 최종 LLMShotGraph 조립 (creative 우선)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

from ...tools.adapters.shot_graph_adapter import to_shot_graph

router = APIRouter(prefix="/scene", tags=["scene"])

_orchestrated_pipeline = None


def init(pipeline):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


@router.post("/generate")
def generate_scene(body: dict = Body(...)):
    """자연어 → LLMShotGraph 변환.

    Request:
        { "user_input": "노을빛에 슬픈 클로즈업", "context": {} }

    Response:
        {
            "shot_graph": { ... LLMShotGraph ... },
            "metadata": { "domain": "animation", "latency_ms": 245, "warnings": [] }
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

    # creative_direction step 결과 추출 (체인에서 누적된 슬롯)
    creative = slots.get("creative_direction") or slots.get("animation.creative_direction")

    # solve_shot이 만든 기존 shot_graph가 있으면 재조립, 없으면 슬롯에서 생성
    # 어느 경우든 creative data를 최종 병합
    shot_graph = to_shot_graph(slots, creative=creative)

    warnings = result_dict.get("errors", [])
    domain = result_dict.get("task_type", "animation")

    return {
        "shot_graph": shot_graph,
        "metadata": {
            "domain": domain,
            "latency_ms": latency_ms,
            "warnings": warnings,
        },
    }
