"""
routing/task_router.py - 요청을 모델 풀/프로필로 라우팅
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ..core.envelope import RequestEnvelope
from ..core.model_profile import ModelProfile, DEFAULT_PROFILES
from ..core.task_type import TASK_POOL_MAP


class TaskRouter:
    """RequestEnvelope → ModelProfile 선택"""

    def __init__(self, profiles: Optional[dict[str, ModelProfile]] = None):
        self.profiles = profiles or DEFAULT_PROFILES

    def resolve_profile(self, request: RequestEnvelope) -> ModelProfile:
        # 1. task_type → pool 매핑
        pool_name = TASK_POOL_MAP.get(request.task_type, "strict-json-pool")

        # 2. latency budget로 보정
        if request.latency_budget_ms < 1500 and "fast-chat-pool" in self.profiles:
            pool_name = "fast-chat-pool"

        # 3. priority 보정
        if request.priority == "high" and pool_name == "fast-chat-pool":
            pool_name = "strict-json-pool"  # high priority는 정확도 우선

        # 4. 프로필 반환
        profile = self.profiles.get(pool_name)
        if profile is None:
            profile = self.profiles.get("strict-json-pool", list(self.profiles.values())[0])

        return profile


class ShardSelector:
    """prefix caching 효율을 위한 sticky routing"""

    def __init__(self, shard_count: int = 4):
        self.shard_count = shard_count

    def select(self, project_id: str, session_id: str, pool_name: str) -> str:
        """같은 project+session → 같은 shard"""
        key = f"{pool_name}:{project_id}:{session_id}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        shard_index = int(digest[:8], 16) % self.shard_count
        return f"{pool_name}-shard-{shard_index}"
