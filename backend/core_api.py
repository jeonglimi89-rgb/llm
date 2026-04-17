"""
backend/core_api.py - 공용 LLM 코어 API

통합 엔드포인트:
  POST /parse_intent      - 의도 해석
  POST /generate_variants  - 후보안 생성 + 비평
  POST /critique           - 기존 variant 비평
  POST /apply_delta        - delta patch 해석 + 적용
  POST /record_session     - 세션 기록 (학습 데이터)
  GET  /status             - 시스템 상태
  GET  /schema/{project}   - 프로젝트별 스키마

4개 프로젝트 공용: minecraft, builder, product_design, drawing_ai, animation
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException

# core/ 모듈 경로
_CORE_DIR = str(Path(__file__).resolve().parent.parent)
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from core.models import (
    IntentType,
    ParsedIntent,
    Variant,
    Critique,
    DeltaPatch,
    SessionRecord,
)
from core.schema_registry import SchemaRegistry
from core.intent_parser import IntentParserModule
from core.variant_generator import VariantGeneratorModule
from core.critique_ranker import CritiqueRankerModule
from core.delta_patch import DeltaPatchInterpreter
from core.memory_log import MemoryLogPipeline

# ---------------------------------------------------------------------------
# 초기화
# ---------------------------------------------------------------------------

_DATA_DIR = str(Path(__file__).resolve().parent.parent / "data")
_registry = SchemaRegistry()

_parsers: dict[str, IntentParserModule] = {}
_generators: dict[str, VariantGeneratorModule] = {}
_rankers: dict[str, CritiqueRankerModule] = {}
_patchers: dict[str, DeltaPatchInterpreter] = {}
_logs: dict[str, MemoryLogPipeline] = {}


def _get_modules(project_type: str):
    """프로젝트별 모듈 lazy init"""
    if project_type not in _parsers:
        _parsers[project_type] = IntentParserModule(_registry, project_type)
        _generators[project_type] = VariantGeneratorModule(_registry, project_type)
        _rankers[project_type] = CritiqueRankerModule(_registry, project_type)
        _patchers[project_type] = DeltaPatchInterpreter(_registry, project_type)
        _logs[project_type] = MemoryLogPipeline(_DATA_DIR, project_type)
    return (
        _parsers[project_type],
        _generators[project_type],
        _rankers[project_type],
        _patchers[project_type],
        _logs[project_type],
    )


def _connect_ollama(project_type: str):
    """Ollama 백엔드가 있으면 모듈에 연결"""
    try:
        from core.llm_backend import OllamaBackend
        llm = OllamaBackend()
        if llm.is_available():
            parser, generator, ranker, patcher, _ = _get_modules(project_type)
            parser.llm_backend = llm
            patcher.llm_backend = llm
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# 라우터 (독립 실행 시 FastAPI 앱, 번들 연결 시 include_router)
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/core", tags=["core-pipeline"])


@router.get("/status")
def api_status():
    """시스템 상태 + Ollama 연결 상태"""
    projects = _registry.list_projects()
    stats = {}
    for pt in projects:
        try:
            log = MemoryLogPipeline(_DATA_DIR, pt)
            stats[pt] = log.get_stats()
        except Exception:
            stats[pt] = {}

    # Ollama 상태
    ollama_status = {"available": False, "models": []}
    try:
        from core.llm_backend import OllamaBackend
        llm = OllamaBackend()
        ollama_status["available"] = llm.is_available()
        ollama_status["models"] = llm.list_models()
    except Exception:
        pass

    return {
        "version": "core-v2-ollama",
        "projects": projects,
        "session_stats": stats,
        "ollama": ollama_status,
        "modules": [
            "intent_parser", "variant_generator", "critique_ranker",
            "delta_patch", "memory_log", "schema_registry",
        ],
    }


# ---------------------------------------------------------------------------
# /parse_intent
# ---------------------------------------------------------------------------

@router.post("/parse_intent")
def parse_intent(
    text: str = Body(..., embed=True),
    project_type: str = Body("product_design", embed=True),
    context: dict = Body(default_factory=dict, embed=True),
):
    """
    자연어 → ParsedIntent JSON.
    v1: 규칙 기반, v2: Ollama structured output.
    """
    parser, *_ = _get_modules(project_type)
    _connect_ollama(project_type)
    intent = parser.parse(text, context)
    return intent.to_dict()


# ---------------------------------------------------------------------------
# /generate_variants
# ---------------------------------------------------------------------------

@router.post("/generate_variants")
def generate_variants(
    text: str = Body("", embed=True),
    project_type: str = Body("product_design", embed=True),
    base_params: dict = Body(default_factory=dict, embed=True),
    n_variants: int = Body(3, embed=True),
    diversity_weight: float = Body(0.5, embed=True),
):
    """
    text → intent → variants[] + critiques[].
    항상 복수 후보안 + 비평 반환.
    """
    parser, generator, ranker, _, _ = _get_modules(project_type)
    _connect_ollama(project_type)

    intent = parser.parse(text)
    variants = generator.generate(intent, base_params, n_variants, diversity_weight)
    critiques = ranker.critique_all(variants, intent)

    return {
        "project_type": project_type,
        "intent": intent.to_dict(),
        "variants": [v.to_dict() for v in variants],
        "critiques": [c.to_dict() for c in critiques],
    }


# ---------------------------------------------------------------------------
# /critique
# ---------------------------------------------------------------------------

@router.post("/critique")
def critique_variants(
    variants: list = Body(..., embed=True),
    project_type: str = Body("product_design", embed=True),
    intent: dict = Body(default_factory=dict, embed=True),
):
    """기존 variant 목록을 비평/랭킹."""
    _, _, ranker, _, _ = _get_modules(project_type)
    _connect_ollama(project_type)

    variant_objs = [Variant.from_dict(v) for v in variants]
    intent_obj = ParsedIntent.from_dict(intent) if intent else None
    critiques = ranker.critique_all(variant_objs, intent_obj)

    return {
        "critiques": [c.to_dict() for c in critiques],
    }


# ---------------------------------------------------------------------------
# /apply_delta
# ---------------------------------------------------------------------------

@router.post("/apply_delta")
def apply_delta(
    edit_request: str = Body(..., embed=True),
    project_type: str = Body("product_design", embed=True),
    current_params: dict = Body(default_factory=dict, embed=True),
):
    """
    수정 요청 → DeltaPatch 해석 + 적용.
    전체 재생성 금지, 최소 단위 패치만.
    """
    parser, _, _, patcher, _ = _get_modules(project_type)
    _connect_ollama(project_type)

    intent = parser.parse(edit_request)
    patch = patcher.interpret(edit_request, current_params, intent)
    new_params = patcher.apply(current_params, patch)

    return {
        "intent": intent.to_dict(),
        "patch": patch.to_dict(),
        "new_params": new_params,
        "changed_paths": [op.path for op in patch.operations],
    }


# ---------------------------------------------------------------------------
# /record_session
# ---------------------------------------------------------------------------

@router.post("/record_session")
def record_session(
    project_type: str = Body("product_design", embed=True),
    project_id: str = Body("", embed=True),
    user_request: str = Body("", embed=True),
    intent: dict = Body(default_factory=dict, embed=True),
    variants: list = Body(default_factory=list, embed=True),
    critiques: list = Body(default_factory=list, embed=True),
    selected_variant_id: Optional[str] = Body(None, embed=True),
    edits: list = Body(default_factory=list, embed=True),
    final_params: dict = Body(default_factory=dict, embed=True),
    accepted: bool = Body(False, embed=True),
):
    """세션 기록 저장 (학습 데이터 축적)"""
    _, _, _, _, log = _get_modules(project_type)

    record = SessionRecord(
        project_id=project_id,
        project_type=project_type,
        user_request=user_request,
        parsed_intent=ParsedIntent.from_dict(intent) if intent else None,
        variants_generated=[Variant.from_dict(v) for v in variants],
        critiques=[Critique.from_dict(c) for c in critiques],
        user_selected_variant_id=selected_variant_id,
        user_edits=[DeltaPatch.from_dict(e) for e in edits],
        final_params=final_params,
        final_accepted=accepted,
    )
    path = log.record_session(record)
    return {"session_id": record.session_id, "saved_to": path}


# ---------------------------------------------------------------------------
# 보조 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/session/stats/{project_type}")
def get_session_stats(project_type: str):
    _, _, _, _, log = _get_modules(project_type)
    return log.get_stats()


@router.post("/training/export/{project_type}")
def export_training_data(project_type: str):
    _, _, _, _, log = _get_modules(project_type)
    paths = log.save_training_pairs()
    return {"exported": paths}


@router.get("/schema/{project_type}")
def get_schema(project_type: str):
    try:
        return _registry.get_project_schema(project_type)
    except FileNotFoundError:
        raise HTTPException(404, f"Schema not found: {project_type}")


@router.get("/schema/{project_type}/aliases")
def get_aliases(project_type: str):
    return _registry.get_path_aliases(project_type)


@router.post("/schema/{project_type}/resolve-alias")
def resolve_alias(
    project_type: str,
    expression: str = Body(..., embed=True),
):
    path = _registry.resolve_alias(project_type, expression)
    return {"expression": expression, "resolved_path": path}


# ---------------------------------------------------------------------------
# 독립 실행 (python -m backend.core_api)
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """독립 FastAPI 앱 생성"""
    app = FastAPI(
        title="LLM Core API",
        description="공용 로컬 LLM 코어 - 의도 해석 / 후보안 생성 / 비평 / delta patch",
        version="2.0",
    )
    app.include_router(router)
    return app


# 하위 호환 (기존 코드에서 from backend.core_api import core_router로 쓰던 곳)
core_router = router

if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8100)
