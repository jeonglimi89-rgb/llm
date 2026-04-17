"""
domain/schema_enforcer.py — Hard schema validation per domain.

DomainEvaluator 의 soft quality 평가와 별도로, 출력의 구조적 무결성을
hard-fail 로 검사. 누락 필드 / 타입 drift / 구조 drift 를 분리 기록.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SchemaValidationResult:
    passed: bool = True
    missing_fields: list[str] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    structure_issues: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[str]:
        return self.missing_fields + self.type_errors + self.structure_issues

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "missing_fields": self.missing_fields,
            "type_errors": self.type_errors,
            "structure_issues": self.structure_issues,
        }


# Per-domain required output structure.
# "key" → expected type string ("dict", "list", "str", "int", "float", "any")
# Nested: "key.subkey" → type
_DOMAIN_SCHEMAS: dict[str, dict[str, str]] = {
    "cad": {
        "constraints": "list",
    },
    "builder": {
        "floors": "int",
        "spaces": "list",
    },
    "minecraft": {
        "target_anchor": "dict",
        "operations": "list",
    },
    "animation": {
        "framing": "str",
        "mood": "str",
    },
}

# Extended schema (recommended but not hard-fail)
_DOMAIN_RECOMMENDED: dict[str, list[str]] = {
    "cad": ["systems", "parts", "interfaces", "validation_points"],
    "builder": ["preferences", "code_checks", "mep_requirements"],
    "minecraft": ["block_palette", "structure_rules", "preserve"],
    "animation": ["camera_plan", "continuity_anchors", "speed", "emotion_hint"],
}

_TYPE_MAP = {
    "dict": dict,
    "list": list,
    "str": str,
    "int": (int, float),  # int accepts float too
    "float": (int, float),
    "any": object,
}


class SchemaEnforcer:
    """도메인별 hard schema validation."""

    def validate(self, domain: str, output: Optional[dict]) -> SchemaValidationResult:
        result = SchemaValidationResult()

        if output is None:
            result.passed = False
            result.structure_issues.append("output is None")
            return result

        if not isinstance(output, dict):
            result.passed = False
            result.type_errors.append(f"output is {type(output).__name__}, expected dict")
            return result

        # Hard required fields
        schema = _DOMAIN_SCHEMAS.get(domain, {})
        for key, expected_type in schema.items():
            if key not in output:
                result.missing_fields.append(key)
                result.passed = False
            else:
                expected = _TYPE_MAP.get(expected_type, object)
                if not isinstance(output[key], expected):
                    result.type_errors.append(
                        f"{key}: expected {expected_type}, got {type(output[key]).__name__}"
                    )
                    result.passed = False

        # Recommended fields (soft — logged but don't fail)
        recommended = _DOMAIN_RECOMMENDED.get(domain, [])
        for key in recommended:
            if key not in output:
                result.structure_issues.append(f"recommended field missing: {key}")

        return result
