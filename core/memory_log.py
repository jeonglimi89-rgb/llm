"""
core/memory_log.py — 세션 기록 파이프라인

모든 상호작용을 저장하여 나중에 학습 데이터로 변환 가능하게 한다.
v1: JSONL 파일 기반 (외부 의존성 없음)
v2: SQLite 인덱싱 추가 가능

저장 형식은 v1에서 고정. 필드 추가는 가능하되 제거/이름 변경 금지.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    SessionRecord,
    ParsedIntent,
    Variant,
    Critique,
    DeltaPatch,
)


class MemoryLogPipeline:

    def __init__(self, data_dir: str, project_type: str):
        self.data_dir = data_dir
        self.project_type = project_type
        self._sessions_dir = os.path.join(data_dir, "sessions", project_type)
        self._training_dir = os.path.join(data_dir, "training_ready")
        os.makedirs(self._sessions_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 기록
    # ------------------------------------------------------------------

    def record_session(self, record: SessionRecord) -> str:
        """세션을 JSONL 파일에 원자적으로 저장. 저장된 파일 경로 반환."""
        now = datetime.now(timezone.utc)
        filename = f"{now.strftime('%Y-%m')}.jsonl"
        filepath = os.path.join(self._sessions_dir, filename)

        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return filepath

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def load_sessions(
        self,
        year_month: Optional[str] = None,
        only_accepted: bool = False,
        limit: Optional[int] = None,
    ) -> list[SessionRecord]:
        """저장된 세션 로드. year_month 예: '2026-03'"""
        sessions = []

        if year_month:
            files = [os.path.join(self._sessions_dir, f"{year_month}.jsonl")]
        else:
            files = sorted(Path(self._sessions_dir).glob("*.jsonl"))

        for filepath in files:
            filepath = str(filepath)
            if not os.path.exists(filepath):
                continue
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = SessionRecord.from_dict(json.loads(line))
                    if only_accepted and not record.final_accepted:
                        continue
                    sessions.append(record)
                    if limit and len(sessions) >= limit:
                        return sessions

        return sessions

    def count_sessions(self) -> int:
        count = 0
        for filepath in Path(self._sessions_dir).glob("*.jsonl"):
            with open(filepath, encoding="utf-8") as f:
                count += sum(1 for line in f if line.strip())
        return count

    # ------------------------------------------------------------------
    # 학습 데이터 변환
    # ------------------------------------------------------------------

    def export_training_pairs(
        self,
        only_accepted: bool = True,
    ) -> dict[str, list[dict]]:
        """
        저장된 세션을 학습 데이터 형식으로 변환.

        반환 구조:
        {
            "intent_pairs": [...],     # Intent Parser 학습용
            "ranking_pairs": [...],    # Ranker 학습용
            "patch_pairs": [...]       # Delta Patch 학습용
        }
        """
        sessions = self.load_sessions(only_accepted=only_accepted)

        training_data: dict[str, list[dict]] = {
            "intent_pairs": [],
            "ranking_pairs": [],
            "patch_pairs": [],
        }

        for s in sessions:
            # 1. Intent 학습쌍: (user_request + context) → parsed_intent
            if s.parsed_intent:
                training_data["intent_pairs"].append({
                    "input": s.user_request,
                    "context": {
                        "project_type": s.project_type,
                        "project_id": s.project_id,
                    },
                    "output": s.parsed_intent.to_dict(),
                })

            # 2. Ranking 학습쌍: (variants + critiques) → selected_variant_id
            if s.user_selected_variant_id and s.variants_generated:
                training_data["ranking_pairs"].append({
                    "variants": [v.to_dict() for v in s.variants_generated],
                    "critiques": [c.to_dict() for c in s.critiques],
                    "selected": s.user_selected_variant_id,
                })

            # 3. Patch 학습쌍: (edit_request + current_params) → operations
            for edit in s.user_edits:
                training_data["patch_pairs"].append({
                    "request": edit.description,
                    "base_params": s.final_params,
                    "operations": [op.to_dict() for op in edit.operations],
                })

        return training_data

    def save_training_pairs(self) -> dict[str, str]:
        """학습 데이터를 파일로 저장. 저장된 파일 경로 dict 반환."""
        data = self.export_training_pairs()
        os.makedirs(self._training_dir, exist_ok=True)

        saved_paths = {}
        for key, pairs in data.items():
            if not pairs:
                continue
            subdir = os.path.join(self._training_dir, key)
            os.makedirs(subdir, exist_ok=True)
            filepath = os.path.join(subdir, f"{self.project_type}.jsonl")
            with open(filepath, "w", encoding="utf-8") as f:
                for pair in pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            saved_paths[key] = filepath

        return saved_paths

    # ------------------------------------------------------------------
    # 통계
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """기본 통계 반환."""
        sessions = self.load_sessions()
        if not sessions:
            return {"total": 0}

        accepted = [s for s in sessions if s.final_accepted]
        edit_counts = [len(s.user_edits) for s in sessions]

        return {
            "total": len(sessions),
            "accepted": len(accepted),
            "acceptance_rate": len(accepted) / len(sessions) if sessions else 0,
            "avg_edits_per_session": sum(edit_counts) / len(sessions) if sessions else 0,
            "total_edits": sum(edit_counts),
            "sessions_with_selection": sum(
                1 for s in sessions if s.user_selected_variant_id
            ),
        }
