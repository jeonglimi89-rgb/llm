"""
test_real_llm_e2e.py — end-to-end tests with a REAL LLM server.

infra-dependent: requires a live LLM server at localhost:8000.
Marked @pytest.mark.infra so the default deterministic gate skips these.
Run explicitly with: pytest -m infra tests/integration/test_real_llm_e2e.py

These tests verify that the full pipeline (user input → prompt loading →
LLM slot extraction → layered review → tool adapter) produces meaningful
results with a real model, not just mock/fake adapters.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.settings import AppSettings
from src.app.bootstrap import Container
from src.app.core.contracts import TaskRequest
from src.app.tools.adapters.builder_planner import generate_plan


pytestmark = pytest.mark.infra


def _get_container():
    """Build a Container with real LLM. Skip if server is not reachable."""
    s = AppSettings()
    c = Container(settings=s)
    if c.llm_client.adapter.provider_name == "mock":
        pytest.skip("LLM server not available (using MockLLMAdapter)")
    return c


# ===========================================================================
# Builder domain
# ===========================================================================

def test_builder_requirement_parse_produces_valid_slots():
    """Real LLM extracts floors + spaces from a Korean building request."""
    c = _get_container()
    req = TaskRequest(domain="builder", task_name="requirement_parse",
                      user_input="2층 주택 거실 크게, 모던 스타일")
    spec = c.router.resolve(req)
    result = c.dispatcher.dispatch(req, spec)

    assert result.status == "done", f"status={result.status}, errors={result.errors}"
    assert result.slots is not None, "slots should not be None"
    # Structural checks — the LLM must produce these keys
    assert "floors" in result.slots, f"missing 'floors' in {result.slots}"
    assert isinstance(result.slots["floors"], int), f"floors should be int"
    assert result.slots["floors"] >= 1
    assert "spaces" in result.slots, f"missing 'spaces' in {result.slots}"
    assert isinstance(result.slots["spaces"], list)
    assert len(result.slots["spaces"]) >= 1


def test_builder_slots_feed_into_generate_plan():
    """Real LLM slots → generate_plan → produces floor plans."""
    c = _get_container()
    req = TaskRequest(domain="builder", task_name="requirement_parse",
                      user_input="3층 상가 건물, 1층 카페, 2-3층 사무실")
    spec = c.router.resolve(req)
    result = c.dispatcher.dispatch(req, spec)

    assert result.slots is not None
    plan = generate_plan(result.slots)
    assert "floor_plans" in plan
    assert len(plan["floor_plans"]) >= 1
    assert plan["metadata"]["total_rooms"] >= 1
    assert plan["metadata"]["total_area_m2"] > 0


# ===========================================================================
# Minecraft domain
# ===========================================================================

def test_minecraft_edit_parse_produces_valid_slots():
    """Real LLM extracts target_anchor + operations from a Korean edit request."""
    c = _get_container()
    req = TaskRequest(domain="minecraft", task_name="edit_parse",
                      user_input="정면 벽을 돌로 바꾸고 창문 크게")
    spec = c.router.resolve(req)
    result = c.dispatcher.dispatch(req, spec)

    assert result.status == "done"
    assert result.slots is not None
    assert "target_anchor" in result.slots
    assert "operations" in result.slots
    assert isinstance(result.slots["operations"], list)


# ===========================================================================
# CAD domain
# ===========================================================================

def test_cad_constraint_parse_produces_valid_slots():
    """Real LLM extracts constraints from a Korean design requirement."""
    c = _get_container()
    req = TaskRequest(domain="cad", task_name="constraint_parse",
                      user_input="방수 처리된 전자 부품, IP67 등급 필요")
    spec = c.router.resolve(req)
    result = c.dispatcher.dispatch(req, spec)

    assert result.status == "done"
    assert result.slots is not None
    assert "constraints" in result.slots
    assert isinstance(result.slots["constraints"], list)
    assert len(result.slots["constraints"]) >= 1


# ===========================================================================
# Animation domain
# ===========================================================================

def test_animation_shot_parse_produces_valid_slots():
    """Real LLM extracts framing + mood from a Korean shot description."""
    c = _get_container()
    req = TaskRequest(domain="animation", task_name="shot_parse",
                      user_input="비 오는 밤 외로운 와이드 샷")
    spec = c.router.resolve(req)
    result = c.dispatcher.dispatch(req, spec)

    assert result.status == "done"
    assert result.slots is not None
    assert "framing" in result.slots
    assert "mood" in result.slots


# ===========================================================================
# Cross-domain: prompt section isolation
# ===========================================================================

def test_prompt_section_isolation_no_cross_contamination():
    """Two different tasks from the same domain file should produce
    structurally different outputs. requirement_parse should have
    'floors'/'spaces'; patch_intent_parse should have 'intent'."""
    c = _get_container()

    req1 = TaskRequest(domain="builder", task_name="requirement_parse",
                       user_input="1층 원룸 작게")
    r1 = c.dispatcher.dispatch(req1, c.router.resolve(req1))

    req2 = TaskRequest(domain="builder", task_name="patch_intent_parse",
                       user_input="거실을 더 넓게 해줘")
    r2 = c.dispatcher.dispatch(req2, c.router.resolve(req2))

    assert r1.slots is not None and r2.slots is not None
    # requirement_parse → floors/spaces structure
    assert "floors" in r1.slots or "spaces" in r1.slots, (
        f"requirement_parse should have floors/spaces, got {r1.slots}"
    )
    # patch_intent_parse → intent structure
    assert "intent" in r2.slots or "operation_type" in r2.slots, (
        f"patch_intent_parse should have intent/operation_type, got {r2.slots}"
    )
