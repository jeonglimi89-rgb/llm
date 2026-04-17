"""
core/eval_variant.py — Variant Generator 품질 평가

생성된 Variant의 다양성, 제약 반영율, 스키마 준수율을 측정.

실행: python -X utf8 -m core.eval_variant
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .models import ParsedIntent, IntentType, Variant
from .schema_registry import SchemaRegistry
from .intent_parser import IntentParserModule
from .variant_generator import VariantGeneratorModule
from .critique_ranker import CritiqueRankerModule


# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------

VARIANT_TEST_CASES = [
    {
        "id": 1,
        "project_type": "product_design",
        "input": "미니멀한 사무용 의자 만들어줘",
        "n_variants": 3,
        "checks": {
            "min_variants": 3,
            "expected_constraints": {"style": "minimal"},
            # VariantGeneratorModule은 axis-level params 생성 (concept/requirement 레벨)
            "params_fields": [],
        },
    },
    {
        "id": 2,
        "project_type": "product_design",
        "input": "접이식 욕실 선반 설계해줘, 방수 필요",
        "n_variants": 3,
        "checks": {
            "min_variants": 3,
            "expected_constraints": {"form": "foldable", "waterproof": True},
            "params_fields": [],
        },
    },
    {
        "id": 3,
        "project_type": "product_design",
        "input": "저비용 구조로 다시 생성해줘",
        "n_variants": 2,
        "checks": {
            "min_variants": 2,
            "expected_constraints": {"budget_level": "low"},
            "params_fields": [],
        },
    },
    {
        "id": 4,
        "project_type": "drawing_ai",
        "input": "기본 3면도 그려줘",
        "n_variants": 3,
        "checks": {
            "min_variants": 3,
            # VariantGeneratorModule은 _view_count/_annotation_level/_layer_mode 등 axis params 생성
            "params_fields": [],
        },
    },
    {
        "id": 5,
        "project_type": "drawing_ai",
        "input": "아이소메트릭 뷰 추가해줘",
        "n_variants": 2,
        "checks": {
            "min_variants": 2,
            "params_fields": [],
        },
    },
]


# ---------------------------------------------------------------------------
# 평가 결과
# ---------------------------------------------------------------------------

@dataclass
class VariantEvalResult:
    total: int = 0
    count_ok: int = 0          # min_variants 만족
    constraint_ok: int = 0     # 기대 제약 반영
    constraint_expected: int = 0
    schema_ok: int = 0         # 필수 필드 존재
    diversity_avg: float = 0.0 # 평균 다양성 점수 (0~1)
    critique_ok: int = 0       # critique 반환 성공
    failures: list[dict] = field(default_factory=list)

    @property
    def count_rate(self) -> float:
        return self.count_ok / self.total if self.total else 0

    @property
    def constraint_rate(self) -> float:
        return self.constraint_ok / self.constraint_expected if self.constraint_expected else 1.0

    @property
    def schema_rate(self) -> float:
        return self.schema_ok / self.total if self.total else 0

    def summary(self) -> dict:
        return {
            "total": self.total,
            "variant_count_ok": f"{self.count_ok}/{self.total}",
            "constraint_recall": f"{round(self.constraint_rate * 100, 1)}%",
            "schema_compliance": f"{round(self.schema_rate * 100, 1)}%",
            "diversity_avg": round(self.diversity_avg, 3),
            "critique_ok": f"{self.critique_ok}/{self.total}",
            "failure_count": len(self.failures),
        }


# ---------------------------------------------------------------------------
# 다양성 계산
# ---------------------------------------------------------------------------

def _diversity_score(variants: list[Variant]) -> float:
    """태그 기반 다양성: 각 variant 쌍이 공유하지 않는 태그 비율"""
    if len(variants) < 2:
        return 1.0

    total_pairs = 0
    total_diff = 0.0
    for i in range(len(variants)):
        for j in range(i + 1, len(variants)):
            tags_i = set(variants[i].tags or [])
            tags_j = set(variants[j].tags or [])
            union = tags_i | tags_j
            if union:
                diff = len(tags_i.symmetric_difference(tags_j)) / len(union)
            else:
                # tags 없으면 description 단어 수로 비교
                desc_i = set((variants[i].description or "").split())
                desc_j = set((variants[j].description or "").split())
                union2 = desc_i | desc_j
                diff = len(desc_i.symmetric_difference(desc_j)) / len(union2) if union2 else 0.5
            total_diff += diff
            total_pairs += 1

    return total_diff / total_pairs if total_pairs else 0.0


# ---------------------------------------------------------------------------
# 평가 실행
# ---------------------------------------------------------------------------

def run_evaluation() -> VariantEvalResult:
    registry = SchemaRegistry()
    result = VariantEvalResult()
    diversity_scores = []

    for case in VARIANT_TEST_CASES:
        result.total += 1
        case_id = case["id"]
        project_type = case["project_type"]
        checks = case["checks"]

        parser = IntentParserModule(registry, project_type)
        generator = VariantGeneratorModule(registry, project_type)
        ranker = CritiqueRankerModule(registry, project_type)

        intent = parser.parse(case["input"])
        variants = generator.generate(intent, {}, case["n_variants"])
        critiques = ranker.critique_all(variants, intent)

        # 1. 수량 체크
        if len(variants) >= checks["min_variants"]:
            result.count_ok += 1
        else:
            result.failures.append({
                "id": case_id,
                "check": "count",
                "expected": f">={checks['min_variants']}",
                "actual": len(variants),
            })

        # 2. 제약 조건 반영 체크 (최소 1개 variant의 params에 반영되어야)
        expected_constraints = checks.get("expected_constraints", {})
        for ckey, cval in expected_constraints.items():
            result.constraint_expected += 1
            found = False
            for v in variants:
                # params 최상위 또는 requirements 하위에서 확인
                p = v.params
                if _check_constraint_in_params(p, ckey, cval):
                    found = True
                    break
                # intent constraints도 함께 확인
                if intent.constraints.get(ckey) == cval:
                    found = True
                    break
            if found:
                result.constraint_ok += 1
            else:
                result.failures.append({
                    "id": case_id,
                    "check": f"constraint:{ckey}",
                    "expected": cval,
                    "actual": "not found in any variant",
                })

        # 3. 스키마 필드 체크
        required_fields = checks.get("params_fields", [])
        schema_pass = True
        for v in variants:
            for fld in required_fields:
                if fld not in v.params:
                    schema_pass = False
                    result.failures.append({
                        "id": case_id,
                        "check": f"schema_field:{fld}",
                        "variant_id": v.variant_id,
                        "actual": "missing",
                    })
        if schema_pass:
            result.schema_ok += 1

        # 4. 다양성 점수
        score = _diversity_score(variants)
        diversity_scores.append(score)

        # 5. Critique 반환 여부
        if critiques and len(critiques) >= 1:
            result.critique_ok += 1
        else:
            result.failures.append({
                "id": case_id,
                "check": "critique",
                "actual": f"got {len(critiques)} critiques",
            })

    if diversity_scores:
        result.diversity_avg = sum(diversity_scores) / len(diversity_scores)

    return result


def _check_constraint_in_params(params: dict, key: str, value: Any) -> bool:
    """params dict 내에서 key=value 쌍이 존재하는지 재귀 검색"""
    if not isinstance(params, dict):
        return False
    if key in params and params[key] == value:
        return True
    for v in params.values():
        if isinstance(v, dict) and _check_constraint_in_params(v, key, value):
            return True
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and _check_constraint_in_params(item, key, value):
                    return True
    return False


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_report(result: VariantEvalResult) -> None:
    summary = result.summary()
    print("=" * 60)
    print("Variant Generator Evaluation Report")
    print("=" * 60)
    print(f"  Total test cases: {summary['total']}")
    print(f"  Variant count OK: {summary['variant_count_ok']}")
    print(f"  Constraint recall: {summary['constraint_recall']}")
    print(f"  Schema compliance: {summary['schema_compliance']}")
    print(f"  Diversity avg: {summary['diversity_avg']}")
    print(f"  Critique OK: {summary['critique_ok']}")
    print(f"  Failures: {summary['failure_count']}")

    if result.failures:
        print()
        print("--- Failures ---")
        for f in result.failures:
            print(f"  #{f['id']} [{f['check']}]: {f}")
    print("=" * 60)


if __name__ == "__main__":
    result = run_evaluation()
    print_report(result)
