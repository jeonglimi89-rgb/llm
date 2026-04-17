"""
core/models.py — 공용 데이터 모델

모든 프로젝트의 Intent Parser, Variant Generator, Critique/Ranker,
Delta Patch Interpreter, Memory/Log Pipeline이 공유하는 데이터 구조.

이 파일의 구조는 v1에서 고정하고 쉽게 변경하지 않는다.
필드 추가는 가능하되, 기존 필드 제거/이름 변경은 마이그레이션 필수.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class IntentType(str, Enum):
    CREATE_NEW = "create_new"
    MODIFY_EXISTING = "modify_existing"
    EXPLORE_VARIANTS = "explore_variants"
    REFINE = "refine"
    UNDO = "undo"
    COMPARE = "compare"
    SELECT = "select"
    DELETE = "delete"
    QUERY = "query"  # 정보 질문 (생성/수정이 아닌)


@dataclass
class ParsedIntent:
    intent_type: IntentType
    target_object: str                          # "concept", "module", "dimension", "wiring", ...
    constraints: dict[str, Any] = field(default_factory=dict)
    modification_scope: Optional[str] = None    # "legs_only", "color_only", 특정 필드 경로
    reference_id: Optional[str] = None          # 이전 결과물 참조 ID
    confidence: float = 0.0
    ambiguities: list[str] = field(default_factory=list)
    raw_text: str = ""                          # 원본 사용자 입력 보존

    def to_dict(self) -> dict:
        d = asdict(self)
        d["intent_type"] = self.intent_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ParsedIntent:
        d = dict(d)
        d["intent_type"] = IntentType(d["intent_type"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Variant
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    variant_id: str = field(default_factory=lambda: _new_id("var"))
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    diff_from_base: dict[str, Any] = field(default_factory=dict)
    generation_method: str = "rule_expansion"  # "rule_expansion" | "llm_suggestion" | "user_history"
    tags: list[str] = field(default_factory=list)  # "safe", "bold", "budget", ...

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Variant:
        return cls(**d)


# ---------------------------------------------------------------------------
# Critique
# ---------------------------------------------------------------------------

@dataclass
class Critique:
    variant_id: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    overall_rank: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Critique:
        return cls(**d)


# ---------------------------------------------------------------------------
# Delta Patch
# ---------------------------------------------------------------------------

@dataclass
class PatchOperation:
    op_type: str          # "set" | "adjust" | "remove" | "add"
    path: str             # JSON pointer: "/dimensions/overall_width_mm"
    value: Any = None     # 새 값 또는 delta 값
    relative: bool = False  # True면 value가 상대값 (+10, -0.2 등)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeltaPatch:
    patch_id: str = field(default_factory=lambda: _new_id("patch"))
    base_variant_id: str = ""
    operations: list[PatchOperation] = field(default_factory=list)
    description: str = ""  # 원본 수정 요청 텍스트

    def to_dict(self) -> dict:
        d = asdict(self)
        d["operations"] = [op.to_dict() for op in self.operations]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DeltaPatch:
        d = dict(d)
        d["operations"] = [PatchOperation(**op) for op in d.get("operations", [])]
        return cls(**d)


# ---------------------------------------------------------------------------
# Session Record (학습 데이터 축적용)
# ---------------------------------------------------------------------------

@dataclass
class SessionRecord:
    session_id: str = field(default_factory=lambda: _new_id("sess"))
    project_id: str = ""
    project_type: str = ""                       # "product_design" | "drawing_ai" | ...
    timestamp: str = field(default_factory=_now_utc)

    # 입력
    user_request: str = ""
    context: dict[str, Any] = field(default_factory=dict)  # 현재 프로젝트 상태 요약

    # 처리 과정
    parsed_intent: Optional[ParsedIntent] = None
    variants_generated: list[Variant] = field(default_factory=list)
    critiques: list[Critique] = field(default_factory=list)

    # 사용자 선택
    user_selected_variant_id: Optional[str] = None
    user_edits: list[DeltaPatch] = field(default_factory=list)
    final_params: dict[str, Any] = field(default_factory=dict)

    # 결과
    final_accepted: bool = False
    self_critique: str = ""                     # 시스템 자체 평가
    user_satisfaction: Optional[int] = None     # 1~5 (선택적)

    def to_dict(self) -> dict:
        d = {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "project_type": self.project_type,
            "timestamp": self.timestamp,
            "user_request": self.user_request,
            "context": self.context,
            "parsed_intent": self.parsed_intent.to_dict() if self.parsed_intent else None,
            "variants_generated": [v.to_dict() for v in self.variants_generated],
            "critiques": [c.to_dict() for c in self.critiques],
            "user_selected_variant_id": self.user_selected_variant_id,
            "user_edits": [e.to_dict() for e in self.user_edits],
            "final_params": self.final_params,
            "final_accepted": self.final_accepted,
            "self_critique": self.self_critique,
            "user_satisfaction": self.user_satisfaction,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SessionRecord:
        d = dict(d)
        if d.get("parsed_intent"):
            d["parsed_intent"] = ParsedIntent.from_dict(d["parsed_intent"])
        d["variants_generated"] = [Variant.from_dict(v) for v in d.get("variants_generated", [])]
        d["critiques"] = [Critique.from_dict(c) for c in d.get("critiques", [])]
        d["user_edits"] = [DeltaPatch.from_dict(e) for e in d.get("user_edits", [])]
        return cls(**d)
