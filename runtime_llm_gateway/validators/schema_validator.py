"""
validators/schema_validator.py - JSON Schema 검증

LLM 출력이 형식적으로 올바른지 검증한다.
jsonschema 패키지가 없으면 기본 키 존재 체크만 수행.
"""

from __future__ import annotations

from typing import Any


def validate_json_schema(content: dict, schema: dict) -> tuple[bool, list[str]]:
    """JSON Schema 검증. (ok, errors) 반환."""
    try:
        import jsonschema
        v = jsonschema.Draft202012Validator(schema)
        errors = [e.message for e in v.iter_errors(content)]
        return len(errors) == 0, errors[:5]
    except ImportError:
        return _basic_validate(content, schema)


def _basic_validate(content: dict, schema: dict) -> tuple[bool, list[str]]:
    """jsonschema 없을 때 기본 키 체크"""
    errors = []
    required = schema.get("required", [])
    for key in required:
        if key not in content:
            errors.append(f"Missing required field: {key}")
    return len(errors) == 0, errors
