"""
core/schema_registry.py — 스키마 레지스트리

모든 모듈이 공유하는 스키마 정의 저장소.
- 프로젝트마다 다른 engine_params 스키마를 등록
- Intent/Critique/Patch의 상위 구조는 공통
- 자연어 → 파라미터 경로 매핑(path_aliases) 관리
- JSON Schema 검증
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

import jsonschema


class SchemaRegistry:

    def __init__(self, schemas_dir: Optional[str] = None):
        if schemas_dir is None:
            schemas_dir = str(Path(__file__).parent / "schemas")
        self.schemas_dir = schemas_dir
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 로딩
    # ------------------------------------------------------------------

    def _load(self, name: str) -> dict:
        if name in self._cache:
            return self._cache[name]

        # 프로젝트별 스키마는 projects/ 하위에서 찾음
        candidates = [
            os.path.join(self.schemas_dir, f"{name}.json"),
            os.path.join(self.schemas_dir, "projects", f"{name}.json"),
        ]
        for path in candidates:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self._cache[name] = data
                return data

        raise FileNotFoundError(
            f"Schema '{name}' not found. Searched: {candidates}"
        )

    def reload(self, name: str) -> dict:
        self._cache.pop(name, None)
        return self._load(name)

    # ------------------------------------------------------------------
    # 프로젝트 등록/조회
    # ------------------------------------------------------------------

    def list_projects(self) -> list[str]:
        projects_dir = os.path.join(self.schemas_dir, "projects")
        if not os.path.isdir(projects_dir):
            return []
        return [
            f.replace(".json", "")
            for f in os.listdir(projects_dir)
            if f.endswith(".json") and not f.startswith("_")
        ]

    def get_project_schema(self, project_type: str) -> dict:
        return deepcopy(self._load(project_type))

    def get_engine_params_schema(self, project_type: str) -> dict:
        schema = self._load(project_type)
        return deepcopy(schema.get("engine_params", {}))

    def get_path_aliases(self, project_type: str) -> dict[str, str]:
        schema = self._load(project_type)
        return deepcopy(schema.get("path_aliases", {}))

    def get_constraint_mapping(self, project_type: str) -> dict[str, str]:
        schema = self._load(project_type)
        return deepcopy(schema.get("constraint_mapping", {}))

    def get_critique_criteria(self, project_type: str) -> list[dict]:
        schema = self._load(project_type)
        return deepcopy(schema.get("critique_criteria", []))

    def get_variant_axes(self, project_type: str) -> list[dict]:
        schema = self._load(project_type)
        return deepcopy(schema.get("variant_axes", []))

    # ------------------------------------------------------------------
    # 공통 스키마
    # ------------------------------------------------------------------

    def get_common_schema(self) -> dict:
        return deepcopy(self._load("_common"))

    def get_intent_schema(self, project_type: str) -> dict:
        common = self._load("_common")
        base_intent = deepcopy(common.get("intent_schema", {}))
        try:
            project = self._load(project_type)
            extensions = project.get("intent_extensions", {})
            if extensions:
                base_intent = _merge_schemas(base_intent, extensions)
        except FileNotFoundError:
            pass
        return base_intent

    def get_session_record_schema(self) -> dict:
        common = self._load("_common")
        return deepcopy(common.get("session_record_schema", {}))

    # ------------------------------------------------------------------
    # 검증
    # ------------------------------------------------------------------

    def validate_engine_params(self, project_type: str, params: dict) -> list[str]:
        schema = self.get_engine_params_schema(project_type)
        if not schema:
            return [f"No engine_params schema for project_type '{project_type}'"]
        errors = []
        try:
            jsonschema.validate(instance=params, schema=schema)
        except jsonschema.ValidationError as e:
            errors.append(f"Validation error at {list(e.absolute_path)}: {e.message}")
        except jsonschema.SchemaError as e:
            errors.append(f"Schema error: {e.message}")
        return errors

    def validate_intent(self, project_type: str, intent_dict: dict) -> list[str]:
        schema = self.get_intent_schema(project_type)
        if not schema:
            return []
        errors = []
        try:
            jsonschema.validate(instance=intent_dict, schema=schema)
        except jsonschema.ValidationError as e:
            errors.append(f"Intent validation error: {e.message}")
        return errors

    # ------------------------------------------------------------------
    # 경로 해석 헬퍼
    # ------------------------------------------------------------------

    def resolve_alias(self, project_type: str, user_expression: str) -> Optional[str]:
        """사용자 자연어 표현을 파라미터 JSON 경로로 변환"""
        aliases = self.get_path_aliases(project_type)
        user_expression_lower = user_expression.strip().lower()

        # 정확 매칭
        if user_expression_lower in aliases:
            return aliases[user_expression_lower]

        # 부분 매칭 (가장 긴 매칭 우선)
        matches = []
        for alias_key, path in aliases.items():
            if alias_key in user_expression_lower:
                matches.append((len(alias_key), path))
        if matches:
            matches.sort(reverse=True)
            return matches[0][1]

        return None


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _merge_schemas(base: dict, extension: dict) -> dict:
    """JSON Schema를 병합 (extension이 base를 확장)"""
    result = deepcopy(base)
    for key, value in extension.items():
        if key == "properties" and "properties" in result:
            result["properties"].update(value)
        elif key == "required" and "required" in result:
            result["required"] = list(set(result["required"]) | set(value))
        else:
            result[key] = deepcopy(value)
    return result
