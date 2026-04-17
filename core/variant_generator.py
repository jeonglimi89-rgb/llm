"""
core/variant_generator.py — 후보안 생성기

ParsedIntent → Variant[] 파라미터 조합 생성.

핵심: LLM이 최종 결과를 만드는 게 아니라 "파라미터 조합"을 제안.
실제 결과물은 프로젝트 엔진이 렌더링.

항상 복수 후보안 생성. 단일 안 출력 금지.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from .models import IntentType, ParsedIntent, Variant, _new_id
from .schema_registry import SchemaRegistry


class VariantGeneratorModule:

    def __init__(self, schema_registry: SchemaRegistry, project_type: str):
        self.schema_registry = schema_registry
        self.project_type = project_type

    def generate(
        self,
        intent: ParsedIntent,
        base_params: Optional[dict] = None,
        n_variants: int = 3,
        diversity_weight: float = 0.5,
    ) -> list[Variant]:
        """
        ParsedIntent + 기존 파라미터 → n개 후보 Variant 생성.

        diversity_weight:
          0.0 = 모든 안이 가장 안전한 방향
          1.0 = 의도적으로 대비되는 안 포함
        """
        if base_params is None:
            base_params = {}

        # intent의 constraints를 base_params에 오버레이
        merged = self._apply_constraints(base_params, intent.constraints)

        variants = []

        # 전략 1: variant_axes 기반 확장
        axes_variants = self._expand_by_axes(merged, intent, n_variants, diversity_weight)
        variants.extend(axes_variants)

        # 전략 2: constraint relaxation (제약 완화 변이)
        if diversity_weight > 0.3 and len(variants) < n_variants:
            relaxed = self._relax_constraints(merged, intent)
            variants.extend(relaxed)

        # 전략 3: 대비안 (의도적으로 다른 방향)
        if diversity_weight > 0.6 and len(variants) < n_variants:
            contrast = self._generate_contrast(merged, intent)
            if contrast:
                variants.append(contrast)

        # 중복 제거 + n_variants만큼 자르기
        variants = self._deduplicate(variants)[:n_variants]

        # 최소 보장: variants가 비어있으면 base를 그대로 1개 반환
        if not variants:
            variants = [Variant(
                params=merged,
                description="기본 파라미터 (변이 없음)",
                generation_method="fallback",
                tags=["default"],
            )]

        return variants

    # ------------------------------------------------------------------
    # 내부 전략
    # ------------------------------------------------------------------

    def _apply_constraints(self, base: dict, constraints: dict) -> dict:
        """intent.constraints를 base_params에 오버레이"""
        result = copy.deepcopy(base)
        for key, value in constraints.items():
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            if parts:
                current[parts[-1]] = value
        return result

    def _expand_by_axes(
        self,
        base: dict,
        intent: ParsedIntent,
        n: int,
        diversity: float,
    ) -> list[Variant]:
        """variant_axes를 따라 변이 생성"""
        axes = self.schema_registry.get_variant_axes(self.project_type)
        if not axes:
            return []

        variants = []

        for i, axis in enumerate(axes[:n]):
            axis_name = axis.get("axis", f"axis_{i}")
            values = axis.get("values", [])
            if not values or values == ["template_dependent"]:
                continue

            for j, val in enumerate(values[:n]):
                if len(variants) >= n:
                    break

                variant_params = copy.deepcopy(base)

                # 축별 파라미터 변이 적용
                diff = self._axis_value_to_diff(axis_name, val, base)
                for path, new_val in diff.items():
                    parts = path.split(".")
                    current = variant_params
                    for part in parts[:-1]:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    if parts:
                        current[parts[-1]] = new_val

                tag = f"{axis_name}:{val}"
                variants.append(Variant(
                    params=variant_params,
                    description=f"{axis.get('description', axis_name)} - {val}",
                    diff_from_base=diff,
                    generation_method="rule_expansion",
                    tags=[tag],
                ))

        return variants

    def _axis_value_to_diff(self, axis: str, value: str, base: dict) -> dict:
        """축 + 값 → 파라미터 차이"""
        diff: dict[str, Any] = {}

        if axis == "complexity":
            diff["concept.estimated_complexity"] = value
        elif axis == "cost":
            diff["concept.estimated_cost_level"] = value
            if value == "low":
                diff["requirement.budget_level"] = "low"
            elif value == "high":
                diff["requirement.budget_level"] = "high"
        elif axis == "material":
            diff["_material_hint"] = value
        elif axis == "view_set":
            # Drawing AI 전용
            if value == "minimal":
                diff["_view_count"] = 1
            elif value == "standard":
                diff["_view_count"] = 3
            elif value == "detailed":
                diff["_view_count"] = 5
        elif axis == "annotation_density":
            diff["_annotation_level"] = value
        elif axis == "layer_visibility":
            diff["_layer_mode"] = value

        return diff

    def _relax_constraints(self, base: dict, intent: ParsedIntent) -> list[Variant]:
        """제약 조건 하나를 완화한 변이"""
        if not intent.constraints:
            return []

        variants = []
        for key in list(intent.constraints.keys())[:1]:  # 첫 제약만
            relaxed_params = copy.deepcopy(base)
            # 해당 제약을 제거
            parts = key.split(".")
            current = relaxed_params
            for part in parts[:-1]:
                if isinstance(current, dict) and part in current:
                    current = current[part]
            if parts and isinstance(current, dict):
                current.pop(parts[-1], None)

            variants.append(Variant(
                params=relaxed_params,
                description=f"제약 완화: {key} 제거",
                diff_from_base={key: "REMOVED"},
                generation_method="constraint_relaxation",
                tags=["relaxed"],
            ))

        return variants

    def _generate_contrast(self, base: dict, intent: ParsedIntent) -> Optional[Variant]:
        """의도적으로 대비되는 안 생성"""
        contrast_params = copy.deepcopy(base)
        diff: dict[str, Any] = {}

        # 복잡도 반전
        complexity = (
            base.get("concept", {}).get("estimated_complexity", "medium")
        )
        opposite = {"low": "high", "medium": "low", "high": "low"}
        new_complexity = opposite.get(complexity, "medium")
        if "concept" not in contrast_params:
            contrast_params["concept"] = {}
        contrast_params["concept"]["estimated_complexity"] = new_complexity
        diff["concept.estimated_complexity"] = new_complexity

        return Variant(
            params=contrast_params,
            description=f"대비안: 복잡도 {complexity} → {new_complexity}",
            diff_from_base=diff,
            generation_method="contrastive",
            tags=["contrast", "bold"],
        )

    def _deduplicate(self, variants: list[Variant]) -> list[Variant]:
        """params가 동일한 variant 제거"""
        seen: list[str] = []
        unique: list[Variant] = []
        for v in variants:
            key = str(sorted(v.params.items()) if isinstance(v.params, dict) else v.params)
            if key not in seen:
                seen.append(key)
                unique.append(v)
        return unique
