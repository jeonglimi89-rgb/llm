"""
core/test_core_api_e2e.py — core/ API 엔드포인트 E2E 테스트

FastAPI TestClient를 사용해 실제 HTTP 요청/응답을 검증.
서버를 별도로 실행할 필요 없음.

실행: python -X utf8 -m core.test_core_api_e2e
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

# backend/app을 import 가능하게
_BASE = Path(__file__).resolve().parent.parent
_BACKEND = str(_BASE / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
_ROOT = str(_BASE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _make_client():
    """FastAPI TestClient 생성. httpx 없으면 None 반환."""
    try:
        from fastapi.testclient import TestClient
        from app.core_api import core_router
        from fastapi import FastAPI
        test_app = FastAPI()
        test_app.include_router(core_router)
        return TestClient(test_app)
    except ImportError as e:
        print(f"  [SKIP] TestClient unavailable: {e}")
        return None


def test_status(client) -> bool:
    print("  1. GET /api/core/status")
    r = client.get("/api/core/status")
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    assert "projects" in data
    assert "product_design" in data["projects"]
    assert "drawing_ai" in data["projects"]
    print(f"    OK: projects={data['projects']}, modules={len(data.get('modules', []))}")
    return True


def test_intent_parse(client) -> bool:
    print("  2. POST /api/core/intent/parse")
    r = client.post("/api/core/intent/parse", json={
        "text": "전체 폭을 360mm로 바꿔줘",
        "project_type": "product_design",
    })
    assert r.status_code == 200, f"status={r.status_code}, body={r.text[:200]}"
    data = r.json()
    assert data["intent_type"] == "modify_existing"
    assert data["target_object"] == "dimension"
    assert "dimensions.overall_width_mm" in data["constraints"]
    print(f"    OK: intent={data['intent_type']}, target={data['target_object']}, constraints={data['constraints']}")
    return True


def test_variants_generate(client) -> bool:
    print("  3. POST /api/core/variants/generate")
    r = client.post("/api/core/variants/generate", json={
        "text": "미니멀한 사무용 의자 만들어줘",
        "project_type": "product_design",
        "n_variants": 3,
    })
    assert r.status_code == 200, f"status={r.status_code}, body={r.text[:200]}"
    data = r.json()
    assert "intent" in data
    assert "variants" in data
    assert "critiques" in data
    assert len(data["variants"]) >= 1
    assert len(data["critiques"]) >= 1
    print(f"    OK: variants={len(data['variants'])}, critiques={len(data['critiques'])}")
    return True


def test_patch_interpret(client) -> bool:
    print("  4. POST /api/core/patch/interpret")
    r = client.post("/api/core/patch/interpret", json={
        "edit_request": "전체 폭을 500mm로 바꿔줘",
        "project_type": "product_design",
        "current_params": {"dimensions": {"overall_width_mm": 420, "overall_depth_mm": 180, "overall_height_mm": 260}},
    })
    assert r.status_code == 200, f"status={r.status_code}, body={r.text[:200]}"
    data = r.json()
    assert "patch" in data
    assert "new_params" in data
    assert len(data["patch"]["operations"]) >= 1
    assert data["new_params"]["dimensions"]["overall_width_mm"] == 500
    print(f"    OK: ops={len(data['patch']['operations'])}, changed={data['changed_paths']}")
    return True


def test_session_record(client) -> bool:
    print("  5. POST /api/core/session/record")
    r = client.post("/api/core/session/record", json={
        "project_type": "product_design",
        "project_id": "e2e_test_proj",
        "user_request": "접이식 욕실 선반 만들어줘",
        "intent": {"intent_type": "create_new", "target_object": "general", "constraints": {}, "confidence": 0.8},
        "variants": [],
        "critiques": [],
        "final_params": {},
        "accepted": True,
    })
    assert r.status_code == 200, f"status={r.status_code}, body={r.text[:200]}"
    data = r.json()
    assert "session_id" in data
    assert data["session_id"].startswith("sess_")
    print(f"    OK: session_id={data['session_id']}")
    return True


def test_session_stats(client) -> bool:
    print("  6. GET /api/core/session/stats/product_design")
    r = client.get("/api/core/session/stats/product_design")
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    assert "total" in data
    print(f"    OK: stats={data}")
    return True


def test_schema_get(client) -> bool:
    print("  7. GET /api/core/schema/product_design")
    r = client.get("/api/core/schema/product_design")
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    assert "engine_params" in data or "project_type" in data
    print(f"    OK: schema keys={list(data.keys())[:5]}")
    return True


def test_schema_aliases(client) -> bool:
    print("  8. GET /api/core/schema/product_design/aliases")
    r = client.get("/api/core/schema/product_design/aliases")
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    assert len(data) > 0
    print(f"    OK: {len(data)} aliases")
    return True


def test_resolve_alias(client) -> bool:
    print("  9. POST /api/core/schema/product_design/resolve-alias")
    r = client.post("/api/core/schema/product_design/resolve-alias", json={
        "expression": "전체 폭",
    })
    assert r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    assert data["resolved_path"] is not None
    print(f"    OK: '{data['expression']}' -> '{data['resolved_path']}'")
    return True


def test_drawing_ai(client) -> bool:
    print("  10. drawing_ai project_type")
    r = client.post("/api/core/intent/parse", json={
        "text": "단면도 추가해줘",
        "project_type": "drawing_ai",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["intent_type"] in ("create_new", "modify_existing")
    print(f"    OK: intent={data['intent_type']}, target={data['target_object']}")
    return True


TESTS = [
    test_status,
    test_intent_parse,
    test_variants_generate,
    test_patch_interpret,
    test_session_record,
    test_session_stats,
    test_schema_get,
    test_schema_aliases,
    test_resolve_alias,
    test_drawing_ai,
]


if __name__ == "__main__":
    print("=" * 60)
    print("core/ API E2E Tests (FastAPI TestClient)")
    print("=" * 60)

    client = _make_client()
    if client is None:
        print("SKIP: TestClient not available")
        sys.exit(0)

    passed = 0
    failed = 0

    for test_fn in TESTS:
        try:
            test_fn(client)
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} passed")
    if failed:
        print(f"  FAILURES: {failed}")
        sys.exit(1)
    else:
        print("ALL E2E TESTS PASSED!")
    print("=" * 60)
