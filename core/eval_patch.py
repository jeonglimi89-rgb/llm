"""
core/eval_patch.py — Delta Patch 정확도 평가

수정 요청에 대해 올바른 PatchOperation이 생성되고
적용 결과가 기대값과 일치하는지 측정.

실행: python -X utf8 -m core.eval_patch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema_registry import SchemaRegistry
from .intent_parser import IntentParserModule
from .delta_patch import DeltaPatchInterpreter, _get_at_path


# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------

PATCH_TEST_CASES = [
    # --- product_design: 절대값 치수 변경 ---
    {
        "id": 1,
        "project_type": "product_design",
        "edit_request": "전체 폭을 360mm로 바꿔줘",
        "base_params": {"dimensions": {"overall_width_mm": 420, "overall_depth_mm": 180, "overall_height_mm": 260}},
        "checks": {
            "min_ops": 1,
            "expected_path": "/dimensions/overall_width_mm",
            "expected_value": 360,
        },
    },
    {
        "id": 2,
        "project_type": "product_design",
        "edit_request": "높이를 920mm로 수정",
        "base_params": {"dimensions": {"overall_width_mm": 600, "overall_depth_mm": 300, "overall_height_mm": 750}},
        "checks": {
            "min_ops": 1,
            "expected_path": "/dimensions/overall_height_mm",
            "expected_value": 920,
        },
    },
    {
        "id": 3,
        "project_type": "product_design",
        "edit_request": "깊이를 300mm로 바꿔",
        "base_params": {"dimensions": {"overall_width_mm": 420, "overall_depth_mm": 180, "overall_height_mm": 260}},
        "checks": {
            "min_ops": 1,
            "expected_path": "/dimensions/overall_depth_mm",
            "expected_value": 300,
        },
    },
    # --- product_design: 상대값 변경 ---
    {
        "id": 4,
        "project_type": "product_design",
        "edit_request": "전체 높이를 좀 더 낮춰줘",
        "base_params": {"dimensions": {"overall_width_mm": 420, "overall_depth_mm": 180, "overall_height_mm": 300}},
        "checks": {
            "min_ops": 1,
            "expected_path": "/dimensions/overall_height_mm",
            "applied_check": lambda old, new: (
                new.get("dimensions", {}).get("overall_height_mm", 999)
                < old.get("dimensions", {}).get("overall_height_mm", 0)
            ),
        },
    },
    # --- product_design: 속성 변경 ---
    {
        "id": 5,
        "project_type": "product_design",
        "edit_request": "3D프린팅으로 제작 방식 변경",
        "base_params": {"fabrication": {"mode": "cnc"}},
        "checks": {
            "min_ops": 1,
        },
    },
    # --- drawing_ai: 뷰 추가 ---
    {
        "id": 6,
        "project_type": "drawing_ai",
        "edit_request": "단면도도 추가해줘",
        "base_params": {
            "views": [{"view_type": "front"}, {"view_type": "top"}, {"view_type": "side"}],
            "source_design": {"project_id": "test"},
        },
        "checks": {
            "min_ops": 1,
            "applied_check": lambda old, new: len(new.get("views", [])) > len(old.get("views", [])),
        },
    },
    {
        "id": 7,
        "project_type": "drawing_ai",
        "edit_request": "아이소메트릭 뷰 추가",
        "base_params": {
            "views": [{"view_type": "front"}],
            "source_design": {"project_id": "test"},
        },
        "checks": {
            "min_ops": 1,
            "applied_check": lambda old, new: any(
                v.get("view_type") == "isometric" for v in new.get("views", [])
            ),
        },
    },
    # --- drawing_ai: 속성 변경 ---
    {
        "id": 8,
        "project_type": "drawing_ai",
        "edit_request": "도면을 PDF로 내보내줘",
        "base_params": {"output_format": "svg", "views": [], "source_design": {"project_id": "test"}},
        "checks": {
            "min_ops": 1,
            "expected_path": "/output_format",
            "expected_value": "pdf",
        },
    },
]


# ---------------------------------------------------------------------------
# 평가 결과
# ---------------------------------------------------------------------------

@dataclass
class PatchEvalResult:
    total: int = 0
    ops_ok: int = 0        # min_ops 만족
    path_ok: int = 0       # expected_path 명중
    value_ok: int = 0      # expected_value 일치
    apply_ok: int = 0      # applied_check 통과
    path_expected: int = 0
    value_expected: int = 0
    apply_expected: int = 0
    failures: list[dict] = field(default_factory=list)

    @property
    def ops_rate(self) -> float:
        return self.ops_ok / self.total if self.total else 0

    @property
    def path_rate(self) -> float:
        return self.path_ok / self.path_expected if self.path_expected else 1.0

    @property
    def value_rate(self) -> float:
        return self.value_ok / self.value_expected if self.value_expected else 1.0

    @property
    def apply_rate(self) -> float:
        return self.apply_ok / self.apply_expected if self.apply_expected else 1.0

    def summary(self) -> dict:
        return {
            "total": self.total,
            "ops_generated": f"{round(self.ops_rate * 100, 1)}%",
            "path_accuracy": f"{round(self.path_rate * 100, 1)}%",
            "value_accuracy": f"{round(self.value_rate * 100, 1)}%",
            "apply_correctness": f"{round(self.apply_rate * 100, 1)}%",
            "failure_count": len(self.failures),
        }


# ---------------------------------------------------------------------------
# 평가 실행
# ---------------------------------------------------------------------------

def run_evaluation() -> PatchEvalResult:
    import copy
    registry = SchemaRegistry()
    result = PatchEvalResult()

    for case in PATCH_TEST_CASES:
        result.total += 1
        case_id = case["id"]
        project_type = case["project_type"]
        checks = case["checks"]
        base_params = copy.deepcopy(case["base_params"])

        parser = IntentParserModule(registry, project_type)
        patcher = DeltaPatchInterpreter(registry, project_type)

        intent = parser.parse(case["edit_request"])
        patch = patcher.interpret(case["edit_request"], base_params, intent)
        new_params = patcher.apply(base_params, patch)

        # 1. ops 수 체크
        if len(patch.operations) >= checks["min_ops"]:
            result.ops_ok += 1
        else:
            result.failures.append({
                "id": case_id,
                "check": "ops_count",
                "expected": f">={checks['min_ops']}",
                "actual": len(patch.operations),
                "request": case["edit_request"],
            })

        # 2. expected_path 체크
        if "expected_path" in checks:
            result.path_expected += 1
            paths = [op.path for op in patch.operations]
            if checks["expected_path"] in paths:
                result.path_ok += 1
            else:
                result.failures.append({
                    "id": case_id,
                    "check": "path",
                    "expected": checks["expected_path"],
                    "actual": paths,
                    "request": case["edit_request"],
                })

        # 3. expected_value 체크 (적용 후 값 확인)
        if "expected_value" in checks and "expected_path" in checks:
            result.value_expected += 1
            actual_val = _get_at_path(new_params, checks["expected_path"])
            if actual_val == checks["expected_value"]:
                result.value_ok += 1
            else:
                result.failures.append({
                    "id": case_id,
                    "check": "value",
                    "path": checks["expected_path"],
                    "expected": checks["expected_value"],
                    "actual": actual_val,
                    "request": case["edit_request"],
                })

        # 4. applied_check (커스텀 람다)
        if "applied_check" in checks:
            result.apply_expected += 1
            try:
                passed = checks["applied_check"](base_params, new_params)
                if passed:
                    result.apply_ok += 1
                else:
                    result.failures.append({
                        "id": case_id,
                        "check": "apply_lambda",
                        "request": case["edit_request"],
                        "detail": "custom check returned False",
                    })
            except Exception as e:
                result.failures.append({
                    "id": case_id,
                    "check": "apply_lambda",
                    "request": case["edit_request"],
                    "detail": str(e),
                })

    return result


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_report(result: PatchEvalResult) -> None:
    summary = result.summary()
    print("=" * 60)
    print("Delta Patch Evaluation Report")
    print("=" * 60)
    print(f"  Total test cases: {summary['total']}")
    print(f"  Ops generated: {summary['ops_generated']}")
    print(f"  Path accuracy: {summary['path_accuracy']}")
    print(f"  Value accuracy: {summary['value_accuracy']}")
    print(f"  Apply correctness: {summary['apply_correctness']}")
    print(f"  Failures: {summary['failure_count']}")

    if result.failures:
        print()
        print("--- Failures ---")
        for f in result.failures:
            print(f"  #{f['id']} [{f['check']}] \"{f.get('request', '')}\"")
            for k, v in f.items():
                if k not in ("id", "check", "request"):
                    print(f"    {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    result = run_evaluation()
    print_report(result)
