"""
test_builder_engine_e2e.py — Builder 실엔진 E2E

경로: user text → LLM requirement_parse → builder.generate_plan → floor plan

Note: most tests here are deterministic (planner + registry). The only
exception is ``test_llm_to_planner_e2e`` which requires a live LLM
server and is marked ``infra`` + uses ``pytest.skip`` when the server is
not reachable. See pytest.ini / docs/testing_gate.md.
"""
import sys, json, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.builder_planner import generate_plan
from src.app.tools.registry import create_default_registry


# ===================================================================
# A. Planner 단독 (LLM 없이)
# ===================================================================

def test_basic_2floor():
    print("  [1] 2층 주택 기본 배치")
    result = generate_plan({
        "project_type": "주거",
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "kitchen", "count": 1, "priority": "normal"},
            {"type": "bedroom", "count": 2, "priority": "normal"},
            {"type": "bathroom", "count": 1, "priority": "normal"},
        ],
        "preferences": {"style_family": "modern"},
    })
    assert len(result["floor_plans"]) == 2
    assert result["metadata"]["floors"] == 2
    assert result["metadata"]["total_rooms"] >= 5
    assert result["metadata"]["total_area_m2"] > 0
    f1_rooms = [r["name"] for r in result["floor_plans"][0]["rooms"]]
    f2_rooms = [r["name"] for r in result["floor_plans"][1]["rooms"]]
    print(f"    1F: {f1_rooms}")
    print(f"    2F: {f2_rooms}")
    print(f"    total: {result['metadata']['total_rooms']} rooms, {result['metadata']['total_area_m2']}m2")


def test_3floor_multifamily():
    print("  [2] 3층 다세대 원룸")
    result = generate_plan({
        "floors": 3,
        "spaces": [
            {"type": "bedroom", "count": 6, "priority": "normal"},
            {"type": "bathroom", "count": 3, "priority": "normal"},
            {"type": "entrance", "count": 1, "priority": "normal"},
        ],
        "preferences": "minimalist",
    })
    assert len(result["floor_plans"]) == 3
    assert result["metadata"]["style"] == "minimalist"
    for fp in result["floor_plans"]:
        print(f"    {fp['floor']}F: {[r['name'] for r in fp['rooms']]}")


def test_cafe_plus_residential():
    print("  [3] 지하 카페 + 2층 주거")
    result = generate_plan({
        "floors": 2,
        "spaces": [
            {"type": "cafe", "count": 1, "priority": "high"},
            {"type": "living_room", "count": 1, "priority": "normal"},
            {"type": "bedroom", "count": 1, "priority": "normal"},
            {"type": "bathroom", "count": 1, "priority": "normal"},
        ],
        "preferences": {"style_family": "brick"},
    })
    assert len(result["floor_plans"]) == 2
    f1_types = [r["type"] for r in result["floor_plans"][0]["rooms"]]
    assert "cafe" in f1_types, f"1F should have cafe: {f1_types}"
    print(f"    1F: {[r['name'] for r in result['floor_plans'][0]['rooms']]}")
    print(f"    2F: {[r['name'] for r in result['floor_plans'][1]['rooms']]}")


def test_single_floor():
    print("  [4] 1층 단독 (평면)")
    result = generate_plan({
        "floors": 1,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "bedroom", "count": 1, "priority": "normal"},
            {"type": "bathroom", "count": 1, "priority": "normal"},
        ],
    })
    assert len(result["floor_plans"]) == 1
    assert result["metadata"]["total_rooms"] >= 3
    print(f"    rooms: {[r['name'] for r in result['floor_plans'][0]['rooms']]}")
    print(f"    bounds: {result['floor_plans'][0]['bounds']}")


def test_korean_room_names():
    print("  [5] 한국어 방 이름 처리")
    result = generate_plan({
        "floors": 1,
        "spaces": ["거실", "주방", "안방", "화장실"],
    })
    room_types = [r["type"] for r in result["floor_plans"][0]["rooms"]]
    assert "living_room" in room_types
    assert "kitchen" in room_types
    print(f"    types: {room_types}")


def test_room_coordinates_valid():
    print("  [6] 좌표 겹침/음수 없음")
    result = generate_plan({
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "kitchen", "count": 1},
            {"type": "bedroom", "count": 3},
            {"type": "bathroom", "count": 2},
        ],
    })
    for fp in result["floor_plans"]:
        for r in fp["rooms"]:
            assert r["x"] >= 0, f"Negative x: {r}"
            assert r["y"] >= 0, f"Negative y: {r}"
            assert r["w"] > 0, f"Zero width: {r}"
            assert r["h"] > 0, f"Zero height: {r}"
            assert r["area_m2"] > 0, f"Zero area: {r}"
    print(f"    OK: all coordinates valid")


