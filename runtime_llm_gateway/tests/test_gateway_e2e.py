"""
tests/test_gateway_e2e.py - Gateway E2E 테스트 (MockProvider)

실행: python -X utf8 -m runtime_llm_gateway.tests.test_gateway_e2e
"""

from __future__ import annotations

import sys
from pathlib import Path

# 패키지 경로
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.envelope import RequestEnvelope, Message
from runtime_llm_gateway.core.task_type import TASK_POOL_MAP
from runtime_llm_gateway.execution.gateway_service import RuntimeGatewayService
from runtime_llm_gateway.providers.vllm_provider import MockProvider
from runtime_llm_gateway.routing.task_router import TaskRouter, ShardSelector
from runtime_llm_gateway.telemetry.audit_logger import AuditLogger


def test_builder_requirement():
    print("  1. builder.requirement_parse")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="builder.requirement_parse",
        project_id="builder_proj_1",
        session_id="sess_1",
        messages=[Message(role="user", content="2층 주택이고 거실은 크게, 외관은 따뜻한 모던 스타일로 해줘.")],
        schema_id="builder/requirement_v1",
        priority="high",
    )
    resp = gw.process(req)
    assert resp.error_code is None, f"error: {resp.error_code}: {resp.error_message}"
    assert resp.validation.schema_ok, f"schema errors: {resp.validation.errors}"
    assert resp.structured_content is not None
    assert resp.model_profile == "strict-json-pool"
    print(f"    OK: profile={resp.model_profile}, shard={resp.route_shard}, latency={resp.latency_ms}ms")
    return True


def test_minecraft_edit():
    print("  2. minecraft.edit_parse")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="minecraft.edit_parse",
        project_id="mc_proj_8",
        session_id="sess_2",
        messages=[Message(role="user", content="정면 창문 더 넓게, 지붕은 유지하고 입구만 강조해.")],
        schema_id="minecraft/edit_patch_v1",
    )
    resp = gw.process(req)
    assert resp.error_code is None
    assert resp.structured_content is not None
    print(f"    OK: profile={resp.model_profile}, latency={resp.latency_ms}ms")
    return True


def test_animation_shot():
    print("  3. animation.shot_parse")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="animation.shot_parse",
        project_id="anim_proj_3",
        session_id="sess_3",
        messages=[Message(role="user", content="노을빛에 여주가 천천히 돌아보는 슬픈 컷.")],
        schema_id="animation/shot_graph_v1",
    )
    resp = gw.process(req)
    assert resp.error_code is None
    assert resp.structured_content is not None
    print(f"    OK: profile={resp.model_profile}, latency={resp.latency_ms}ms")
    return True


def test_cad_constraint():
    print("  4. cad.constraint_parse")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="cad.constraint_parse",
        project_id="cad_proj_4",
        session_id="sess_4",
        messages=[Message(role="user", content="외형은 심플하게 하고 배수 연결과 전기선 설계도 같이 고려해.")],
        schema_id="cad/constraint_v1",
    )
    resp = gw.process(req)
    assert resp.error_code is None
    assert resp.structured_content is not None
    print(f"    OK: profile={resp.model_profile}, latency={resp.latency_ms}ms")
    return True


def test_fast_chat():
    print("  5. fast-chat-pool (minecraft.patch_commentary)")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="minecraft.patch_commentary",
        project_id="mc_proj_8",
        session_id="sess_2",
        messages=[Message(role="user", content="방금 수정한 거 괜찮아 보여?")],
        schema_id="",
    )
    resp = gw.process(req)
    assert resp.error_code is None
    assert resp.raw_text is not None
    assert resp.model_profile == "fast-chat-pool"
    print(f"    OK: profile={resp.model_profile}, text='{resp.raw_text[:50]}'")
    return True


def test_shard_sticky():
    print("  6. shard sticky routing")
    ss = ShardSelector(shard_count=4)
    s1 = ss.select("proj_1", "sess_1", "strict-json-pool")
    s2 = ss.select("proj_1", "sess_1", "strict-json-pool")
    s3 = ss.select("proj_2", "sess_2", "strict-json-pool")
    assert s1 == s2, "same project+session should get same shard"
    # s3 may or may not differ, but routing is deterministic
    print(f"    OK: same=('{s1}'=='{s2}'), diff='{s3}'")
    return True


def test_metrics():
    print("  7. audit metrics")
    gw = _make_gateway()
    req = RequestEnvelope(
        task_type="builder.requirement_parse",
        project_id="test",
        session_id="test",
        messages=[Message(role="user", content="test")],
        schema_id="builder/requirement_v1",
    )
    gw.process(req)
    gw.process(req)
    metrics = gw.audit.get_metrics()
    assert metrics["total"] == 2
    assert metrics["success"] >= 0
    print(f"    OK: metrics={metrics}")
    return True


def test_task_routing_coverage():
    print("  8. all task_types have pool mappings")
    from runtime_llm_gateway.core.model_profile import DEFAULT_PROFILES
    missing = []
    for task, pool in TASK_POOL_MAP.items():
        if pool not in DEFAULT_PROFILES:
            missing.append(f"{task} -> {pool}")
    assert not missing, f"missing profiles: {missing}"
    print(f"    OK: {len(TASK_POOL_MAP)} tasks mapped to pools")
    return True


TESTS = [
    test_builder_requirement,
    test_minecraft_edit,
    test_animation_shot,
    test_cad_constraint,
    test_fast_chat,
    test_shard_sticky,
    test_metrics,
    test_task_routing_coverage,
]


def _make_gateway():
    import tempfile
    return RuntimeGatewayService(
        provider=MockProvider(),
        audit_logger=AuditLogger(log_dir=tempfile.mkdtemp()),
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Runtime LLM Gateway E2E Tests (MockProvider)")
    print("=" * 60)

    passed = 0
    failed = 0

    for test_fn in TESTS:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} passed")
    if failed:
        print(f"  FAILURES: {failed}")
        sys.exit(1)
    else:
        print("ALL GATEWAY E2E TESTS PASSED!")
    print("=" * 60)
