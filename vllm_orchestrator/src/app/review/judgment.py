"""
review/judgment.py - 구조화된 인간 검수 판정 레이어

LLM critique text가 아니라 다운스트림 코드와 사람이 함께 소비하는
구조화된 판정. 모든 도메인(builder/cad/minecraft/animation)이 공유.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class JudgmentItem:
    """단일 검수 항목"""
    category: str                       # geometry/style/safety/regulation/...
    severity: str                       # Severity value
    rationale: str                      # 짧은 설명
    evidence: dict[str, Any] = field(default_factory=dict)  # 증거 포인터
    recommended_action: str = ""        # 권장 조치
    confidence: float = 1.0             # 0.0~1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewJudgment:
    """전체 판정 결과 — 다운스트림이 소비하는 안정 스키마"""
    artifact_id: str
    domain: str                         # builder/cad/minecraft/animation
    task_type: str                      # 예: builder.generate_plan
    verdict: str                        # Verdict value
    items: list[JudgmentItem] = field(default_factory=list)
    summary: str = ""
    auto_pass: bool = False             # 자동 검증 통과 여부 (별도)
    human_required: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "domain": self.domain,
            "task_type": self.task_type,
            "verdict": self.verdict,
            "summary": self.summary,
            "auto_pass": self.auto_pass,
            "human_required": self.human_required,
            "items": [i.to_dict() for i in self.items],
            "created_at": self.created_at,
            "stats": {
                "total_items": len(self.items),
                "critical": sum(1 for i in self.items if i.severity == "critical"),
                "high": sum(1 for i in self.items if i.severity == "high"),
                "medium": sum(1 for i in self.items if i.severity == "medium"),
                "low": sum(1 for i in self.items if i.severity == "low"),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> ReviewJudgment:
        items = [JudgmentItem(**i) for i in d.get("items", [])]
        return cls(
            artifact_id=d["artifact_id"],
            domain=d["domain"],
            task_type=d["task_type"],
            verdict=d["verdict"],
            items=items,
            summary=d.get("summary", ""),
            auto_pass=d.get("auto_pass", False),
            human_required=d.get("human_required", False),
            created_at=d.get("created_at", datetime.now(UTC).isoformat()),
        )


def validate_judgment_schema(j: dict) -> tuple[bool, list[str]]:
    """판정 스키마 형식 검증"""
    errors = []
    required = ["artifact_id", "domain", "task_type", "verdict"]
    for k in required:
        if k not in j:
            errors.append(f"missing field: {k}")
    if "verdict" in j and j["verdict"] not in ("pass", "fail", "needs_review"):
        errors.append(f"invalid verdict: {j['verdict']}")
    for i, item in enumerate(j.get("items", [])):
        if "severity" in item and item["severity"] not in ("info", "low", "medium", "high", "critical"):
            errors.append(f"item[{i}]: invalid severity: {item['severity']}")
        if "category" not in item:
            errors.append(f"item[{i}]: missing category")
        if "rationale" not in item:
            errors.append(f"item[{i}]: missing rationale")
    return len(errors) == 0, errors
