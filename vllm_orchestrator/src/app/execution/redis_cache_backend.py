"""redis_cache_backend.py — 분산 캐시 백엔드 (Redis).

RequestCache와 동일한 인터페이스 (get/put/size/is_cacheable/...) 제공.
Redis 가용 시 bootstrap에서 선택적으로 사용.

장점:
  - orchestrator 재시작해도 캐시 유지
  - 여러 인스턴스 수평 확장 시 공유 캐시
  - TTL이 Redis 자체 기능 (SET ... EX ...)

비 가용/장애 시 graceful fallback:
  - 생성자에서 redis 라이브러리 없거나 ping 실패하면 self.connected=False
  - get/put 전부 no-op (miss 반환, False 반환)
  - 호출자는 backend 교체 없이 그냥 계속 작동
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    import redis as _redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _redis_lib = None
    _REDIS_AVAILABLE = False


@dataclass
class _Stats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    bypassed: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total > 0 else 0.0
        return {
            "hits": self.hits, "misses": self.misses,
            "stores": self.stores, "bypassed": self.bypassed,
            "errors": self.errors, "hit_rate": round(hit_rate, 3),
        }


def _normalize_input(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip().lower())


def _stable_context_key(ctx: Any) -> str:
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


class RedisRequestCache:
    """Redis-backed drop-in replacement for RequestCache.

    Env vars:
      REDIS_URL (default: redis://localhost:6379/0)
      REDIS_KEY_PREFIX (default: vllm_orch:cache:)
    """

    def __init__(
        self,
        url: Optional[str] = None,
        ttl_s: float = 3600.0,
        key_prefix: str = "vllm_orch:cache:",
        cacheable_task_types: Optional[set[str]] = None,
        connect_timeout_s: float = 2.0,
    ):
        self.url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.ttl_s = float(ttl_s)
        self.key_prefix = key_prefix
        self._cacheable = cacheable_task_types
        self.stats = _Stats()
        self.max_entries = 0  # not enforced (Redis handles via its own maxmemory policy)
        self.connected = False
        self.client = None
        self._init_error: Optional[str] = None

        if not _REDIS_AVAILABLE:
            self._init_error = "redis library not installed"
            return
        try:
            self.client = _redis_lib.from_url(
                self.url, socket_connect_timeout=connect_timeout_s,
                socket_timeout=connect_timeout_s, decode_responses=True,
            )
            self.client.ping()
            self.connected = True
        except Exception as e:
            self._init_error = f"Redis connect failed: {e}"
            self.client = None
            self.connected = False

    # ── interface parity with RequestCache ────────────────────────────────

    def make_key(self, task_type: str, user_input: str, context: Any) -> str:
        payload = f"{task_type}|{_normalize_input(user_input)}|{_stable_context_key(context)}"
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"{self.key_prefix}{h}"

    def is_cacheable(self, task_type: str) -> bool:
        if self._cacheable is None:
            return (
                task_type.startswith("minecraft.scene_graph")
                or task_type.startswith("minecraft.brainstorm")
                or task_type.startswith("minecraft.palette_only")
                or task_type.endswith(".planner")
                or task_type.endswith(".critic")
            )
        return task_type in self._cacheable

    def get(self, task_type: str, user_input: str, context: Any) -> Optional[dict]:
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
        if not self.connected:
            # Fallback: cache disabled, treat as miss
            self.stats.misses += 1
            observe_cache(task_type, "miss")
            return None
        key = self.make_key(task_type, user_input, context)
        try:
            raw = self.client.get(key)
        except Exception:
            self.stats.errors += 1
            self.stats.misses += 1
            observe_cache(task_type, "miss")
            return None
        if raw is None:
            self.stats.misses += 1
            observe_cache(task_type, "miss")
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self.stats.errors += 1
            self.stats.misses += 1
            observe_cache(task_type, "miss")
            return None
        self.stats.hits += 1
        observe_cache(task_type, "hit")
        update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
        return value

    def put(self, task_type: str, user_input: str, context: Any, value: dict) -> bool:
        try:
            from ..observability.metrics import observe_cache, update_cache_stats
        except Exception:
            def observe_cache(*a, **k):
                return None
            def update_cache_stats(*a, **k):
                return None

        if not self.is_cacheable(task_type):
            return False
        if not self.connected:
            return False
        jud = value.get("layered_judgment") or {}
        if not jud.get("auto_validated"):
            return False
        key = self.make_key(task_type, user_input, context)
        try:
            payload = json.dumps(value, ensure_ascii=False, default=str)
            self.client.set(key, payload, ex=int(self.ttl_s))
        except Exception:
            self.stats.errors += 1
            return False
        self.stats.stores += 1
        observe_cache(task_type, "store")
        update_cache_stats(self.size(), self.stats.to_dict()["hit_rate"])
        return True

    def invalidate(self, task_type: str, user_input: str, context: Any) -> bool:
        if not self.connected:
            return False
        key = self.make_key(task_type, user_input, context)
        try:
            return bool(self.client.delete(key))
        except Exception:
            return False

    def clear(self) -> None:
        """Delete all keys under this prefix. Use carefully."""
        if not self.connected:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = self.client.scan(cursor=cursor, match=f"{self.key_prefix}*", count=500)
                if keys:
                    self.client.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    def size(self) -> int:
        """Approximate entry count via SCAN."""
        if not self.connected:
            return 0
        try:
            count = 0
            cursor = 0
            while True:
                cursor, keys = self.client.scan(cursor=cursor, match=f"{self.key_prefix}*", count=500)
                count += len(keys)
                if cursor == 0:
                    break
                if count > 100000:  # 안전상한
                    break
            return count
        except Exception:
            return 0

    def stats_dict(self) -> dict:
        d = self.stats.to_dict()
        d["connected"] = self.connected
        d["backend"] = "redis"
        d["url"] = re.sub(r"://[^@/]*@", "://***@", self.url)  # 마스킹
        d["ttl_s"] = self.ttl_s
        if self._init_error:
            d["init_error"] = self._init_error
        # size 는 SCAN 비용 때문에 기본 생략; 호출자가 명시적으로 요청 시만
        return d


def build_cache(url: Optional[str] = None, ttl_s: float = 3600.0, max_entries: int = 1000):
    """Factory: Redis 가용 시 RedisRequestCache, 아니면 in-memory RequestCache."""
    from .request_cache import RequestCache
    if os.getenv("REQUEST_CACHE_BACKEND", "").lower() == "memory":
        return RequestCache(max_entries=max_entries, ttl_s=ttl_s)
    cache = RedisRequestCache(url=url, ttl_s=ttl_s)
    if cache.connected:
        return cache
    # Fallback
    return RequestCache(max_entries=max_entries, ttl_s=ttl_s)
