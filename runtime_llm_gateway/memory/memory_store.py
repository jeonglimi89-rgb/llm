"""
memory/memory_store.py - 사용자/프로젝트/세션 메모리

3계층:
  session_memory:      현재 세션 수정 이력 (휘발)
  project_memory:      프로젝트별 승인/거부된 결과 (영속)
  preference_memory:   사용자 선호 패턴 (영속, 점진적 업데이트)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional


class MemoryStore:
    """JSONL 기반 3계층 메모리"""

    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            base_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "memory")
        self.base_dir = base_dir
        os.makedirs(os.path.join(base_dir, "sessions"), exist_ok=True)
        os.makedirs(os.path.join(base_dir, "projects"), exist_ok=True)
        os.makedirs(os.path.join(base_dir, "preferences"), exist_ok=True)

    # ------------------------------------------------------------------
    # Session Memory (현재 세션 수정 이력)
    # ------------------------------------------------------------------

    def add_session_entry(self, session_id: str, entry: dict) -> None:
        entry["timestamp"] = datetime.now(UTC).isoformat()
        path = os.path.join(self.base_dir, "sessions", f"{session_id}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_session_history(self, session_id: str, last_n: int = 10) -> list[dict]:
        path = os.path.join(self.base_dir, "sessions", f"{session_id}.jsonl")
        if not os.path.exists(path):
            return []
        items = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items[-last_n:]

    # ------------------------------------------------------------------
    # Project Memory (승인/거부 결과)
    # ------------------------------------------------------------------

    def save_approved(self, project_id: str, result: dict) -> None:
        result["_status"] = "approved"
        result["_timestamp"] = datetime.now(UTC).isoformat()
        self._append_project(project_id, result)

    def save_rejected(self, project_id: str, result: dict, reason: str) -> None:
        result["_status"] = "rejected"
        result["_reason"] = reason
        result["_timestamp"] = datetime.now(UTC).isoformat()
        self._append_project(project_id, result)

    def get_project_history(self, project_id: str, status: Optional[str] = None) -> list[dict]:
        items = self._load_project(project_id)
        if status:
            items = [i for i in items if i.get("_status") == status]
        return items

    # ------------------------------------------------------------------
    # Preference Memory (사용자 선호)
    # ------------------------------------------------------------------

    def update_preference(self, user_id: str, key: str, value: Any) -> None:
        prefs = self.get_preferences(user_id)
        prefs[key] = value
        prefs["_updated_at"] = datetime.now(UTC).isoformat()
        path = os.path.join(self.base_dir, "preferences", f"{user_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)

    def get_preferences(self, user_id: str) -> dict:
        path = os.path.join(self.base_dir, "preferences", f"{user_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def record_choice(self, user_id: str, program: str, chosen: dict, alternatives: list[dict]) -> None:
        """사용자가 대안 중 하나를 선택했을 때 선호 기록"""
        entry = {
            "program": program,
            "chosen": chosen,
            "alternatives_count": len(alternatives),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        path = os.path.join(self.base_dir, "preferences", f"{user_id}_choices.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _append_project(self, project_id: str, entry: dict) -> None:
        path = os.path.join(self.base_dir, "projects", f"{project_id}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _load_project(self, project_id: str) -> list[dict]:
        path = os.path.join(self.base_dir, "projects", f"{project_id}.jsonl")
        if not os.path.exists(path):
            return []
        items = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items
