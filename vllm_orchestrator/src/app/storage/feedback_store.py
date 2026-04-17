"""feedback_store.py — 사용자 피드백 수집 + JSONL append store.

POST /tasks/{task_id}/feedback 로 들어온 신호를 append-only 파일에 기록.
나중에 학습 파이프라인에서:
  - rating >= 4 인 결과 → 프롬프트 개선 / LoRA fine-tuning 데이터
  - rating <= 2 인 결과 + tags → 프롬프트 약점 분석
  - tag 분포 → 어떤 측면이 반복해서 불만인지 (monitoring)

저장 포맷 (JSONL):
  {"ts": "...", "task_id": "...", "task_type": "...", "rating": 4, "tags": [...], "notes": "..."}

파일 회전 (크기 기반): feedback.jsonl.1, .2, ... (TODO: 필요시)
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class FeedbackEntry:
    ts: str = ""
    task_id: str = ""
    task_type: str = ""
    rating: int = 0                      # 1~5 (1=bad, 5=perfect)
    tags: list[str] = field(default_factory=list)  # 자유 태그 (e.g. "wrong_theme", "too_simple", "perfect")
    notes: str = ""
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    # 재현용 메타
    slots_snapshot_hash: Optional[str] = None   # 결과물 해시 (재현 대조용)
    critic_quality: Optional[float] = None
    validated: Optional[bool] = None
    variant_family: Optional[str] = None         # multi-variant 시 선택된 family

    def to_dict(self) -> dict:
        return asdict(self)


class FeedbackStore:
    """Thread-safe JSONL append store with rotation policy.

    Rotation rules (env):
      - FEEDBACK_MAX_BYTES (default 50MB) — 파일이 이 크기 넘으면 rotate
      - FEEDBACK_MAX_AGE_DAYS (default 90) — 이 기간 지난 record는 retention 제외
      - FEEDBACK_KEEP_ARCHIVES (default 12) — 보관할 rotated archive 개수
    """

    def __init__(self, path: Optional[str] = None):
        if path:
            self.path = Path(path)
        else:
            try:
                from .paths import feedback_log_path
                self.path = feedback_log_path()
            except Exception:
                self.path = Path(os.getenv("FEEDBACK_LOG_PATH", "./logs/feedback.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stats = {"records": 0, "by_rating": {str(i): 0 for i in range(1, 6)}}
        # Restore stats on startup (count existing lines)
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        self._stats["records"] += 1
                        try:
                            d = json.loads(line)
                            r = str(int(d.get("rating", 0)))
                            if r in self._stats["by_rating"]:
                                self._stats["by_rating"][r] += 1
                        except Exception:
                            pass
            except Exception:
                pass

    def _rotate_if_needed(self) -> None:
        """FEEDBACK_MAX_BYTES 넘으면 rotate + 오래된 archive 제거."""
        try:
            max_bytes = int(os.getenv("FEEDBACK_MAX_BYTES", str(50 * 1024 * 1024)))
            keep = int(os.getenv("FEEDBACK_KEEP_ARCHIVES", "12"))
        except ValueError:
            return
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except Exception:
            return
        if size < max_bytes:
            return
        # Rotate: feedback.jsonl → feedback.jsonl.<ts>
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.path.with_suffix(self.path.suffix + f".{ts}")
        try:
            self.path.replace(archive)
        except Exception:
            return
        # Prune old archives
        try:
            archives = sorted(self.path.parent.glob(self.path.name + ".*"))
            while len(archives) > keep:
                try:
                    archives[0].unlink()
                except Exception:
                    break
                archives = archives[1:]
        except Exception:
            pass

    def record(self, entry: FeedbackEntry) -> bool:
        """Append a feedback entry. Returns True on success."""
        if not entry.ts:
            entry.ts = datetime.now(timezone.utc).isoformat()
        # PII redaction on free-text fields
        try:
            from ..security.pii import redact_text
            entry.notes = redact_text(entry.notes or "")
            entry.tags = [redact_text(t) for t in (entry.tags or [])]
        except Exception:
            pass
        # Log rotation check
        try:
            self._rotate_if_needed()
        except Exception:
            pass
        line = json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                self._stats["records"] += 1
                r = str(max(1, min(5, int(entry.rating or 0))))
                if r in self._stats["by_rating"]:
                    self._stats["by_rating"][r] += 1
                # Prometheus
                try:
                    from ..observability.metrics import registry
                    from prometheus_client import Counter
                    # 지연 생성 (리스트가 모듈에 있으면 기존 사용)
                except Exception:
                    pass
                return True
            except Exception:
                return False

    def stats(self) -> dict:
        with self._lock:
            return {
                "path": str(self.path),
                "records": self._stats["records"],
                "by_rating": dict(self._stats["by_rating"]),
            }

    def recent(self, limit: int = 50) -> list[dict]:
        """최근 N개 엔트리 반환 (file tail 스캔)."""
        if not self.path.exists():
            return []
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    lines = f.readlines()
                tail = lines[-limit:]
                return [json.loads(l) for l in tail if l.strip()]
            except Exception:
                return []


# 프로세스 전역 싱글톤 (bootstrap에서 세팅 가능하도록)
_store: Optional[FeedbackStore] = None


def get_store() -> FeedbackStore:
    global _store
    if _store is None:
        _store = FeedbackStore()
    return _store
