"""auth_middleware.py — API key + token-bucket rate limit.

Design:
  - Token bucket per API key (in-memory; TODO: distribute via Redis).
  - Public paths (/health/*, /metrics) are exempt.
  - Env-controlled: API_KEY_REQUIRED=1 enables key check; empty/unset → open.
  - Multiple keys via API_KEYS env (comma-separated).

Headers:
  - X-API-Key: <key>  (preferred)
  - Authorization: Bearer <key>

Rate limit config (env):
  - RATE_LIMIT_RPS (default 2.0) — tokens per second per key
  - RATE_LIMIT_BURST (default 20) — bucket capacity

Response on reject:
  - 401 if missing/invalid key
  - 429 if rate-limited (Retry-After header set)
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


# ── public path prefixes exempt from auth+rate limit ───────────────────────
_PUBLIC_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


@dataclass
class _Bucket:
    """Classic token bucket. `tokens` decays at `rate` per second, caps at `burst`."""
    tokens: float
    last_refill: float

    def consume(self, rate: float, burst: float, cost: float = 1.0) -> tuple[bool, float]:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(burst, self.tokens + elapsed * rate)
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        # deficit 토큰을 모을 시간 계산
        deficit = cost - self.tokens
        retry_after_s = deficit / rate if rate > 0 else 60.0
        return False, retry_after_s


# ── Distributed token bucket (Redis, for multi-worker deploys) ─────────────
# Uses INCR + EXPIRE to implement a simplified fixed-window-with-burst-smoothing.
# 정확한 leaky bucket은 Lua script 필요하지만, 이 정도면 대부분 워크로드에 충분.

class _RedisBucket:
    """Redis-backed rate limiter. 여러 uvicorn worker/instance 간 공유됨.
    실패 시 (Redis down) → open (허용). 안전성보다 가용성 우선."""

    def __init__(self, client, rate: float, burst: float, key_prefix: str = "vllm_orch:rl:"):
        self.c = client
        self.rate = float(rate)
        self.burst = float(burst)
        self.key_prefix = key_prefix
        # 1초 슬라이딩 윈도우에 대해 rate만큼 허용, burst는 누적 한도
        self._window_s = 1

    def consume(self, bucket_key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_s)."""
        if self.c is None:
            return True, 0.0
        k = f"{self.key_prefix}{bucket_key}"
        try:
            # counter + window expiry
            count = self.c.incr(k)
            if count == 1:
                self.c.expire(k, self._window_s)
            # 윈도우 안에 rate 이상 오면 reject
            if count > self.rate:
                # 누적 burst 예산까지는 허용
                # 간단화: 윈도우 내 rate*1초 + burst-rate 만큼 더 허용
                if count > self.rate + (self.burst - self.rate):
                    return False, 1.0
            return True, 0.0
        except Exception:
            return True, 0.0


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # API keys whitelist
        self.api_key_required = os.getenv("API_KEY_REQUIRED", "").lower() in ("1", "true", "yes")
        keys_raw = os.getenv("API_KEYS", "")
        self.valid_keys: set[str] = {k.strip() for k in keys_raw.split(",") if k.strip()}
        if self.api_key_required and not self.valid_keys:
            # 안전장치: 요구하는데 등록된 키 없으면 모두 차단 상태가 되니 경고 출력 + 단일 dev key 허용
            import logging
            logging.getLogger("vllm_orch.auth").warning(
                "API_KEY_REQUIRED=1 but API_KEYS is empty; using dev-only key 'dev'"
            )
            self.valid_keys = {"dev"}
        # Rate limit config
        try:
            self.rate = float(os.getenv("RATE_LIMIT_RPS", "2.0"))
        except ValueError:
            self.rate = 2.0
        try:
            self.burst = float(os.getenv("RATE_LIMIT_BURST", "20"))
        except ValueError:
            self.burst = 20.0
        self.rate_limit_enabled = self.rate > 0 and self.burst > 0
        # Per-key bucket (in-memory fallback)
        self._buckets: dict[str, _Bucket] = {}
        # Redis-backed distributed bucket (optional)
        self._redis_bucket: Optional[_RedisBucket] = None
        backend = os.getenv("RATE_LIMIT_BACKEND", "auto").lower()
        if backend in ("auto", "redis"):
            try:
                import redis as _redis_lib
                client = _redis_lib.from_url(
                    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                    socket_connect_timeout=1.5, socket_timeout=1.5,
                    decode_responses=True,
                )
                client.ping()
                self._redis_bucket = _RedisBucket(client, self.rate, self.burst)
                import logging
                logging.getLogger("vllm_orch.auth").info("RateLimit backend=redis (distributed)")
            except Exception as _e:
                if backend == "redis":
                    import logging
                    logging.getLogger("vllm_orch.auth").warning(
                        f"RateLimit backend=redis requested but failed ({_e}), fallback to memory"
                    )
                # fallback to memory silently on 'auto'

    def _extract_key(self, request: Request) -> Optional[str]:
        hk = request.headers.get("x-api-key")
        if hk:
            return hk.strip()
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _is_public(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in _PUBLIC_PREFIXES)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_public(path):
            return await call_next(request)

        # API key check (store-backed with lifecycle + env fallback)
        key = self._extract_key(request)
        if self.api_key_required:
            if not key:
                self._observe_reject("missing_api_key")
                return _json_error(401, "missing API key (X-API-Key header)")
            try:
                from ..security.api_keys import check_api_key
                rec = check_api_key(key)
            except Exception:
                rec = None
                # legacy env-only fallback
                if key in self.valid_keys:
                    rec = True  # truthy
            if not rec:
                self._observe_reject("bad_api_key")
                return _json_error(401, "invalid API key")

        # Rate limit (key 기준; key 없으면 client IP)
        if self.rate_limit_enabled:
            bucket_key = key or (request.client.host if request.client else "anonymous")
            if self._redis_bucket is not None:
                allowed, retry_after = self._redis_bucket.consume(bucket_key)
            else:
                bucket = self._buckets.get(bucket_key)
                if bucket is None:
                    bucket = _Bucket(tokens=self.burst, last_refill=time.time())
                    self._buckets[bucket_key] = bucket
                allowed, retry_after = bucket.consume(self.rate, self.burst)
            if not allowed:
                self._observe_reject("rate_limited")
                return _json_error(
                    429, f"rate limit exceeded (rps={self.rate} burst={self.burst})",
                    headers={"Retry-After": f"{max(1, int(retry_after)):d}"},
                )

        return await call_next(request)

    def _observe_reject(self, reason: str) -> None:
        try:
            from ..observability.metrics import auth_rejections
            auth_rejections.labels(reason=reason).inc()
        except Exception:
            pass


def _json_error(status_code: int, message: str, headers: Optional[dict] = None) -> Response:
    import json as _json
    body = _json.dumps({"error": message, "status_code": status_code}).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return Response(content=body, status_code=status_code, headers=h)
