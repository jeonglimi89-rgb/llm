"""
test_minecraft_pipeline.py — Minecraft compile → validate_palette 파이프라인
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.minecraft_compiler import compile_edit
from src.app.tools.adapters.minecraft_palette_validator import validate_palette
from src.app.tools.registry import create_default_registry


def test_good_palette_passes():
    print("  [1] Stone facade → palette pass")
    blocks = compile_edit({
        "target_anchor": {"anchor_type": "facade"},
        "operations": [{"type": "add", "delta": {"material": "stone", "count": 20}}],
        "preserve": [],
    })
    result = validate_palette(blocks, theme="medieval")
    assert result["verdict"] in ("pass", "warn")
    assert result["checks"]["theme_ok"]
    print(f"    verdict={result['verdict']}, score={result['style_score']}, types={result['stats']['unique_block_types']}")


def test_theme_violation():
    print("  [2] Concrete in medieval → theme violation")
    # 수동 블록 (concrete는 medieval에서 금지)
    fake_blocks = {
        "blocks": [{"x": i, "y": 0, "z": 0, "block_type": "minecraft:concrete"} for i in range(10)],
        "metadata": {"block_count": 10},
    }
    result = validate_palette(fake_blocks, theme="medieval")
    assert not result["checks"]["theme_ok"], "Should fail theme check"
    theme_issues = [i for i in result["issues"] if "테마" in i["detail"]]
    assert len(theme_issues) > 0
    print(f"    verdict={result['verdict']}, theme_ok=False (correct)")


def test_mixed_materials():
    print("  [3] Mixed oak + stone → variety check")
    blocks = compile_edit({
        "target_anchor": {"anchor_type": "facade"},
        "operations": [
            {"type": "add", "delta": {"material": "oak", "count": 10}},
            {"type": "add", "delta": {"material": "stone", "count": 10}},
        ],
        "preserve": [],
    })
    result = validate_palette(blocks)
    assert result["stats"]["unique_block_types"] == 2
    print(f"    types={result['stats']['unique_block_types']}, families={result['stats']['families']}")


def test_enlarge_then_validate():
    print("  [4] Enlarge window → validate")
    blocks = compile_edit({
        "target_anchor": {"anchor_type": "window"},
        "operations": [{"type": "enlarge", "delta": {"material": "glass"}}],
        "preserve": [],
    })
    result = validate_palette(blocks, theme="modern")
    assert result["checks"]["theme_ok"], "Glass should be OK for modern"
    print(f"    verdict={result['verdict']}, score={result['style_score']}")


def test_registry_pipeline():
    print("  [5] Registry: compile → validate_palette 파이프라인")
    reg = create_default_registry()

    # Step 1: compile
    comp_result = reg.call("minecraft.compile_archetype", {
        "target_anchor": {"anchor_type": "facade"},
        "operations": [{"type": "add", "delta": {"material": "brick", "count": 15}}],
        "preserve": ["door"],
    })
    assert comp_result["status"] == "executed"

    # Step 2: validate palette
    val_result = reg.call("minecraft.validate_palette", comp_result["result"])
    assert val_result["status"] == "executed"
    print(f"    compile: {comp_result['block_count']} blocks")
    print(f"    validate: verdict={val_result['verdict']}, score={val_result['style_score']}")


def test_real_tools_count():
    """T-tranche-3 (2026-04-08) cleanup: anchored to the canonical registry
    contract. The pre-existing stale ``len(real) == 4`` assertion came from
    the early-2026 era when only 4 tools were registered as real adapters
    (the rest were manifest stubs). The current registry has 14 real
    tools across 4 domains; this file's slice is the **3 minecraft** ones.
    See ``tools/registry_contract.py`` for the full contract.

    What this test still guarantees:
      - the full registry composition matches the contract
      - the **minecraft** subset is exactly 3 real tools
      - the three known minecraft tool names are present
    """
    from src.app.tools.registry_contract import (
        EXPECTED_DEFAULT_REAL_TOOLS,
        EXPECTED_REAL_TOOLS_BY_DOMAIN,
        verify_default_registry_contract,
    )
    print("  [6] Real tools count (contract-anchored)")
    reg = create_default_registry()
    real = reg.list_real_tools()
    print(f"    real: {real}")

    # Full contract — catches drift in any domain.
    verify_default_registry_contract(reg)

    # Minecraft slice — exact size + exact members.
    mc_real = sorted(t for t in real if t.startswith("minecraft."))
    expected_mc = sorted(
        t for t in EXPECTED_DEFAULT_REAL_TOOLS if t.startswith("minecraft.")
    )
    assert len(mc_real) == EXPECTED_REAL_TOOLS_BY_DOMAIN["minecraft"] == 3, (
        f"minecraft real-tool count drift: got {mc_real}, expected {expected_mc}"
    )
    assert mc_real == expected_mc, (
        f"minecraft real-tool set drift: got {mc_real}, expected {expected_mc}"
    )
    assert "minecraft.compile_archetype" in real
    assert "minecraft.validate_palette" in real
    assert "minecraft.place_blocks" in real


TESTS = [
    test_good_palette_passes,
    test_theme_violation,
    test_mixed_materials,
    test_enlarge_then_validate,
    test_registry_pipeline,
    test_real_tools_count,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Minecraft Pipeline Tests (compile → validate_palette)")
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
        print("ALL MINECRAFT PIPELINE TESTS PASSED!")
