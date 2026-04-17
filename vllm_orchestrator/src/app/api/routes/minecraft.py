"""minecraft.py — Minecraft LLM Active Orchestration 엔드포인트

빌드/편집/비평/수리 전 과정에서 LLM이 의사결정 중심.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Body, HTTPException

from ...tools.adapters.shot_graph_adapter import to_shot_graph

router = APIRouter(prefix="/minecraft", tags=["minecraft"])

_orchestrated_pipeline = None


def init(pipeline, orch_router=None, dispatcher=None):
    global _orchestrated_pipeline
    _orchestrated_pipeline = pipeline


def _execute_pipeline(user_input: str, context: dict) -> tuple[dict, int]:
    """공통 파이프라인 실행.

    orchestrated_pipeline 대신 chain_engine을 직접 호출하여
    최종 평가/수리 사이클이 build_planner slots을 덮어쓰지 않게 함.
    """
    if _orchestrated_pipeline is None:
        raise HTTPException(503, "OrchestratedPipeline not initialized")
    if not user_input.strip():
        raise HTTPException(400, "user_input is required")

    t0 = time.perf_counter()

    # 체인 엔진 직접 호출 (orchestrated_pipeline의 fail_loud/repair 사이클 우회)
    chain_engine = _orchestrated_pipeline._chain_engine
    chain_defs = _orchestrated_pipeline._chain_definitions
    chain_def = chain_defs.get("minecraft_llm_active_build")

    if chain_def is None:
        # 폴백: 전체 파이프라인
        result = _orchestrated_pipeline.execute(user_input, context)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        result_dict = result.to_dict()
        try:
            if result.task_result and getattr(result.task_result, "slots", None):
                result_dict["slots"] = result.task_result.slots
        except Exception:
            pass
        return result_dict, latency_ms

    chain_result = chain_engine.execute_chain(
        chain=chain_def, user_input=user_input, context=context, enrichment=None,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000)

    return {
        "status": "done" if chain_result.success else "error",
        "slots": chain_result.final_output or {},
        "task_type": "minecraft.chain_direct",
        "errors": [s.error for s in chain_result.steps_completed if s.error],
    }, latency_ms


@router.post("/plan")
def plan_build(body: dict = Body(...)):
    """LLM Build Planner — 자연어 → 구조화된 빌드 계획.

    LLM이 build_type, style, footprint, silhouette_strategy,
    material_hints, key_features, creative_notes를 직접 결정.
    """
    result_dict, latency_ms = _execute_pipeline(
        body.get("user_input", ""),
        {**body.get("context", {}), "_force_task": "build_planner"},
    )
    slots = result_dict.get("output") or result_dict.get("slots") or {}

    return {
        "plan": slots,
        "metadata": {
            "domain": "minecraft",
            "task_type": "build_planner",
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
        },
    }


@router.post("/variants")
def plan_variants(body: dict = Body(...)):
    """LLM Variant Planner — 기본 빌드 계획 → 3개 변형.

    각 variant는 silhouette_first / gameplay_first / decorative_first
    등 의미 있게 다른 전략.
    """
    base_plan = body.get("base_plan", {})
    enriched_input = f"Base plan: {json.dumps(base_plan, ensure_ascii=False)}\nCreate 3 meaningful variants."
    context = {**body.get("context", {}), "_force_task": "variant_planner"}

    result_dict, latency_ms = _execute_pipeline(enriched_input, context)
    slots = result_dict.get("output") or result_dict.get("slots") or {}

    return {
        "variants": slots.get("variants", []),
        "metadata": {"latency_ms": latency_ms, "warnings": result_dict.get("errors", [])},
    }


@router.post("/critique")
def critique_build(body: dict = Body(...)):
    """LLM Build Critic — 빌드 결과 비평.

    rubric 점수 + 블록 통계를 받아 구조화 critique 생성.
    """
    rubric = body.get("rubric_summary", "")
    user_intent = body.get("user_intent", "")
    block_stats = body.get("block_stats", {})

    enriched_input = (
        f"User intent: {user_intent}\n"
        f"Rubric scores:\n{rubric}\n"
        f"Block stats: {json.dumps(block_stats, ensure_ascii=False)}\n"
        f"Critique this build."
    )
    context = {**body.get("context", {}), "_force_task": "build_critic"}

    result_dict, latency_ms = _execute_pipeline(enriched_input, context)
    slots = result_dict.get("output") or result_dict.get("slots") or {}

    return {
        "critique": slots,
        "metadata": {"latency_ms": latency_ms, "warnings": result_dict.get("errors", [])},
    }


@router.post("/repair")
def plan_repair(body: dict = Body(...)):
    """LLM Repair Planner — critique 기반 수리 계획.

    critique 결과를 받아 구체적 repair step 생성.
    """
    critique = body.get("critique", {})
    enriched_input = (
        f"Critique results: {json.dumps(critique, ensure_ascii=False)}\n"
        f"Plan specific repair operations."
    )
    context = {**body.get("context", {}), "_force_task": "repair_planner"}

    result_dict, latency_ms = _execute_pipeline(enriched_input, context)
    slots = result_dict.get("output") or result_dict.get("slots") or {}

    return {
        "repair_plan": slots,
        "metadata": {"latency_ms": latency_ms, "warnings": result_dict.get("errors", [])},
    }


@router.post("/build")
def build_full(body: dict = Body(...)):
    """전체 빌드 파이프라인 — planner → variants → compile → critique → repair.

    한 번의 호출로 LLM 중심 전체 파이프라인 실행.
    """
    user_input = body.get("user_input", "")
    context = body.get("context", {})

    result_dict, latency_ms = _execute_pipeline(user_input, context)
    slots = result_dict.get("output") or result_dict.get("slots") or {}

    # BuildSpecV1 호환 정규화 (하위 호환)
    spec = _normalize_build_spec(slots)

    return {
        "spec": spec,
        "plan": slots,
        "metadata": {
            "domain": "minecraft",
            "task_type": result_dict.get("task_type", ""),
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
            "validated": result_dict.get("validated", False),
        },
    }


@router.post("/edit")
def edit_build(body: dict = Body(...)):
    """자연어 + 현재 빌드 요약 → EditSpecV1."""
    user_input = body.get("user_input", "")
    current_build = body.get("current_build_summary", {})
    context = body.get("context", {})

    enriched_input = user_input
    if current_build:
        enriched_input = f"Current build: {json.dumps(current_build, ensure_ascii=False)}\nEdit request: {user_input}"

    result_dict, latency_ms = _execute_pipeline(enriched_input, context)
    slots = result_dict.get("output") or result_dict.get("slots") or {}
    spec = _normalize_edit_spec(slots)

    return {
        "spec": spec,
        "metadata": {
            "domain": "minecraft",
            "task_type": result_dict.get("task_type", ""),
            "latency_ms": latency_ms,
            "warnings": result_dict.get("errors", []),
        },
    }


# ─── Normalization Helpers ───────────────────────────────────────────

_OPERATION_MAP = {
    "add": "emphasize", "remove": "de_emphasize",
    "enlarge": "scale_up", "shrink": "scale_down",
    "replace_material": "change_style", "increase_detail": "emphasize",
    "simplify": "de_emphasize", "raise": "scale_up",
    "lower": "scale_down", "extend": "scale_up", "mirror": "emphasize",
}

_ANCHOR_TO_TARGET = {
    "facade": "facade", "roof": "roofline", "interior": "ornament",
    "entrance": "facade", "window": "facade", "wall": "facade",
    "tower": "roofline", "garden": "ornament",
}


def _normalize_build_spec(slots: dict) -> dict:
    """Normalize pipeline output into BuildSpec v2.

    v2 preserves all rich fields from the LLM build_planner output.
    v1 fallback (buildingType/style/scale/mood) stays for backward compat.

    If the pipeline ran minecraft_llm_active_build chain, slots contains a
    nested build_planner dict with the full plan. We promote those fields
    to the top-level spec so downstream renderers get direct access.
    """
    # Already v1/v2 format
    if slots.get("version") in (1, 2) and slots.get("kind") == "build":
        return slots

    # Extract the build_planner nested dict (LLM active chain output)
    bp = slots.get("build_planner") if isinstance(slots.get("build_planner"), dict) else {}

    # Merge: flat slots take priority, bp fills in gaps
    def pick(*keys, default=None):
        for src in (slots, bp):
            for k in keys:
                if k in src and src[k] is not None:
                    return src[k]
        return default

    spec: dict = {
        "version": 2,
        "kind": "build",
        # v1 compatibility (legacy renderer)
        "buildingType": pick("buildingType", "build_type", default="cottage"),
        "style": pick("style", default="medieval"),
        "scale": pick("scale", default="medium"),
        "mood": pick("mood", "tone", default="plain"),
        "materialHints": pick("materialHints", "material_hints", default=[]),
        "constraints": pick("constraints", default={}),
        # v2 rich fields (new renderer features)
        "build_type": pick("build_type", default="cottage"),
        "tone": pick("tone", default="plain"),
        "footprint": pick("footprint", default={}),
        "floors": pick("floors", default={}),
        "wall": pick("wall", default={}),
        "roof": pick("roof", default={}),
        "windows": pick("windows", default={}),
        "entrance": pick("entrance", default={}),
        "silhouette_strategy": pick("silhouette_strategy", default=""),
        "wall_height": pick("wall_height"),
        "ornament_density": pick("ornament_density"),
        "defense_level": pick("defense_level"),
        "verticality": pick("verticality"),
        "symmetry_bias": pick("symmetry_bias"),
        "palette_strategy": pick("palette_strategy", default=""),
        "primary_materials": pick("primary_materials", default={}),
        "key_features": pick("key_features", default=[]),
        "interior_rooms": pick("interior_rooms", default=[]),
        "exterior_elements": pick("exterior_elements", default=[]),
        "lighting_scheme": pick("lighting_scheme", default=""),
        "landscape_context": pick("landscape_context", default={}),
        "narrative_hook": pick("narrative_hook", default=""),
        "creative_notes": pick("creative_notes", default=""),
        # Chain outputs (preserved for advanced renderers)
        "variants": (slots.get("variant_planner") or {}).get("variants", []),
        "critique": slots.get("build_critic", {}),
        "repair_plan": slots.get("repair_operations", {}),
        # Domain template (if matched)
        "template_id": slots.get("_template_id", ""),
    }

    # Drop None values so the client doesn't see nulls
    return {k: v for k, v in spec.items() if v not in (None, {}, [], "")}


def _normalize_edit_spec(slots: dict) -> dict:
    if slots.get("version") == 1 and slots.get("kind") == "edit":
        return slots
    operations = []
    target = slots.get("target_anchor", {})
    anchor_type = target.get("anchor_type", "") if isinstance(target, dict) else ""
    for raw_op in slots.get("operations", []):
        if not isinstance(raw_op, dict): continue
        mapped_op = _OPERATION_MAP.get(raw_op.get("type", "add"), "emphasize")
        op_entry: dict = {"op": mapped_op}
        if mapped_op in ("emphasize", "de_emphasize"):
            op_entry["target"] = _ANCHOR_TO_TARGET.get(anchor_type, "ornament")
        elif mapped_op == "change_style":
            op_entry["value"] = raw_op.get("delta", {}).get("material", "")
        operations.append(op_entry)
    if not operations:
        operations = [{"op": "emphasize", "target": _ANCHOR_TO_TARGET.get(anchor_type, "ornament")}]
    return {"version": 1, "kind": "edit", "operations": operations}
