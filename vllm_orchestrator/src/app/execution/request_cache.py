"""request_cache.py — In-memory LRU cache for task results.

대규모 운영에서 같은 user_input + task_type + context 조합이 반복되면
LLM 호출을 건너뛰고 캐시된 결과를 즉시 반환한다.

캐시 키: sha256(task_type | normalized_user_input | sorted_context_json)
캐시 값: TaskResult.to_dict() (slots + metadata 포함)
정책:
  - LRU eviction (OrderedDict)
  - TTL 기반 자동 만료
  - auto_validated=True 인 결과만 캐시 (실패 결과는 캐시 안 함)
  - task_type별 cache bypass 가능 (per-task 설정)

Thread-safe (단일 asyncio loop 가정 — 멀티스레드 사용 시 Lock 추가).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CacheEntry:
    value: dict
    created_at: float
    hit_count: int = 0

    def is_expired(self, ttl_s: float) -> bool:
        return (time.time() - self.created_at) > ttl_s


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired: int = 0
    stores: int = 0
    bypassed: int = 0

    def to_dict(self) -> dict:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total > 0 else 0.0
        return {
            "hits": self.hits, "misses": self.misses,
            "evictions": self.evictions, "expired": self.expired,
            "stores": self.stores, "bypassed": self.bypassed,
            "hit_rate": round(hit_rate, 3),
        }


def _normalize_input(s: str) -> str:
    """Lowercase + collapse whitespace + strip. 대소문자/공백 차이가 같은 요청으로 매핑되도록."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip().lower())


def _stable_context_key(ctx: Any) -> str:
    """Context dict를 sorted JSON으로 직렬화. 키 순서가 달라도 같은 캐시 키.
    `_` 로 시작하는 internal 키 (e.g. `_intent_analysis`) 는 제외 —
    dispatcher 내부에서 추가되는 값이라 cache key 안정성을 해치면 안 됨.
    Filter 후 빈 dict는 None/{}/"" 모두 같은 "" 로 정규화 (empty 동치)."""
    if not ctx:
        return ""
    try:
        if isinstance(ctx, dict):
            filtered = {k: v for k, v in ctx.items() if not (isinstance(k, str) and k.startswith("_"))}
            if not filtered:
                return ""
            return json.dumps(filtered, sort_keys=True, ensure_ascii=False, default=str)
        return json.dumps(ctx, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(ctx)[:200]


class RequestCache:
    """LRU + TTL + task-scoped cache for /tasks/submit results."""

    def __init__(
        self,
        max_entries: int = 1000,
        ttl_s: float = 3600.0,
        cacheable_task_types: Optional[set[str]] = None,
    ):
        """
        Args:
            max_entries: LRU 최대 항목 수. 초과 시 oldest evict.
            ttl_s: 항목 만료 시간 (초). 기본 1시간.
            cacheable_task_types: 캐시 대상 task_type set. None이면 모든 creative task.
        """
        self.max_entries = int(max_entries)
        self.ttl_s = float(ttl_s)
        # None → creative 기본 set. 명시적 set도 허용.
        self._cacheable: Optional[set[str]] = cacheable_task_types
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = CacheStats()

    # ── public API ──────────────────────────────────────────────────────

    def make_key(self, task_type: str, user_input: str, context: Any) -> str:
        """결정론적 cache key 생성."""
        payload = f"{task_type}|{_normalize_input(user_input)}|{_stable_context_key(context)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def is_cacheable(self, task_type: str) -> bool:
        if self._cacheable is None:
            # 기본: creative_json pool 계열만 캐시 (비싸고 재사용 가치 큼)
            return (
                task_type.startswith("minecraft.scene_graph")
                or task_type.startswith("minecraft.brainstorm")
                or task_type.startswith("minecraft.palette_only")
                or task_type.endswith(".planner")
                or task_type.endswith(".critic")
            )
        return task_type in self._cacheable

    def get(self, task_type: str, user_input: str, context: Any) -> Optional[dict]:
        """캐시 조회. 없거나 만료면 None."""
        try:
            from ..observability.metrics import observe_cache, update_cache_stats
        except Exception:
            def observe_cache(*a, **k):
                return None
            def update_cache_stats(*a, **k):
                return None

        if not self.is_cacheable(task_type):
            self.stats.bypassed += 1
            observe_cache(task_type, "bypass")
            return None
        key = self.make_key(task_type, user_input, context)
        entry = self._store.get(key)
        if entry is None:
            self.stats.misses += 1
            observe_cache(task_type, "miss")
            update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
            return None
        if entry.is_expired(self.ttl_s):
            self._store.pop(key, None)
            self.stats.expired += 1
            self.stats.misses += 1
            observe_cache(task_type, "expire")
            observe_cache(task_type, "miss")
            update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
            return None
        self._store.move_to_end(key)
        entry.hit_count += 1
        self.stats.hits += 1
        observe_cache(task_type, "hit")
        update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
        return entry.value

    def put(self, task_type: str, user_input: str, context: Any, value: dict) -> bool:
        """결과 저장. auto_validated=True 인 경우만 저장. 성공 시 True."""
        if not self.is_cacheable(task_type):
            return False
        # 실패한 결과는 캐시 금지 (품질 낮은 응답이 재사용되면 사용자 피해)
        jud = value.get("layered_judgment") or {}
        if not jud.get("auto_validated"):
            return False
        key = self.make_key(task_type, user_input, context)
        now = time.time()
        # 기존 항목 update 또는 신규 추가
        try:
            from ..observability.metrics import observe_cache, update_cache_stats
        except Exception:
            def observe_cache(*a, **k):
                return None
            def update_cache_stats(*a, **k):
                return None
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = CacheEntry(value=value, created_at=now)
        else:
            self._store[key] = CacheEntry(value=value, created_at=now)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)
                self.stats.evictions += 1
                observe_cache(task_type, "evict")
        self.stats.stores += 1
        observe_cache(task_type, "store")
        update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
        return True

    def invalidate(self, task_type: str, user_input: str, context: Any) -> bool:
        """특정 키 강제 제거. 성공 시 True."""
        key = self.make_key(task_type, user_input, context)
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)

    def stats_dict(self) -> dict:
        d = self.stats.to_dict()
        d["size"] = self.size()
        d["max_entries"] = self.max_entries
        d["ttl_s"] = self.ttl_s
        return d