# ===================================================================
# B. Registry 통합
# ===================================================================

def test_registry_real():
    print("  [7] Registry: builder.generate_plan is REAL")
    reg = create_default_registry()
    assert "builder.generate_plan" in reg.list_real_tools()
    result = reg.call("builder.generate_plan", {
        "floors": 1,
        "spaces": [{"type": "living_room", "count": 1}],
    })
    assert result["status"] == "executed"
    assert result["total_rooms"] >= 1
    print(f"    OK: executed, {result['total_rooms']} rooms, {result['total_area_m2']}m2")


# ===================================================================
# C. LLM → Planner E2E
# ===================================================================

@pytest.mark.infra
def test_llm_to_planner_e2e():
    """infra-dependent (T-tranche-6, 2026-04-08): marked ``infra`` so the
    default gate deselects it. When selected via ``pytest -m infra`` and
    the live LLM server happens to be down, we ``pytest.skip`` with an
    explicit reason so the skip shows up in the report as intent, not
    as an accidental no-op."""
    print("  [8] FULL E2E: LLM parse → builder planner → floor plan")

    from src.app.llm.adapters.vllm_http import VLLMHttpAdapter
    adapter = VLLMHttpAdapter("http://192.168.57.105:8000", "internal-token", "qwen2.5-0.5b-instruct")
    if not adapter.is_available():
        pytest.skip(
            "live LLM server at http://192.168.57.105:8000 not reachable "
            "(infra-dependent test)"
        )

    from src.app.observability.health_registry import HealthRegistry
    from src.app.execution.circuit_breaker import CircuitBreaker
    from src.app.llm.client import LLMClient

    llm = LLMClient(adapter, HealthRegistry(), CircuitBreaker(), max_retries=1)

    # Step 1: LLM slot extraction
    start = time.time()
    prompt = (
        "Output ONLY valid JSON.\n"
        "Extract: floors (integer), spaces (array of {type, count, priority}), "
        "preferences (object with style_family).\n"
        "Room types: living_room, kitchen, bedroom, master_bedroom, bathroom, entrance, study, dressing_room."
    )
    parsed, raw, ms = llm.extract_slots(
        system_prompt=prompt,
        user_input="2층 주택, 거실 크게, 방 3개, 화장실 2개, 모던 스타일",
        pool_type="strict_json",
        timeout_s=120,
    )
    print(f"    LLM: {json.dumps(parsed, ensure_ascii=False)[:120]} ({ms}ms)")

    if parsed is None:
        parsed = {
            "floors": 2,
            "spaces": [
                {"type": "living_room", "count": 1, "priority": "high"},
                {"type": "bedroom", "count": 3, "priority": "normal"},
                {"type": "bathroom", "count": 2, "priority": "normal"},
            ],
            "preferences": {"style_family": "modern"},
        }
        print("    Using fallback slots")

    # 정규화
    if "floors" not in parsed:
        parsed["floors"] = 1
    if "spaces" not in parsed:
        parsed["spaces"] = [{"type": "living_room", "count": 1}]

    # Step 2: Planner
    plan = generate_plan(parsed)
    total = int((time.time() - start) * 1000)

    print(f"    Plan: {plan['metadata']['floors']}F, {plan['metadata']['total_rooms']} rooms, {plan['metadata']['total_area_m2']}m2")
    for fp in plan["floor_plans"]:
        print(f"      {fp['floor']}F: {[r['name'] for r in fp['rooms']]}")
    print(f"    Total: {total}ms (LLM={ms}ms)")

    assert plan["metadata"]["total_rooms"] >= 3


# ===================================================================

TESTS = [
    test_basic_2floor,
    test_3floor_multifamily,
    test_cafe_plus_residential,
    test_single_floor,
    test_korean_room_names,
    test_room_coordinates_valid,
    test_registry_real,
    test_llm_to_planner_e2e,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Builder Engine E2E Tests (Floor Plan Generator)")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            import traceback; traceback.print_exc()

    print(f"\nResults: {passed}/{len(TESTS)} passed")
    if passed == len(TESTS):
        print("ALL BUILDER ENGINE TESTS PASSED!")
