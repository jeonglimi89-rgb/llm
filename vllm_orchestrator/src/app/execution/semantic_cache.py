"""semantic_cache.py — 경량 유사도 기반 near-duplicate cache layer.

exact-match cache (request_cache.py) 앞단에 들어가는 layer. embedding 모델 없이
n-gram Jaccard 유사도로 "의미상 같은" 요청을 흡수.

예:
  "witch castle with towers" → cache에 저장
  "witch castle with multiple towers and walls" → 유사도 0.65 → cache hit

장점:
  - sentence_transformers 불필요 (CPU/메모리 가볍)
  - 결정론적, ~1ms per lookup (< 1000 entries)
  - 완전 신뢰하진 않음 — threshold 넘어야만 hit

단점:
  - 의미가 다른데 단어가 겹치면 false positive (e.g. "big witch castle" vs "small witch castle")
    → 중간 수준의 threshold(0.7+)로 완화
  - 진짜 의미 유사도는 embedding이 더 정확

전략:
  1. Exact-match 먼저 시도 (request_cache)
  2. Miss면 semantic cache에 조회 (similarity >= threshold → hit)
  3. Miss면 LLM 호출, 결과를 semantic_cache + exact_cache 양쪽에 저장
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


_TOKEN_RE = re.compile(r"[a-z0-9\u3131-\u3163\uac00-\ud7a3]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _ngrams(tokens: list[str], n: int = 2) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def text_similarity(a: str, b: str) -> float:
    """단어 Jaccard + bigram Jaccard 평균 (0~1). 결정론적."""
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return 0.0
    word_sim = _jaccard(set(ta), set(tb))
    bg_sim = _jaccard(_ngrams(ta, 2), _ngrams(tb, 2))
    return round((word_sim * 0.4 + bg_sim * 0.6), 4)


@dataclass
class _SemEntry:
    user_input: str
    task_type: str
    context_key: str
    value: dict
    created_at: float = field(default_factory=time.time)
    hits: int = 0

    def is_expired(self, ttl_s: float) -> bool:
        return (time.time() - self.created_at) > ttl_s


class SemanticCache:
    """Similarity-based cache. 큰 사이즈는 linear scan → cap을 둠."""

    def __init__(
        self,
        max_entries: int = 500,
        ttl_s: float = 3600.0,
        similarity_threshold: float = 0.55,
        cacheable_task_types: Optional[set[str]] = None,
    ):
        self.max_entries = int(max_entries)
        self.ttl_s = float(ttl_s)
        self.threshold = float(similarity_threshold)
        self._cacheable: Optional[set[str]] = cacheable_task_types
        # Keyed by task_type + context → list of entries
        self._buckets: dict[str, list[_SemEntry]] = {}
        self._lock = threading.Lock()
        self.stats = {"lookups": 0, "hits": 0, "misses": 0, "stores": 0, "evictions": 0}

    def is_cacheable(self, task_type: str) -> bool:
        if self._cacheable is None:
            return (
                task_type.endswith("scene_graph")
                or task_type.endswith("brainstorm")
                or task_type.endswith("palette_only")
            )
        return task_type in self._cacheable

    def _bucket_key(self, task_type: str, context_key: str) -> str:
        return f"{task_type}::{context_key}"

    def lookup(self, task_type: str, user_input: str, context_key: str) -> tuple[Optional[dict], float, Optional[str]]:
        """가장 유사한 엔트리 찾기. Returns (value_or_None, best_similarity, matched_input)."""
        if not self.is_cacheable(task_type):
            return None, 0.0, None
        bkey = self._bucket_key(task_type, context_key)
        with self._lock:
            self.stats["lookups"] += 1
            bucket = self._buckets.get(bkey) or []
            best_sim = 0.0
            best_entry: Optional[_SemEntry] = None
            # TTL 만료 제거 + best 찾기
            fresh: list[_SemEntry] = []
            for e in bucket:
                if e.is_expired(self.ttl_s):
                    continue
                fresh.append(e)
                sim = text_similarity(user_input, e.user_input)
                if sim > best_sim:
                    best_sim = sim
                    best_entry = e
            self._buckets[bkey] = fresh
            if best_entry and best_sim >= self.threshold:
                best_entry.hits += 1
                self.stats["hits"] += 1
                return best_entry.value, best_sim, best_entry.user_input
            self.stats["misses"] += 1
            return None, best_sim, None

    def store(self, task_type: str, user_input: str, context_key: str, value: dict) -> bool:
        if not self.is_cacheable(task_type):
            return False
        # auto_validated=True만 저장
        jud = value.get("layered_judgment") or {}
        if not jud.get("auto_validated"):
            return False
        bkey = self._bucket_key(task_type, context_key)
        entry = _SemEntry(user_input=user_input, task_type=task_type,
                          context_key=context_key, value=value)
        with self._lock:
            bucket = self._buckets.setdefault(bkey, [])
            bucket.append(entry)
            self.stats["stores"] += 1
            # Total size enforcement (전체 bucket 합)
            total = sum(len(b) for b in self._buckets.values())
            while total > self.max_entries:
                # 모든 bucket에서 가장 오래된 항목 하나 evict
                oldest_bucket = None
                oldest_idx = -1
                oldest_ts = time.time() + 1
                for bk, b in self._buckets.items():
                    if not b:
                        continue
                    # 첫 번째가 가장 오래됨 (append order)
                    if b[0].created_at < oldest_ts:
                        oldest_ts = b[0].created_at
                        oldest_bucket = bk
                        oldest_idx = 0
                if oldest_bucket is None:
                    break
                self._buckets[oldest_bucket].pop(oldest_idx)
                self.stats["evictions"] += 1
                total -= 1
            return True

    def size(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._buckets.values())

    def stats_dict(self) -> dict:
        d = dict(self.stats)
        d["size"] = self.size()
        d["max_entries"] = self.max_entries
        d["ttl_s"] = self.ttl_s
        d["threshold"] = self.threshold
        total = d["hits"] + d["misses"]
        d["hit_rate"] = round(d["hits"] / total, 3) if total else 0.0
        return d


# ── Factory/singleton ──────────────────────────────────────────────────────

_instance: Optional[SemanticCache] = None


def get_semantic_cache() -> Optional[SemanticCache]:
    """환경변수 SEMANTIC_CACHE_DISABLED=1 이면 None 반환."""
    global _instance
    if os.getenv("SEMANTIC_CACHE_DISABLED", "").lower() in ("1", "true", "yes"):
        return None
    if _instance is None:
        _instance = SemanticCache(
            max_entries=int(os.getenv("SEMANTIC_CACHE_MAX", "500")),
            ttl_s=float(os.getenv("SEMANTIC_CACHE_TTL_S", "3600")),
            similarity_threshold=float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.55")),
        )
    return _instance
