"""
execution/output_stabilizer.py - LLM 출력 안정화 레이어

파이프라인: raw_text → extract → repair → validate → result

방어 대상:
  A. Builder: spaces[] 복잡 배열, enum 불일치, 중첩 객체 누락
  B. Animation: markdown 펜스, 앞뒤 설명문, trailing comma
  C. CAD/공통: malformed JSON, 부분 출력, type mismatch
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Stage 1: JSON 추출 (raw text → clean JSON string)
# ---------------------------------------------------------------------------

def extract_json(text: str) -> str:
    """LLM 출력에서 JSON 부분만 추출.

    처리 순서:
    1. markdown 펜스 제거 (```json ... ```)
    2. 앞뒤 설명문 제거
    3. 중첩 JSON 객체/배열 찾기
    """
    if not text or not text.strip():
        raise ValueError("Empty output")

    text = text.strip()

    # 1. markdown 펜스 제거
    if "```" in text:
        match = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    # 2. 순수 JSON이면 바로 반환
    if text.startswith("{") or text.startswith("["):
        return text

    # 3. 텍스트 안에서 가장 바깥 JSON 객체 찾기 (balanced brace)
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    # 4. 배열 찾기
    start = text.find("[")
    if start >= 0:
        end = text.rfind("]") + 1
        if end > start:
            return text[start:end]

    raise ValueError(f"No JSON found in: {text[:100]}...")


# ---------------------------------------------------------------------------
# Stage 2: JSON 수리 (common syntax errors)
# ---------------------------------------------------------------------------

def repair_json_syntax(text: str) -> str:
    """흔한 JSON 문법 오류 수정.

    수리 대상:
    - trailing comma: {"a":1,} → {"a":1}
    - single quotes: {'a':'b'} → {"a":"b"}
    - unquoted keys: {a: "b"} → {"a": "b"}
    - truncated output: {"a":1, "b → {"a":1}
    """
    if not text:
        return text

    # trailing comma before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # single quotes → double quotes (간단한 케이스만)
    # 주의: 문자열 내부 quote는 건드리지 않음
    if "'" in text and '"' not in text:
        text = text.replace("'", '"')

    # truncated output 복구: 열린 brace 수 맞추기
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    if open_braces > 0:
        # 마지막 완전한 key-value 쌍까지 잘라내기
        last_comma = text.rfind(",")
        last_brace = text.rfind("}")
        if last_comma > last_brace:
            text = text[:last_comma]
        text += "}" * open_braces

    if open_brackets > 0:
        text += "]" * open_brackets

    return text


# ---------------------------------------------------------------------------
# Stage 3: Schema-aware 수리
# ---------------------------------------------------------------------------

def repair_schema_aware(content: dict, schema: dict) -> dict:
    """스키마 기반 필드 수리.

    수리 대상:
    - required 필드 누락 → 기본값 채움
    - enum 불일치 → closest valid value
    - type 불일치 → 강제 변환
    - array 대신 object → 배열로 감싸기
    """
    if not schema or schema.get("type") != "object":
        return content

    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    for field in required:
        if field not in content:
            # 필드 누락: 기본값 생성
            field_schema = props.get(field, {})
            content[field] = _default_for_schema(field_schema)

    for field, field_schema in props.items():
        if field not in content:
            continue

        value = content[field]
        expected_type = field_schema.get("type")

        # enum 불일치 → closest valid
        if "enum" in field_schema and value not in field_schema["enum"]:
            closest = _closest_enum(value, field_schema["enum"])
            if closest:
                content[field] = closest

        # array인데 object가 들어옴 → 감싸기
        if expected_type == "array" and isinstance(value, dict):
            content[field] = [value]

        # object인데 배열이 들어옴 → 첫 원소 사용
        if expected_type == "object" and isinstance(value, list) and value:
            content[field] = value[0]

        # nested object → 재귀 수리
        if expected_type == "object" and isinstance(content[field], dict):
            content[field] = repair_schema_aware(content[field], field_schema)

        # integer인데 string → 변환
        if expected_type == "integer" and isinstance(value, str):
            try:
                content[field] = int(re.sub(r"[^\d-]", "", value))
            except (ValueError, TypeError):
                pass

        # number인데 string → 변환
        if expected_type == "number" and isinstance(value, str):
            try:
                content[field] = float(re.sub(r"[^\d.\-]", "", value))
            except (ValueError, TypeError):
                pass

        # number/integer 범위 보정
        if expected_type in ("number", "integer") and isinstance(content[field], (int, float)):
            if "minimum" in field_schema and content[field] < field_schema["minimum"]:
                content[field] = field_schema["minimum"]
            if "maximum" in field_schema and content[field] > field_schema["maximum"]:
                content[field] = field_schema["maximum"]

    return content


def _default_for_schema(schema: dict) -> Any:
    """스키마에서 기본값 생성"""
    t = schema.get("type", "string")
    if "default" in schema:
        return schema["default"]
    if t == "string":
        return schema["enum"][0] if "enum" in schema else ""
    if t == "integer":
        return schema.get("minimum", 0)
    if t == "number":
        return float(schema.get("minimum", 0.0))
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    if isinstance(t, list):
        for sub in t:
            if sub != "null":
                return _default_for_schema({"type": sub})
        return None
    return None


def _closest_enum(value: Any, enum_values: list) -> Optional[Any]:
    """가장 가까운 enum 값 찾기"""
    if not enum_values:
        return None
    if not isinstance(value, str):
        return enum_values[0]

    value_lower = str(value).lower().strip()
    for ev in enum_values:
        if str(ev).lower() == value_lower:
            return ev
    # 부분 일치
    for ev in enum_values:
        if value_lower in str(ev).lower() or str(ev).lower() in value_lower:
            return ev
    return enum_values[0]


# ---------------------------------------------------------------------------
# 통합 파이프라인
# ---------------------------------------------------------------------------

@dataclass
class StabilizationMetrics:
    """수리 효과 측정 메트릭."""
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    syntax_repairs: int = 0
    schema_repairs: int = 0
    extract_failures: int = 0
    parse_failures: int = 0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_calls if self.total_calls else 0.0

    @property
    def repair_rate(self) -> float:
        """수리가 필요했던 비율 (성공 중)."""
        repaired = self.syntax_repairs + self.schema_repairs
        return repaired / self.total_calls if self.total_calls else 0.0

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 3),
            "repair_rate": round(self.repair_rate, 3),
            "repairs": {
                "syntax": self.syntax_repairs,
                "schema": self.schema_repairs,
            },
            "failures": {
                "extract": self.extract_failures,
                "parse": self.parse_failures,
            },
        }


# 모듈 수준 싱글턴 메트릭
_metrics = StabilizationMetrics()


def get_stabilization_metrics() -> StabilizationMetrics:
    """현재 메트릭 조회."""
    return _metrics


def reset_stabilization_metrics() -> None:
    """메트릭 초기화 (테스트용)."""
    global _metrics
    _metrics = StabilizationMetrics()


def stabilize_output(raw_text: str, schema: dict) -> tuple[dict | None, str, list[str]]:
    """
    전체 안정화 파이프라인.

    Returns:
        (parsed_dict or None, cleaned_text, repair_log)
    """
    _metrics.total_calls += 1
    repairs: list[str] = []

    # Stage 1: 추출
    try:
        extracted = extract_json(raw_text)
    except ValueError as e:
        repairs.append(f"extract_failed: {e}")
        _metrics.failure_count += 1
        _metrics.extract_failures += 1
        return None, raw_text, repairs

    # Stage 2: 문법 수리
    repaired = repair_json_syntax(extracted)
    if repaired != extracted:
        repairs.append("syntax_repaired")
        _metrics.syntax_repairs += 1

    # Stage 3: 파싱
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError as e:
        repairs.append(f"parse_failed_after_repair: {e}")
        _metrics.failure_count += 1
        _metrics.parse_failures += 1
        return None, repaired, repairs

    # Stage 4: 스키마 수리
    if isinstance(parsed, dict) and schema:
        before = json.dumps(parsed, sort_keys=True)
        parsed = repair_schema_aware(parsed, schema)
        after = json.dumps(parsed, sort_keys=True)
        if before != after:
            repairs.append("schema_aware_repair")
            _metrics.schema_repairs += 1

    _metrics.success_count += 1
    return parsed, repaired, repairs
