"""
router.py - 입력 → domain/task 결정 + 실행 경로 선택
"""
from __future__ import annotations

from ..core.contracts import TaskRequest
from ..core.errors import ValidationError
from ..domain.registry import get_task_spec, TaskSpec


class Router:
    """TaskRequest → TaskSpec 매핑"""

    def resolve(self, request: TaskRequest) -> TaskSpec:
        spec = get_task_spec(request.task_type)
        if spec is None:
            raise ValidationError(f"Unknown task_type: {request.task_type}")
        if not spec.enabled:
            raise ValidationError(f"Task disabled: {request.task_type}")
        return spec
