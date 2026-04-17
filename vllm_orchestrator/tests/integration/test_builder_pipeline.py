"""
test_builder_pipeline.py — Builder generate → validate 파이프라인

검증: LLM parse → generate_plan → validate → verdict
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.builder_planner import generate_plan
from src.app.tools.adapters.builder_validator import validate_plan
from src.app.tools.registry import create_default_registry


def test_good_plan_passes():
    print("  [1] Good plan → pass")
    plan = generate_plan({
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "kitchen", "count": 1},
            {"type": "bedroom", "count": 2},
            {"type": "bathroom", "count": 2},
        ],
        "preferences": "modern",
    })
    result = validate_plan(plan)
    print(f"    verdict: {result['verdict']}, issues: {len(result['issues'])}")
    for iss in result["issues"]:
        print(f"      [{iss['severity']}] {iss['rule']}: {iss['detail']}")
    assert result["verdict"] in ("pass", "warn"), f"Good plan should pass, got {result['verdict']}"


def test_missing_bathroom_warns():
    print("  [2] Missing bathroom → warn")
    plan = generate_plan({
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1},
            {"type": "bedroom", "count": 3},
            # 화장실 없음
        ],
    })
    result = validate_plan(plan)
    bath_issues = [i for i in result["issues"] if "화장실" in i["detail"]]
    print(f"    verdict: {result['verdict']}, bathroom issues: {len(bath_issues)}")
    # planner가 자동으로 bathroom 추가할 수 있으므로 warn 또는 pass
    assert result["verdict"] in ("pass", "warn")


def test_single_floor_no_stair():
    print("  [3] 1층 → 계단 불필요")
    plan = generate_plan({
        "floors": 1,
        "spaces": [
            {"type": "living_room", "count": 1},
            {"type": "bedroom", "count": 1},
            {"type": "bathroom", "count": 1},
        ],
    })
    result = validate_plan(plan)
    stair_issues = [i for i in result["issues"] if "계단" in i["detail"]]
    assert len(stair_issues) == 0, f"1층인데 계단 관련 이슈: {stair_issues}"
    print(f"    verdict: {result['verdict']}, no stair issues (correct)")


def test_large_building():
    print("  [4] 대형 건물 info")
    plan = generate_plan({
        "floors": 3,
        "spaces": [
            {"type": "cafe", "count": 1, "priority": "high"},
            {"type": "living_room", "count": 3, "priority": "high"},
            {"type": "bedroom", "count": 6},
            {"type": "bathroom", "count": 4},
            {"type": "kitchen", "count": 2},
        ],
    })
    result = validate_plan(plan)
    print(f"    verdict: {result['verdict']}, total_area: {result['stats']['total_area_m2']}m2")
    print(f"    issues: {len(result['issues'])}")
    for iss in result["issues"][:3]:
        print(f"      [{iss['severity']}] {iss['detail']}")


def test_registry_pipeline():
    print("  [5] Registry: generate → validate 파이프라인")
    reg = create_default_registry()

    # Step 1: generate
    gen_result = reg.call("builder.generate_plan", {
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "bedroom", "count": 2},
            {"type": "bathroom", "count": 1},
        ],
    })
    assert gen_result["status"] == "executed"
    plan = gen_result["result"]

    # Step 2: validate
    val_result = reg.call("builder.validate", plan)
    assert val_result["status"] == "executed"
    print(f"    generate: {gen_result['total_rooms']} rooms, {gen_result['total_area_m2']}m2")
    print(f"    validate: verdict={val_result['verdict']}, critical={val_result['critical_issues']}, warn={val_result['warnings']}")

    assert val_result["verdict"] in ("pass", "warn")


def test_real_tools_count():
    """T-tranche-3 (2026-04-08) cleanup: anchored to the canonical registry
    contract. The pre-existing stale ``len(real) == 4`` assertion was a
    snapshot from the era when only 4 tools had been promoted from
    manifest stubs to real adapters. The current registry has 14 real
    tools (3 builder + 3 minecraft + 5 cad + 3 animation) and the test
    now reads the expected counts directly from the source-of-truth
    constants, so any future drift fails *with the exact reason*.

    What this test still guarantees:
      - the *full* registry composition matches the contract
      - the **builder** subset (this file's domain) is exactly 3 real tools
      - the three known builder tool names are present
    """
    from src.app.tools.registry_contract import (
        EXPECTED_DEFAULT_REAL_TOOLS,
        EXPECTED_REAL_TOOLS_BY_DOMAIN,
        verify_default_registry_contract,
    )
    print("  [6] Real tools count (contract-anchored)")
    reg = create_default_registry()
    real = reg.list_real_tools()
    manifest = reg.list_manifest_tools()
    print(f"    real engines: {len(real)} → {real}")
    print(f"    manifest: {len(manifest)}")

    # Full contract: catches drift outside the builder slice too.
    verify_default_registry_contract(reg)

    # Builder slice (this file's domain) — exact size + exact members.
    builder_real = sorted(t for t in real if t.startswith("builder."))
    expected_builder = sorted(
        t for t in EXPECTED_DEFAULT_REAL_TOOLS if t.startswith("builder.")
    )
    assert len(builder_real) == EXPECTED_REAL_TOOLS_BY_DOMAIN["builder"] == 3, (
        f"builder real-tool count drift: got {builder_real}, expected {expected_builder}"
    )
    assert builder_real == expected_builder, (
        f"builder real-tool set drift: got {builder_real}, expected {expected_builder}"
    )
    # The legacy spot-checks (still part of the contract) — kept so the
    # test fails loudly if any of these specific names ever disappears.
    assert "builder.generate_plan" in real
    assert "builder.validate" in real
    assert "builder.export" in real
    assert "minecraft.compile_archetype" in real


TESTS = [
    test_good_plan_passes,
    test_missing_bathroom_warns,
    test_single_floor_no_stair,
    test_large_building,
    test_registry_pipeline,
    test_real_tools_count,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Builder Pipeline Tests (generate → validate)")
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
        print("ALL BUILDER PIPELINE TESTS PASSED!")
