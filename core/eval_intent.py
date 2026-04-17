"""
core/eval_intent.py — Intent Parser 평가 러너

test_intent_dataset.json의 50개 테스트 케이스로
규칙 기반 Intent Parser의 정확도를 측정.

실행: python -X utf8 -m core.eval_intent
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .models import IntentType, ParsedIntent
from .schema_registry import SchemaRegistry
from .intent_parser import IntentParserModule


@dataclass
class EvalResult:
    total: int = 0
    intent_type_correct: int = 0
    target_object_correct: int = 0
    constraints_detected: int = 0
    constraints_expected: int = 0
    ambiguity_correct: int = 0
    ambiguity_expected: int = 0
    failures: list[dict] = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []

    @property
    def intent_accuracy(self) -> float:
        return self.intent_type_correct / self.total if self.total else 0

    @property
    def target_accuracy(self) -> float:
        return self.target_object_correct / self.total if self.total else 0

    @property
    def constraint_recall(self) -> float:
        return self.constraints_detected / self.constraints_expected if self.constraints_expected else 1.0

    def summary(self) -> dict:
        return {
            "total": self.total,
            "intent_type_accuracy": round(self.intent_accuracy * 100, 1),
            "target_object_accuracy": round(self.target_accuracy * 100, 1),
            "constraint_recall": round(self.constraint_recall * 100, 1),
            "ambiguity_detection": f"{self.ambiguity_correct}/{self.ambiguity_expected}",
            "failure_count": len(self.failures),
        }


def run_evaluation(project_type: str = "product_design") -> EvalResult:
    """테스트셋 전체 평가 실행"""
    dataset_path = os.path.join(os.path.dirname(__file__), "test_intent_dataset.json")
    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)

    registry = SchemaRegistry()
    parser = IntentParserModule(registry, project_type)
    result = EvalResult()

    for case in dataset["test_cases"]:
        result.total += 1
        case_id = case["id"]
        user_input = case["input"]
        expected = case["expected"]

        # 파싱
        intent = parser.parse(user_input)

        # intent_type 평가
        expected_type = expected["intent_type"]
        actual_type = intent.intent_type.value
        if actual_type == expected_type:
            result.intent_type_correct += 1
        else:
            result.failures.append({
                "id": case_id,
                "input": user_input,
                "field": "intent_type",
                "expected": expected_type,
                "actual": actual_type,
            })

        # target_object 평가
        expected_target = expected.get("target_object")
        if expected_target:
            if intent.target_object == expected_target:
                result.target_object_correct += 1
            else:
                result.failures.append({
                    "id": case_id,
                    "input": user_input,
                    "field": "target_object",
                    "expected": expected_target,
                    "actual": intent.target_object,
                })
        else:
            result.target_object_correct += 1  # 기대값 없으면 통과

        # constraints 평가
        expected_keys = expected.get("constraints_keys", [])
        if expected_keys:
            result.constraints_expected += len(expected_keys)
            for key in expected_keys:
                if key in intent.constraints:
                    result.constraints_detected += 1

        # ambiguity 평가
        if expected.get("has_ambiguity"):
            result.ambiguity_expected += 1
            if intent.ambiguities:
                result.ambiguity_correct += 1

    return result


def print_report(result: EvalResult) -> None:
    """평가 결과 출력"""
    summary = result.summary()
    print("=" * 60)
    print("Intent Parser Evaluation Report")
    print("=" * 60)
    print(f"  Total test cases: {summary['total']}")
    print(f"  Intent type accuracy: {summary['intent_type_accuracy']}%")
    print(f"  Target object accuracy: {summary['target_object_accuracy']}%")
    print(f"  Constraint recall: {summary['constraint_recall']}%")
    print(f"  Ambiguity detection: {summary['ambiguity_detection']}")
    print(f"  Failures: {summary['failure_count']}")

    if result.failures:
        print()
        print("--- Failures ---")
        for f in result.failures:
            print(f"  #{f['id']} [{f['field']}] \"{f['input']}\"")
            print(f"    expected: {f['expected']} / actual: {f['actual']}")
    print("=" * 60)


if __name__ == "__main__":
    result = run_evaluation()
    print_report(result)
