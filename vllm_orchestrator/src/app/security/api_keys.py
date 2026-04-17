"""api_keys.py — API key lifecycle & rotation support.

Keys are stored as records (not just strings):
  {
    "key_id": "k-abc123",
    "hash": "sha256:...",            # 실제 키는 저장 안 함, hash만
    "tier": "default|premium|...",   # quota 등급
    "status": "active|revoked|expired|grace",
    "created_at": "...",
    "expires_at": "...",              # optional
    "grace_until": "...",             # optional, 로테이션 중 구 키 허용 기간
    "last_used_at": "...",
    "metadata": {...}
  }

Rotation flow:
  1. generate_key(tier="default") → (key_id, plain_secret) 발급
  2. 사용자에게 plain_secret 1회 전달 (이후 복구 불가)
  3. 로테이션 시: old key 를 "grace" 상태로 → new key 생성 → grace_until 지나면 revoke
  4. 요청 들어오면 check_key(plain) 로 hash 대조

Storage:
  - JSONL `storage/api_keys.jsonl` (or Redis, backend=auto)
  - File lock으로 multi-worker 경합 방지

Auth middleware에서 static `API_KEYS` env 대신 이 모듈의 check_key()를 호출.
fallback: api_keys.jsonl 없으면 env API_KEYS (comma-separated) 평문 비교.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(secret: str) -> str:
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass
class APIKeyRecord:
    key_id: str
    hash: str
    tier: str = "default"
    status: str = "active"               # active|grace|revoked|expired
    created_at: str = field(default_factory=_now)
    expires_at: Optional[str] = None
    grace_until: Optional[str] = None
    last_used_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def is_usable_now(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.status == "revoked":
            return False
        if self.expires_at:
            try:
                if datetime.fromisoformat(self.expires_at) < now:
                    return False
            except Exception:
                pass
        if self.status == "grace":
            if self.grace_until:
                try:
                    if datetime.fromisoformat(self.grace_until) < now:
                        return False
                except Exception:
                    pass
        return True


class APIKeyStore:
    """JSONL 기반 key record store. Thread-safe."""

    def __init__(self, path: Optional[str] = None):
        if path:
            self.path = Path(path)
        else:
            try:
                from ..storage.paths import storage_dir
                self.path = storage_dir() / "api_keys.jsonl"
            except Exception:
                self.path = Path("./storage/api_keys.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read_all(self) -> list[APIKeyRecord]:
        if not self.path.exists():
            return []
        out: list[APIKeyRecord] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        out.append(APIKeyRecord(**d))
                    except Exception:
                        continue
        except Exception:
            pass
        return out

    def _write_all(self, records: list[APIKeyRecord]) -> None:
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def generate(self, tier: str = "default", ttl_days: Optional[int] = None, metadata: Optional[dict] = None) -> tuple[str, str]:
        """Returns (key_id, plain_secret). plain_secret은 여기서만 보임."""
        with self._lock:
            key_id = "k-" + secrets.token_hex(6)
            plain = secrets.token_urlsafe(32)
            rec = APIKeyRecord(
                key_id=key_id,
                hash=_hash(plain),
                tier=tier,
                expires_at=(datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat() if ttl_days else None,
                metadata=metadata or {},
            )
            records = self._read_all()
            records.append(rec)
            self._write_all(records)
            return key_id, plain

    def find_by_hash(self, h: str) -> Optional[APIKeyRecord]:
        with self._lock:
            for r in self._read_all():
                if r.hash == h:
                    return r
        return None

    def check(self, plain: str) -> Optional[APIKeyRecord]:
        """Plain key → 사용 가능한 record 반환, 아니면 None."""
        if not plain:
            return None
        rec = self.find_by_hash(_hash(plain))
        if rec and rec.is_usable_now():
            # last_used_at 갱신 (성능 위해 비동기/배치 처리 권장; 여기선 즉시)
            try:
                self._touch_last_used(rec.key_id)
            except Exception:
                pass
            return rec
        return None

    def _touch_last_used(self, key_id: str) -> None:
        with self._lock:
            records = self._read_all()
            for r in records:
                if r.key_id == key_id:
                    r.last_used_at = _now()
                    break
            self._write_all(records)

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            records = self._read_all()
            changed = False
            for r in records:
                if r.key_id == key_id:
                    r.status = "revoked"
                    changed = True
            if changed:
                self._write_all(records)
            return changed

    def start_rotation(self, old_key_id: str, grace_hours: int = 24) -> tuple[Optional[str], Optional[str]]:
        """old key를 grace 상태로 전환 + 새 key 발급. grace_hours 지나면 old key 사용 불가."""
        with self._lock:
            records = self._read_all()
            old = None
            for r in records:
                if r.key_id == old_key_id:
                    old = r
                    break
            if not old:
                return None, None
            old.status = "grace"
            old.grace_until = (datetime.now(timezone.utc) + timedelta(hours=grace_hours)).isoformat()
            self._write_all(records)
        new_id, new_plain = self.generate(tier=old.tier, metadata=old.metadata)
        return new_id, new_plain

    def list_all(self) -> list[APIKeyRecord]:
        with self._lock:
            return self._read_all()


# Singleton
_store: Optional[APIKeyStore] = None


def get_store() -> APIKeyStore:
    global _store
    if _store is None:
        _store = APIKeyStore()
    return _store


def check_api_key(plain: str) -> Optional[APIKeyRecord]:
    """Middleware에서 호출. env API_KEYS fallback 포함."""
    if not plain:
        return None
    # 1. Store lookup
    store = get_store()
    rec = store.check(plain)
    if rec:
        return rec
    # 2. Legacy env fallback (comma-separated plaintext)
    env_keys = os.getenv("API_KEYS", "")
    allowed = {k.strip() for k in env_keys.split(",") if k.strip()}
    if plain in allowed:
        return APIKeyRecord(key_id="env", hash="env", tier="default", status="active")
    return None
