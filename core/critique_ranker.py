"""
core/critique_ranker.py — 비평/랭킹 모듈

Variant[] → Critique[] 평가 + 랭킹.

v1: 규칙 기반 스코어링 (제약 만족도, 실현 가능성 등)
v2: 로컬 LLM이 자연어 비평 추가
v3: 사용자 선택 이력 학습된 랭커

핵심: 비평 없는 후보안 제시 금지.
사용자에게 "왜 이 안이 좋은지/나쁜지" 근거를 제공.
이 근거가 나중에 학습 데이터가 됨.
"""

from __future__ import annotations

from typing import Any

from .models import Critique, ParsedIntent, Variant
from .schema_registry import SchemaRegistry


class CritiqueRankerModule:

    def __init__(self, schema_registry: SchemaRegistry, project_type: str):
        self.schema_registry = schema_registry
        self.project_type = project_type
        self.criteria = schema_registry.get_critique_criteria(project_type)

    def critique_all(
        self,
        variants: list[Variant],
        intent: ParsedIntent,
    ) -> list[Critique]:
        """모든 variant를 비평하고 랭킹"""
        critiques = []

        for v in variants:
            scores: dict[str, float] = {}
            strengths: list[str] = []
            weaknesses: list[str] = []

            for criterion in self.criteria:
                name = criterion["name"]
                score = self._evaluate_criterion(name, v, intent)
                scores[name] = score

                if score >= 0.7:
                    strengths.append(
                        self._explain_strength(name, criterion, v, intent)
                    )
                elif score < 0.4:
                    weaknesses.append(
                        self._explain_weakness(name, criterion, v, intent)
                    )

            critiques.append(Critique(
                variant_id=v.variant_id,
                scores=scores,
                strengths=strengths,
                weaknesses=weaknesses,
                overall_rank=0,
            ))

        # 가중 합산으로 랭킹
        weight_map = {c["name"]: c.get("weight", 0.2) for c in self.criteria}
        critiques.sort(
            key=lambda c: sum(
                c.scores.get(name, 0) * weight_map.get(name, 0.2)
                for name in c.scores
            ),
            reverse=True,
        )
        for i, c in enumerate(critiques):
            c.overall_rank = i + 1

        return critiques

    # ------------------------------------------------------------------
    # 기준별 평가 (v1: 규칙 기반)
    # ------------------------------------------------------------------

    def _evaluate_criterion(
        self, criterion_name: str, variant: Variant, intent: ParsedIntent
    ) -> float:
        evaluators = {
            "constraint_satisfaction": self._eval_constraint_satisfaction,
            "feasibility": self._eval_feasibility,
            "cost_alignment": self._eval_cost_alignment,
            "novelty": self._eval_novelty,
            "safety_compliance": self._eval_safety_compliance,
            # Drawing AI 전용
            "completeness": self._eval_completeness,
            "iso_compliance": self._eval_iso_compliance,
            "readability": self._eval_readability,
            "layer_accuracy": self._eval_layer_accuracy,
        }
        evaluator = evaluators.get(criterion_name, self._eval_default)
        return evaluator(variant, intent)

    def _eval_constraint_satisfaction(self, v: Variant, intent: ParsedIntent) -> float:
        """사용자 제약 조건 만족도"""
        if not intent.constraints:
            return 0.8  # 제약 없으면 기본 양호

        satisfied = 0
        total = len(intent.constraints)
        for key, expected in intent.constraints.items():
            actual = self._get_nested(v.params, key)
            if actual == expected:
                satisfied += 1
            elif actual is not None:
                satisfied += 0.5  # 값이 있지만 다름

        return satisfied / total if total > 0 else 0.8

    def _eval_feasibility(self, v: Variant, intent: ParsedIntent) -> float:
        """실현 가능성 (파라미터 범위 체크)"""
        errors = self.schema_registry.validate_engine_params(
            self.project_type, v.params
        )
        if not errors:
            return 0.9
        # 에러 수에 따라 감점
        return max(0.1, 0.9 - len(errors) * 0.2)

    def _eval_cost_alignment(self, v: Variant, intent: ParsedIntent) -> float:
        """예산-복잡도 정합성"""
        budget = (
            v.params.get("requirement", {}).get("budget_level", "medium")
        )
        complexity = (
            v.params.get("concept", {}).get("estimated_complexity", "medium")
        )

        alignment = {
            ("low", "low"): 0.9,
            ("low", "medium"): 0.6,
            ("low", "high"): 0.2,
            ("medium", "low"): 0.7,
            ("medium", "medium"): 0.9,
            ("medium", "high"): 0.5,
            ("high", "low"): 0.6,
            ("high", "medium"): 0.8,
            ("high", "high"): 0.9,
        }
        return alignment.get((budget, complexity), 0.5)

    def _eval_novelty(self, v: Variant, intent: ParsedIntent) -> float:
        """차별성 (diff_from_base 크기 기반)"""
        if not v.diff_from_base:
            return 0.3
        diff_count = len(v.diff_from_base)
        return min(1.0, 0.3 + diff_count * 0.2)

    def _eval_safety_compliance(self, v: Variant, intent: ParsedIntent) -> float:
        """안전 준수"""
        safety_notes = v.params.get("requirement", {}).get("safety_notes", [])
        if safety_notes:
            return 0.8  # 안전 요구사항이 명시됨
        return 0.6  # 기본

    def _eval_completeness(self, v: Variant, intent: ParsedIntent) -> float:
        """도면 완성도 (Drawing AI)"""
        views = v.params.get("views", [])
        if len(views) >= 3:
            return 0.9
        elif len(views) >= 1:
            return 0.6
        return 0.2

    def _eval_iso_compliance(self, v: Variant, intent: ParsedIntent) -> float:
        """ISO 규격 준수 (Drawing AI)"""
        sheet = v.params.get("sheet", {})
        if sheet.get("projection_method") in ("first_angle", "third_angle"):
            return 0.8
        return 0.5

    def _eval_readability(self, v: Variant, intent: ParsedIntent) -> float:
        """가독성 (Drawing AI) — v1은 기본값"""
        return 0.7

    def _eval_layer_accuracy(self, v: Variant, intent: ParsedIntent) -> float:
        """레이어 정확성 (Drawing AI) — v1은 기본값"""
        return 0.7

    def _eval_default(self, v: Variant, intent: ParsedIntent) -> float:
        return 0.5

    # ------------------------------------------------------------------
    # 강점/약점 설명 생성
    # ------------------------------------------------------------------

    def _explain_strength(
        self, criterion_name: str, criterion: dict, v: Variant, intent: ParsedIntent
    ) -> str:
        label = criterion.get("label", criterion_name)
        tag_str = ", ".join(v.tags) if v.tags else "기본"
        return f"[{label}] 양호 — 태그: {tag_str}"

    def _explain_weakness(
        self, criterion_name: str, criterion: dict, v: Variant, intent: ParsedIntent
    ) -> str:
        label = criterion.get("label", criterion_name)
        return f"[{label}] 부족 — 개선 필요"

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------

    @staticmethod
    def _get_nested(d: dict, dotted_key: str) -> Any:
        """'a.b.c' 형태의 키로 중첩 dict 접근"""
        parts = dotted_key.split(".")
        current = d
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
