"""
datasets/dataset_builder.py - 로그 → 학습 데이터 변환

온라인: 모든 요청/응답/수정 기록
오프라인: 로그 → SFT/LoRA 학습셋 정제

5가지 데이터셋:
  intent_pairs:     사용자 자연어 → 공통 intent JSON
  plan_pairs:       intent JSON → domain plan JSON
  critique_pairs:   원래 요구 + 생성 결과 → 결함 목록 + 수정 제안
  repair_pairs:     실패한 plan → 수정된 정답 plan
  preference_pairs: 사용자 선택/수정 이력 → 선호 프로파일
"""

from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


class DatasetBuilder:
    """파이프라인 로그 → 학습 데이터 변환기"""

    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            base_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "datasets")
        self.base_dir = base_dir
        self._dirs = {
            "raw_logs": os.path.join(base_dir, "raw_logs"),
            "intent_pairs": os.path.join(base_dir, "curated", "intent_pairs"),
            "plan_pairs": os.path.join(base_dir, "curated", "plan_pairs"),
            "critique_pairs": os.path.join(base_dir, "curated", "critique_pairs"),
            "repair_pairs": os.path.join(base_dir, "curated", "repair_pairs"),
            "preference_pairs": os.path.join(base_dir, "curated", "preference_pairs"),
        }
        for d in self._dirs.values():
            os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------
    # 온라인: 원시 로그 기록
    # ------------------------------------------------------------------

    def log_pipeline_run(self, run: dict) -> str:
        """파이프라인 실행 1건 원시 로그"""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            **run,
        }
        path = os.path.join(self._dirs["raw_logs"], "pipeline_runs.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def log_user_edit(self, request_id: str, original: dict, edited: dict, accepted: bool) -> str:
        """사용자 수정 로그"""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "original": original,
            "edited": edited,
            "accepted": accepted,
        }
        path = os.path.join(self._dirs["raw_logs"], "user_edits.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    # ------------------------------------------------------------------
    # 오프라인: 학습 데이터 정제
    # ------------------------------------------------------------------

    def build_all(self) -> dict[str, int]:
        """원시 로그 → 5가지 학습 데이터 생성"""
        runs = self._load_jsonl(os.path.join(self._dirs["raw_logs"], "pipeline_runs.jsonl"))
        edits = self._load_jsonl(os.path.join(self._dirs["raw_logs"], "user_edits.jsonl"))

        counts = {
            "intent_pairs": self._build_intent_pairs(runs),
            "plan_pairs": self._build_plan_pairs(runs),
            "critique_pairs": self._build_critique_pairs(runs),
            "repair_pairs": self._build_repair_pairs(runs, edits),
            "preference_pairs": self._build_preference_pairs(edits),
        }
        return counts

    def _build_intent_pairs(self, runs: list[dict]) -> int:
        """사용자 자연어 → intent JSON"""
        pairs = []
        for run in runs:
            user_req = run.get("user_request", "")
            plan = run.get("plan", {})
            if user_req and plan:
                pairs.append({
                    "instruction": user_req,
                    "output": plan,
                    "program": run.get("program", ""),
                })
        return self._save_pairs("intent_pairs", pairs)

    def _build_plan_pairs(self, runs: list[dict]) -> int:
        """intent → domain plan"""
        pairs = []
        for run in runs:
            plan = run.get("plan", {})
            execution = run.get("execution", {})
            if plan and execution:
                pairs.append({
                    "instruction": json.dumps(plan, ensure_ascii=False),
                    "output": execution,
                    "program": run.get("program", ""),
                })
        return self._save_pairs("plan_pairs", pairs)

    def _build_critique_pairs(self, runs: list[dict]) -> int:
        """요구 + 결과 → 비평"""
        pairs = []
        for run in runs:
            execution = run.get("execution", {})
            review = run.get("review", {})
            if execution and review:
                pairs.append({
                    "instruction": json.dumps({
                        "request": run.get("user_request", ""),
                        "result": execution,
                    }, ensure_ascii=False),
                    "output": review,
                    "program": run.get("program", ""),
                })
        return self._save_pairs("critique_pairs", pairs)

    def _build_repair_pairs(self, runs: list[dict], edits: list[dict]) -> int:
        """실패 plan → 수정된 plan"""
        pairs = []
        edit_map = {}
        for e in edits:
            edit_map[e.get("request_id", "")] = e

        for run in runs:
            rid = run.get("request_id", "")
            validation = run.get("validation", {})
            if not validation.get("critic_pass", True) and rid in edit_map:
                edit = edit_map[rid]
                if edit.get("accepted"):
                    pairs.append({
                        "instruction": json.dumps(run.get("execution", {}), ensure_ascii=False),
                        "output": edit.get("edited", {}),
                        "program": run.get("program", ""),
                    })
        return self._save_pairs("repair_pairs", pairs)

    def _build_preference_pairs(self, edits: list[dict]) -> int:
        """사용자 선택/수정 → 선호"""
        pairs = []
        for e in edits:
            if e.get("accepted"):
                pairs.append({
                    "original": e.get("original", {}),
                    "preferred": e.get("edited", {}),
                    "timestamp": e.get("timestamp", ""),
                })
        return self._save_pairs("preference_pairs", pairs)

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------

    def _save_pairs(self, dataset_name: str, pairs: list[dict]) -> int:
        if not pairs:
            return 0
        path = os.path.join(self._dirs[dataset_name], f"{dataset_name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        return len(pairs)

    def _load_jsonl(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        items = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items
