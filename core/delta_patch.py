"""
core/delta_patch.py — Delta Patch Interpreter

수정 요청을 전체 재생성 없이 파라미터 단위 패치로 변환.

v1: 키워드 → 파라미터 경로 매핑 테이블 (schema_registry.path_aliases 활용)
v2: 로컬 LLM이 수정 요청을 PatchOperation[] JSON으로 변환
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

from .models import DeltaPatch, PatchOperation, ParsedIntent, _new_id
from .schema_registry import SchemaRegistry


# ---------------------------------------------------------------------------
# 상대값 패턴
# ---------------------------------------------------------------------------

_RELATIVE_INCREASE = [
    r"더\s*(?:크게|넓게|높게|길게|두껍게|굵게|늘려|키워|높여|넓혀)",
    r"증가", r"올려", r"키워",
]
_RELATIVE_DECREASE = [
    r"더\s*(?:작게|좁게|낮게|짧게|얇게|가늘게|줄여|낮춰|좁혀)",
    r"감소", r"내려", r"줄여",
]

_AMOUNT_PATTERN = r"(\d+(?:\.\d+)?)\s*(?:mm|%|도|deg|kpa|ml)"
_RELATIVE_VAGUE = r"(?:좀|조금|살짝|약간|많이|훨씬)"


class DeltaPatchInterpreter:

    def __init__(self, schema_registry: SchemaRegistry, project_type: str):
        self.schema_registry = schema_registry
        self.project_type = project_type
        self.llm_backend = None  # v2에서 설정

    def interpret(
        self,
        user_edit_request: str,
        current_params: dict,
        intent: Optional[ParsedIntent] = None,
    ) -> DeltaPatch:
        """
        수정 요청을 DeltaPatch로 변환.
        v1: 규칙 기반
        """
        if self.llm_backend is not None:
            return self._llm_interpret(user_edit_request, current_params, intent)

        return self._rule_based_interpret(user_edit_request, current_params, intent)

    def apply(self, base_params: dict, patch: DeltaPatch) -> dict:
        """patch를 base_params에 적용하여 새 params 반환. base_params는 변경하지 않음."""
        result = copy.deepcopy(base_params)
        for op in patch.operations:
            self._apply_operation(result, op)
        return result

    # ------------------------------------------------------------------
    # v1: 규칙 기반 해석
    # ------------------------------------------------------------------

    def _rule_based_interpret(
        self,
        user_edit_request: str,
        current_params: dict,
        intent: Optional[ParsedIntent],
    ) -> DeltaPatch:
        text = user_edit_request.strip()
        operations: list[PatchOperation] = []

        # 1. 절대값 치수 변경 감지: "폭을 360mm로 바꿔줘"
        absolute_ops = self._extract_absolute_values(text)
        operations.extend(absolute_ops)

        # 2. 상대값 변경 감지: "높이를 좀 더 높여줘", "폭을 20mm 늘려줘"
        if not absolute_ops:
            relative_ops = self._extract_relative_changes(text, current_params)
            operations.extend(relative_ops)

        # 3. 속성 변경 감지: "예산을 낮춰줘", "제작 방식을 3D프린팅으로"
        property_ops = self._extract_property_changes(text)
        operations.extend(property_ops)

        # 4. 중복 제거 (같은 path에 대한 연산이 여러 개면 마지막만)
        operations = self._deduplicate(operations)

        base_id = ""
        if intent and intent.reference_id:
            base_id = intent.reference_id

        return DeltaPatch(
            patch_id=_new_id("patch"),
            base_variant_id=base_id,
            operations=operations,
            description=text,
        )

    def _extract_absolute_values(self, text: str) -> list[PatchOperation]:
        """'폭을 360mm로' 같은 절대값 설정 추출"""
        ops = []
        # 패턴: {대상}을/를 {숫자}mm로
        pattern = r"([\w\s]+?)(?:을|를|은|는)?\s*(\d{2,4})\s*mm\s*(?:로|으로)"
        for match in re.finditer(pattern, text):
            target_word = match.group(1).strip()
            value = int(match.group(2))
            path = self.schema_registry.resolve_alias(self.project_type, target_word)
            if path:
                ops.append(PatchOperation(
                    op_type="set",
                    path=path,
                    value=value,
                    relative=False,
                ))
        return ops

    def _extract_relative_changes(
        self, text: str, current_params: dict
    ) -> list[PatchOperation]:
        """'높이를 좀 더 높여줘', '폭을 20mm 줄여줘' 같은 상대 변경 추출"""
        ops = []

        # 수치 포함 상대 변경: "폭을 20mm 늘려줘"
        pattern_with_amount = r"([\w\s]+?)(?:을|를)?\s*(\d+(?:\.\d+)?)\s*mm\s*(늘려|줄여|키워|낮춰|올려|내려|넓혀|좁혀)"
        for match in re.finditer(pattern_with_amount, text):
            target_word = match.group(1).strip()
            amount = float(match.group(2))
            direction_word = match.group(3)
            path = self.schema_registry.resolve_alias(self.project_type, target_word)
            if path:
                is_decrease = direction_word in ("줄여", "낮춰", "내려", "좁혀")
                delta = -amount if is_decrease else amount
                ops.append(PatchOperation(
                    op_type="adjust",
                    path=path,
                    value=delta,
                    relative=True,
                ))

        if ops:
            return ops

        # 수치 없는 상대 변경: "높이를 좀 더 높여줘"
        is_increase = any(re.search(p, text) for p in _RELATIVE_INCREASE)
        is_decrease = any(re.search(p, text) for p in _RELATIVE_DECREASE)

        if is_increase or is_decrease:
            # 대상 경로 찾기
            aliases = self.schema_registry.get_path_aliases(self.project_type)
            for alias_key, path in aliases.items():
                if alias_key in text:
                    # 현재값의 10%를 기본 delta로 사용
                    current_val = _get_at_path(current_params, path)
                    if isinstance(current_val, (int, float)) and current_val > 0:
                        delta = current_val * 0.1
                        if is_decrease:
                            delta = -delta

                        # "많이" / "훨씬" → 20%, "살짝"/"약간" → 5%
                        if re.search(r"많이|훨씬", text):
                            delta *= 2
                        elif re.search(r"살짝|약간", text):
                            delta *= 0.5

                        ops.append(PatchOperation(
                            op_type="adjust",
                            path=path,
                            value=round(delta, 1),
                            relative=True,
                        ))
                    break

        return ops

    def _extract_property_changes(self, text: str) -> list[PatchOperation]:
        """'예산을 낮춰줘', '제작 방식을 3D프린팅으로', '단면도 추가' 같은 속성/배열 변경"""
        ops = []
        constraint_map = self.schema_registry.get_constraint_mapping(self.project_type)

        text_lower = text.lower()
        for keyword, mapping in constraint_map.items():
            if keyword in text or keyword.lower() in text_lower:
                for field_path, value in mapping.items():
                    # "{array_key}_add" → op_type="add", path="/{array_key}"
                    if field_path.endswith("_add"):
                        base_key = field_path[:-4]  # strip "_add"
                        clean_path = "/" + base_key if not base_key.startswith("/") else base_key
                        ops.append(PatchOperation(
                            op_type="add",
                            path=clean_path,
                            value=value,
                            relative=False,
                        ))
                    else:
                        # array 접근자 제거하고 단순화
                        clean_path = re.sub(r"\[\*\]", "", field_path)
                        if not clean_path.startswith("/"):
                            clean_path = "/" + clean_path.replace(".", "/")
                        ops.append(PatchOperation(
                            op_type="set",
                            path=clean_path,
                            value=value,
                            relative=False,
                        ))

        return ops

    def _deduplicate(self, operations: list[PatchOperation]) -> list[PatchOperation]:
        """같은 path에 대한 연산이 여러 개면 마지막만 유지"""
        seen: dict[str, PatchOperation] = {}
        for op in operations:
            seen[op.path] = op
        return list(seen.values())

    # ------------------------------------------------------------------
    # 패치 적용
    # ------------------------------------------------------------------

    def _apply_operation(self, params: dict, op: PatchOperation) -> None:
        """단일 operation을 params에 적용"""
        if op.op_type == "set":
            _set_at_path(params, op.path, op.value)
        elif op.op_type == "adjust":
            current = _get_at_path(params, op.path)
            if isinstance(current, (int, float)) and op.relative:
                new_val = current + op.value
                # int 경로면 int로 유지
                if isinstance(current, int):
                    new_val = int(round(new_val))
                _set_at_path(params, op.path, new_val)
            elif op.value is not None:
                _set_at_path(params, op.path, op.value)
        elif op.op_type == "remove":
            _remove_at_path(params, op.path)
        elif op.op_type == "add":
            _append_at_path(params, op.path, op.value)

    # ------------------------------------------------------------------
    # v2: LLM 기반 해석 (placeholder)
    # ------------------------------------------------------------------

    def _llm_interpret(
        self,
        user_edit_request: str,
        current_params: dict,
        intent: Optional[ParsedIntent],
    ) -> DeltaPatch:
        """
        v2: 로컬 LLM + constrained decoding으로 PatchOperation[] 생성.
        규칙 기반 결과를 힌트로 사용. 실패 시 규칙 기반 fallback.
        """
        # 규칙 기반 결과를 힌트로
        rule_result = self._rule_based_interpret(user_edit_request, current_params, intent)

        common = self.schema_registry.get_common_schema()
        patch_schema = common.get("delta_patch_schema", {})

        try:
            result = self.llm_backend.interpret_patch(
                edit_request=user_edit_request,
                current_params=current_params,
                patch_schema=patch_schema,
            )
            return DeltaPatch.from_dict(result)
        except Exception:
            return rule_result


# ---------------------------------------------------------------------------
# JSON path 유틸리티 (/ 구분 경로)
# ---------------------------------------------------------------------------

def _parse_path(path: str) -> list[str]:
    """'/a/b/c' → ['a', 'b', 'c']"""
    parts = path.strip("/").split("/")
    return [p for p in parts if p]


def _get_at_path(obj: dict, path: str) -> Any:
    parts = _parse_path(path)
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _set_at_path(obj: dict, path: str, value: Any) -> None:
    parts = _parse_path(path)
    current = obj
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    if parts:
        current[parts[-1]] = value


def _remove_at_path(obj: dict, path: str) -> None:
    parts = _parse_path(path)
    current = obj
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return
    if parts and isinstance(current, dict):
        current.pop(parts[-1], None)


def _append_at_path(obj: dict, path: str, value: Any) -> None:
    parts = _parse_path(path)
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return
    if isinstance(current, list):
        current.append(value)
