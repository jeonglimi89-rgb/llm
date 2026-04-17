"""cad.py — CAD AI 전용 엔드포인트

설계도 생성, 조립성 검증, 제조 가능성 검증.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/cad", tags=["cad"])

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


@router.post("/design")
def generate_design(body: dict = Body(...)):
    """자연어 → 설계도 생성.

    Request:
        { "user_input": "방수 IP67 샤워 필터 설계", "context": {} }
    Response:
        { "design": {...constraints, systems, parts...}, "metadata": {...} }
    """
    result_dict, latency_ms = _execute(
        body.get("user_input", ""),
        {**body.get("context", {}), "_force_domain": "cad"},
    )
    slots = result_dict.get("output") or {}
    return {
        "design": slots,
        "variant_plan": result_dict.get("variant_plan"),
        "command_graph": result_dict.get("command_graph"),
        "metadata": {
            "domain": "cad",
            "task_family": "design_drawing",
            "latency_ms": latency_ms,
            "evaluation": result_dict.get("evaluation"),
            "fail_loud": result_dict.get("fail_loud", False),
            "fail_loud_reason": result_dict.get("fail_loud_reason", ""),
        },
    }


@router.post("/constraint")
def parse_constraints(body: dict = Body(...)):
    """제약 조건 파싱.

    Request:
        { "user_input": "IP67 방수, USB-C 충전, 80x80x200mm", "context": {} }
    """
    result_dict, latency_ms = _execute(
        body.get("user_input", ""),
        {**body.get("context", {}), "_force_domain": "cad"},
    )
    slots = result_dict.get("output") or {}
    return {
        "constraints": slots.get("constraints", slots),
        "metadata": {
            "domain": "cad",
            "task_family": "design_drawing",
            "latency_ms": latency_ms,
            "evaluation": result_dict.get("evaluation"),
            "fail_loud": result_dict.get("fail_loud", False),
        },
    }


@router.post("/validate")
def validate_design(body: dict = Body(...)):
    """설계 검증 (조립성 + 제조 가능성).

    Request:
        { "design": {...기존 설계 결과...}, "check_types": ["assembly", "manufacturability"] }
    """
    design = body.get("design", {})
    check_types = body.get("check_types", ["assembly", "manufacturability"])

    user_input = f"Validate this design: {json.dumps(design, ensure_ascii=False)}\nChecks: {', '.join(check_types)}"
    result_dict, latency_ms = _execute(
        user_input,
        {**body.get("context", {}), "_force_domain": "cad"},
    )
    slots = result_dict.get("output") or {}
    return {
        "validation": slots,
        "metadata": {
            "domain": "cad",
            "latency_ms": latency_ms,
            "fail_loud": result_dict.get("fail_loud", False),
        },
    }
