"""ID 생성"""
from uuid import uuid4


def new_request_id() -> str:
    return f"req_{uuid4().hex[:12]}"


def new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"
