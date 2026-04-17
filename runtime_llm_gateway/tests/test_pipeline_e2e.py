"""
tests/test_pipeline_e2e.py - Planner→Executor→Critic 풀 파이프라인 E2E 테스트

실행: cd LLM && python -X utf8 -m runtime_llm_gateway.tests.test_pipeline_e2e
"""

from __future__ import annotations

import sys
import tempfile
import shutil
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.envelope import RequestEnvelope, Message
from runtime_llm_gateway.execution.pipeline_service import PipelineService
from runtime_llm_gateway.providers.vllm_provider import MockProvider
from runtime_llm_gateway.routing.tool_router import ToolRouter
from runtime_llm_gateway.datasets.dataset_builder import DatasetBuilder
from runtime_llm_gateway.memory.memory_store import MemoryStore
from runtime_llm_gateway.telemetry.audit_logger import AuditLogger


# ---------------------------------------------------------------------------
# Planner용 간단 스키마 (공통)
# ---------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "required": ["goal", "alternatives", "constraints", "uncertainties"],
    "properties": {
        "goal": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "object"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
}


def _exec_schema(program: str) -> dict:
    """프로그램별 executor 스키마"""
    import json
    schema_dir = Path(__file__).resolve().parent.parent / "schemas" / program
    # 가장 첫 번째 스키마 파일 사용
    for f in schema_dir.glob("*.schema.json"):
        return json.loads(f.read_text(encoding="utf-8"))
    return {"type": "object"}


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

def test_full_pipeline_builder():
    print("  1. Builder full pipeline")
    pipe = PipelineService(provider=MockProvider(), audit_logger=AuditLogger(tempfile.mkdtemp()))
    req = RequestEnvelope(
        task_type="builder.requirement_parse",
        project_id="bp1", session_id="s1",
        messages=[Message(role="user", content="2층 주택 거실 크게, 모던 스타일")],
        schema_id="builder/requirement_v1",
    )
    result = pipe.run_full_pipeline(req, PLAN_SCHEMA, _exec_schema("builder"))
    assert "plan" in result
    assert "execution" in result
    assert "review" in result
    print(f"    OK: plan_ok={result['validation']['plan_ok']}, critic={result['review'].get('verdict', '?')}, {result['total_latency_ms']}ms")


def test_full_pipeline_minecraft():
    print("  2. Minecraft full pipeline")
    pipe = PipelineService(provider=MockProvider(), audit_logger=AuditLogger(tempfile.mkdtemp()))
    req = RequestEnvelope(
        task_type="minecraft.edit_parse",
        project_id="mc1", session_id="s2",
        messages=[Message(role="user", content="중세풍 성 정면 창문 넓게")],
        schema_id="minecraft/edit_patch_v1",
    )
    result = pipe.run_full_pipeline(req, PLAN_SCHEMA, _exec_schema("minecraft"))
    assert result["plan"]
    assert result["execution"]
    print(f"    OK: critic={result['review'].get('verdict', '?')}, {result['total_latency_ms']}ms")


def test_full_pipeline_animation():
    print("  3. Animation full pipeline")
    pipe = PipelineService(provider=MockProvider(), audit_logger=AuditLogger(tempfile.mkdtemp()))
    req = RequestEnvelope(
        task_type="animation.shot_parse",
        project_id="an1", session_id="s3",
        messages=[Message(role="user", content="노을빛에 여주가 천천히 돌아보는 슬픈 컷")],
        schema_id="animation/shot_graph_v1",
    )
    result = pipe.run_full_pipeline(req, PLAN_SCHEMA, _exec_schema("animation"))
    assert result["plan"]
    assert result["execution"]
    print(f"    OK: critic={result['review'].get('verdict', '?')}, {result['total_latency_ms']}ms")


def test_full_pipeline_cad():
    print("  4. CAD full pipeline")
    pipe = PipelineService(provider=MockProvider(), audit_logger=AuditLogger(tempfile.mkdtemp()))
    req = RequestEnvelope(
        task_type="cad.constraint_parse",
        project_id="cad1", session_id="s4",
        messages=[Message(role="user", content="배수 연결 + 전기 배선 포함 샤워필터 설계")],
        schema_id="cad/constraint_v1",
    )
    result = pipe.run_full_pipeline(req, PLAN_SCHEMA, _exec_schema("cad"))
    assert result["plan"]
    assert result["execution"]
    print(f"    OK: critic={result['review'].get('verdict', '?')}, {result['total_latency_ms']}ms")


def test_tool_router():
    print("  5. Tool router")
    tr = ToolRouter()
    tools = tr.list_tools()
    assert len(tools) >= 18, f"expected 18+ tools, got {len(tools)}"

    r1 = tr.call("route_wiring", {"from": "pcb", "to": "motor"})
    assert r1["status"] == "dummy_ok"

    r2 = tr.call("nonexistent_tool", {})
    assert "error" in r2

    print(f"    OK: {len(tools)} tools registered, dispatch works")


def test_dataset_builder():
    print("  6. Dataset builder")
    tmp = tempfile.mkdtemp()
    try:
        ds = DatasetBuilder(tmp)

        # 파이프라인 실행 로그 기록
        ds.log_pipeline_run({
            "request_id": "req_001",
            "program": "cad",
            "user_request": "배수 연결 설계",
            "plan": {"goal": "drainage design"},
            "execution": {"systems": ["plumbing"]},
            "review": {"verdict": "pass"},
            "validation": {"critic_pass": True},
        })
        ds.log_user_edit("req_001", {"v": 1}, {"v": 2}, True)

        # 학습 데이터 생성
        counts = ds.build_all()
        assert counts["intent_pairs"] >= 1
        assert counts["plan_pairs"] >= 1
        assert counts["preference_pairs"] >= 1
        print(f"    OK: {counts}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_memory_store():
    print("  7. Memory store")
    tmp = tempfile.mkdtemp()
    try:
        mem = MemoryStore(tmp)

        # 세션 메모리
        mem.add_session_entry("s1", {"action": "edit", "detail": "width=360"})
        mem.add_session_entry("s1", {"action": "accept"})
        hist = mem.get_session_history("s1")
        assert len(hist) == 2

        # 프로젝트 메모리
        mem.save_approved("p1", {"design": "v2"})
        mem.save_rejected("p1", {"design": "v1"}, "too complex")
        approved = mem.get_project_history("p1", "approved")
        assert len(approved) == 1

        # 선호 메모리
        mem.update_preference("user1", "style", "minimal")
        mem.record_choice("user1", "cad", {"id": "a"}, [{"id": "b"}, {"id": "c"}])
        prefs = mem.get_preferences("user1")
        assert prefs["style"] == "minimal"

        print(f"    OK: session={len(hist)}, approved={len(approved)}, prefs={prefs}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    test_full_pipeline_builder,
    test_full_pipeline_minecraft,
    test_full_pipeline_animation,
    test_full_pipeline_cad,
    test_tool_router,
    test_dataset_builder,
    test_memory_store,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Full Pipeline E2E Tests (Planner→Executor→Critic)")
    print("=" * 60)

    passed = 0
    failed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} passed")
    if failed:
        print(f"  FAILURES: {failed}")
        sys.exit(1)
    else:
        print("ALL PIPELINE TESTS PASSED!")
    print("=" * 60)
