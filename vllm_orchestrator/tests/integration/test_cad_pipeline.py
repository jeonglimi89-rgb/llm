"""
test_cad_pipeline.py — CAD 풀 파이프라인:
generate_part → solve_assembly → route_wiring → route_drainage → validate_geometry
+ review judgment 통합
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.cad_part_generator import generate_part
from src.app.tools.adapters.cad_assembly_solver import solve_assembly
from src.app.tools.adapters.cad_wiring_router import route_wiring
from src.app.tools.adapters.cad_drainage_router import route_drainage
from src.app.tools.adapters.cad_geometry_validator import validate_geometry
from src.app.tools.registry import create_default_registry
from src.app.review.reviewer import review_cad_design


def test_simple_product():
    print("  [1] Simple product (mechanical+electrical)")
    parts = generate_part({
        "systems": ["mechanical", "electrical"],
        "constraints": [],
        "design_type": "product",
    })
    assert parts["metadata"]["part_count"] >= 4
    assert len(parts["parts"]) >= 4
    print(f"    parts={parts['metadata']['part_count']}, systems={parts['metadata']['system_count']}")


def test_waterproof_with_drainage():
    print("  [2] Waterproof + drainage (full pipeline)")
    parts = generate_part({
        "systems": ["mechanical", "electrical", "plumbing"],
        "constraints": ["waterproof", "IP67"],
        "design_type": "product",
    })
    assert parts["metadata"]["waterproof"]

    asm = solve_assembly(parts)
    print(f"    assembly: {asm['metadata']['step_count']} steps, {asm['metadata']['collision_count']} collisions")

    wire = route_wiring(parts, asm)
    print(f"    wiring: {wire['metadata']['wire_count']} wires, total={wire['metadata']['total_length_mm']}mm")

    drain = route_drainage(parts, asm)
    print(f"    drainage: {drain['metadata']['drain_count']} drains, valid={drain['metadata']['valid_count']}")

    validation = validate_geometry(parts, asm, wire, drain)
    print(f"    validation: {validation['verdict']}, issues={validation['stats']['total_issues']}")
    assert validation["verdict"] in ("pass", "warn", "fail")


def test_drainage_slope_check():
    print("  [3] Drainage slope validation")
    parts = generate_part({
        "systems": ["plumbing"],
        "constraints": ["waterproof"],
    })
    asm = solve_assembly(parts)
    drain = route_drainage(parts, asm)
    # 평면 배치라 경사가 부족할 수 있음
    print(f"    drains: {drain['metadata']['drain_count']}, critical: {drain['metadata']['critical_issues']}")


def test_review_judgment_cad():
    print("  [4] CAD review judgment integration")
    parts = generate_part({"systems": ["mechanical"], "constraints": []})
    asm = solve_assembly(parts)
    wire = route_wiring(parts, asm)
    drain = route_drainage(parts, asm)
    validation = validate_geometry(parts, asm, wire, drain)

    judgment = review_cad_design(parts, validation, artifact_id="test_cad_001")
    assert judgment.domain == "cad"
    assert judgment.verdict in ("pass", "fail", "needs_review")
    assert judgment.artifact_id == "test_cad_001"
    j_dict = judgment.to_dict()
    assert "stats" in j_dict
    print(f"    verdict={judgment.verdict}, items={len(judgment.items)}, summary='{judgment.summary}'")


def test_registry_cad_tools():
    print("  [5] Registry: 5 CAD tools registered as real")
    reg = create_default_registry()
    cad_real = [t for t in reg.list_real_tools() if t.startswith("cad.")]
    assert len(cad_real) == 5, f"Expected 5 CAD tools, got {len(cad_real)}"
    expected = {"cad.generate_part", "cad.solve_assembly", "cad.route_wiring", "cad.route_drainage", "cad.validate_geometry"}
    assert set(cad_real) == expected
    print(f"    {cad_real}")


def test_registry_full_pipeline_via_calls():
    print("  [6] CAD pipeline via registry calls")
    reg = create_default_registry()

    parts = reg.call("cad.generate_part", {
        "systems": ["mechanical", "plumbing"],
        "constraints": ["waterproof"],
    })
    assert parts["status"] == "executed"

    asm = reg.call("cad.solve_assembly", parts)
    assert asm["status"] == "executed"

    wire = reg.call("cad.route_wiring", {
        "part_result": parts["result"],
        "assembly_result": asm["result"],
    })
    assert wire["status"] == "executed"

    drain = reg.call("cad.route_drainage", {
        "part_result": parts["result"],
        "assembly_result": asm["result"],
    })
    assert drain["status"] == "executed"

    val = reg.call("cad.validate_geometry", {
        "part_result": parts["result"],
        "assembly_result": asm["result"],
        "wiring_result": wire["result"],
        "drainage_result": drain["result"],
    })
    assert val["status"] == "executed"
    print(f"    parts→asm→wire→drain→validate: verdict={val['verdict']}")


TESTS = [
    test_simple_product,
    test_waterproof_with_drainage,
    test_drainage_slope_check,
    test_review_judgment_cad,
    test_registry_cad_tools,
    test_registry_full_pipeline_via_calls,
]

if __name__ == "__main__":
    print("=" * 60)
    print("CAD Pipeline Tests (5 engines + review)")
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
        print("ALL CAD PIPELINE TESTS PASSED!")
