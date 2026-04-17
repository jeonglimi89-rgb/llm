"""feedback.py — POST /tasks/{task_id}/feedback + GET /feedback/stats

학습/개선 신호 수집 엔드포인트. 대규모 운영에서 사용자 rating +
tags 를 쌓아서:
  - 나쁜 결과 (<=2) → 프롬프트 개선 우선순위
  - 좋은 결과 (>=4) → LoRA/DPO 학습 데이터 소스

Body schema:
  {
    "rating": 1-5,
    "tags": ["wrong_theme", "too_simple", ...],   // 자유 텍스트
    "notes": "...",                                // 선택
    "user_id": "...",                              // 선택
    "session_id": "...",                           // 선택
    "task_type": "minecraft.scene_graph",          // 클라이언트가 알고 있는 경우
    "slots_snapshot_hash": "...",                  // 결과 재현 대조
    "critic_quality": 0.85,                        // 이전 응답에서 전달받은 값 그대로
    "validated": true,
    "variant_family": "safe_baseline"
  }
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from ...storage.feedback_store import FeedbackEntry, get_store


router = APIRouter(tags=["feedback"])


@router.post("/tasks/{task_id}/feedback")
def submit_feedback(task_id: str, body: dict = Body(...)):
    rating = body.get("rating")
    if rating is None:
        raise HTTPException(400, "rating is required (1-5)")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise HTTPException(400, "rating must be an integer")
    if not (1 <= rating <= 5):
        raise HTTPException(400, "rating must be 1-5")

    tags = body.get("tags") or []
    if not isinstance(tags, list):
        raise HTTPException(400, "tags must be a list of strings")
    tags = [str(t)[:60] for t in tags][:20]

    entry = FeedbackEntry(
        task_id=task_id,
        task_type=str(body.get("task_type", ""))[:80],
        rating=rating,
        tags=tags,
        notes=str(body.get("notes", ""))[:2000],
        user_id=body.get("user_id") or None,
        session_id=body.get("session_id") or None,
        slots_snapshot_hash=body.get("slots_snapshot_hash") or None,
        critic_quality=body.get("critic_quality"),
        validated=body.get("validated"),
        variant_family=body.get("variant_family") or None,
    )
    store = get_store()
    ok = store.record(entry)
    if not ok:
        raise HTTPException(500, "failed to persist feedback")
    return {"ok": True, "stats": store.stats()}


@router.get("/feedback/stats")
def feedback_stats():
    return get_store().stats()


@router.get("/feedback/recent")
def feedback_recent(limit: int = Query(default=50, ge=1, le=500)):
    return {"entries": get_store().recent(limit=limit)}
