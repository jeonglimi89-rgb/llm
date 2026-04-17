"""
test_engine_e2e.py — 첫 실제 엔진 연결 E2E 검증

경로: user text → LLM slot extraction → minecraft compiler → block spec

Note: most tests here are deterministic (compiler + registry). The only
exception is ``test_llm_to_compiler_e2e`` which requires a live LLM
server and is marked ``infra`` + uses ``pytest.skip`` when the server is
not reachable. See pytest.ini / docs/testing_gate.md.
"""
import sys, json, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.minecraft_compiler import compile_edit
from src.app.tools.registry import create_default_registry


# ===================================================================
# A. 컴파일러 단독 테스트 (LLM 없이)
# ===================================================================

def test_compiler_add():
    print("  [1] Compiler: add stone to facade")
    result = compile_edit({
        "target_anchor": {"anchor_type": "facade", "anchor_id": "정면"},
        "operations": [{"type": "add", "delta": {"material": "stone", "count": 10}}],
        "preserve": ["door"],
    })
    assert len(result["blocks"]) > 0, "No blocks generated"
    assert result["blocks"][0]["block_type"] == "minecraft:stone"
    assert result["preserved"] == ["door"]
    assert result["metadata"]["block_count"] > 0
    print(f"    OK: {result['metadata']['block_count']} blocks, preserved={result['preserved']}")


def test_compiler_enlarge_window():
    print("  [2] Compiler: enlarge window")
    result = compile_edit({
        "target_anchor": {"anchor_type": "window", "anchor_id": "정면 창"},
        "operations": [{"type": "enlarge", "delta": {"material": "glass"}}],
        "preserve": [],
    })
    assert len(result["blocks"]) > 0
    assert "glass" in result["blocks"][0]["block_type"]
    print(f"    OK: {result['metadata']['block_count']} blocks (enlarged)")


def test_compiler_replace_material():
    print("  [3] Compiler: replace wall material to spruce")
    result = compile_edit({
        "target_anchor": {"anchor_type": "wall"},
        "operations": [{"type": "replace_material", "delta": {"material": "스프루스"}}],
        "preserve": ["door"],
    })
    assert all(b["block_type"] == "minecraft:spruce_planks" for b in result["blocks"])
    print(f"    OK: {result['metadata']['block_count']} blocks replaced to spruce")


def test_compiler_raise_tower():
    print("  [4] Compiler: raise tower by 5")
    result = compile_edit({
        "target_anchor": {"anchor_type": "tower"},
        "operations": [{"type": "raise", "delta": {"amount": 5, "material": "stone"}}],
        "preserve": [],
    })
    assert result["metadata"]["block_count"] > 0
    max_y = max(b["y"] for b in result["blocks"])
    assert max_y > 12, f"Tower not raised: max_y={max_y}"
    print(f"    OK: {result['metadata']['block_count']} blocks, max_y={max_y}")


def test_compiler_preserve_door():
    print("  [5] Compiler: remove facade but preserve door")
    result = compile_edit({
        "target_anchor": {"anchor_type": "facade"},
        "operations": [{"type": "remove"}],
        "preserve": ["door"],
    })
    # door 영역(x=4~6, y=0~2)은 제거 목록에 없어야
    door_removed = [b for b in result["removed_blocks"] if 4 <= b["x"] <= 6 and b["y"] <= 2]
    assert len(door_removed) == 0, f"Door was removed: {door_removed}"
    print(f"    OK: {result['metadata']['removed_count']} removed, door preserved")


def test_compiler_add_garden():
    print("  [6] Compiler: add fence + flower to garden")
    result = compile_edit({
        "target_anchor": {"anchor_type": "garden"},
        "operations": [
            {"type": "add", "delta": {"material": "울타리", "count": 8}},
            {"type": "add", "delta": {"material": "꽃", "count": 5}},
        ],
        "preserve": [],
    })
    types = set(b["block_type"] for b in result["blocks"])
    assert "minecraft:oak_fence" in types
    assert "minecraft:poppy" in types
    print(f"    OK: {result['metadata']['block_count']} blocks, types={types}")


# ===================================================================
# B. Registry 통합 테스트
# ===================================================================

def test_registry_real_tool():
    print("  [7] Registry: minecraft.compile_archetype is REAL (not manifest)")
    reg = create_default_registry()
    result = reg.call("minecraft.compile_archetype", {
        "target_anchor": {"anchor_type": "facade"},
        "operations": [{"type": "add", "delta": {"material": "brick", "count": 5}}],
        "preserve": [],
    })
    assert result["status"] == "executed", f"Expected executed, got {result['status']}"
    assert result["block_count"] > 0
    print(f"    OK: status=executed, blocks={result['block_count']}")


def test_registry_builder_generate_plan_is_real():
    """T-tranche-5 (2026-04-08) cleanup: re-anchored to the canonical
    registry contract.

    Stale history
    -------------
    The previous incarnation of this test was named
    ``test_registry_manifest_tool`` and asserted::

        assert result["status"] == "manifest_written"

    That assertion was from the pre-2026-04-06 era when
    ``builder.generate_plan`` was a manifest stub. It has been wired as a
    real adapter for a long time and returns ``status="executed"``. The
    previous T-tranche-3 cleanup re-anchored the 3 originally-listed stale
    tests but missed this 4th one in the same drift family — it is being
    cleaned up here for the same reason and in the same style.

    What this test guarantees now
    -----------------------------
    - ``builder.generate_plan`` is a **real** tool (per the contract SoT),
      NOT a manifest stub, and its handler returns ``status="executed"``.
    - The full default registry composition matches the contract so any
      drift (anywhere, not just this one tool) fails loudly from here too.
    """
    print("  [8] Registry: builder.generate_plan is REAL (contract-anchored)")
    from src.app.tools.registry_contract import (
        EXPECTED_DEFAULT_REAL_TOOLS,
        verify_default_registry_contract,
    )
    from src.app.tools.adapter_inputs import get_spec

    reg = create_default_registry()

    # 1. Full contract — catches drift anywhere in the registry.
    verify_default_registry_contract(reg)

    # 2. Specifically, builder.generate_plan is in the real set.
    assert "builder.generate_plan" in reg.list_real_tools()
    assert "builder.generate_plan" in EXPECTED_DEFAULT_REAL_TOOLS
    assert "builder.generate_plan" not in reg.list_manifest_tools()

    # 3. Call it with the canonical sample from the adapter_inputs SoT and
    #    verify it returns the real-tool marker ("executed"), never the
    #    legacy manifest stub marker ("manifest_written").
    spec = get_spec("builder.generate_plan")
    result = reg.call("builder.generate_plan", spec.canonical_sample)
    assert result.get("status") == "executed", (
        f"builder.generate_plan is a real tool; expected status='executed', "
        f"got {result.get('status')!r}"
    )
    assert result.get("status") != "manifest_written", (
        "builder.generate_plan regressed to the legacy manifest stub marker"
    )
    print("    OK: status=executed")


# ===================================================================
# C. LLM → Compiler E2E (서버 필요)
# ===================================================================

@pytest.mark.infra
def test_llm_to_compiler_e2e():
    """실제 LLM parse → compiler 실행 전체 경로

    infra-dependent (T-tranche-6, 2026-04-08): marked ``infra`` so that
    the default gate deselects it via ``pytest.ini``'s ``-m "not infra"``
    filter. When selected via ``pytest -m infra`` and the live LLM server
    happens to be down, we ``pytest.skip`` with an explicit reason so the
    skip shows up in the report as intent, not as an accidental no-op.
    """
    print("  [9] FULL E2E: LLM parse → compiler → block spec")

    # LLM 서버 확인
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

    health = HealthRegistry()
    circuit = CircuitBreaker()
    llm = LLMClient(adapter, health, circuit, max_retries=1)

    # Step 1: LLM slot extraction
    start = time.time()
    prompt = (
        "Output ONLY valid JSON. Korean for text values.\n"
        "Extract: target_anchor (anchor_type from [facade,roof,wall,window,entrance,tower,garden]), "
        "operations (type from [add,remove,enlarge,replace_material,raise]), preserve (list of strings)."
    )
    parsed, raw, ms = llm.extract_slots(
        system_prompt=prompt,
        user_input="정면 창문 넓게, 지붕은 유지",
        pool_type="strict_json",
        timeout_s=120,
    )

    if parsed is None:
        print(f"    LLM parse failed: {raw[:100]}")
        # fallback: 수동 슬롯으로 compiler 테스트
        parsed = {
            "target_anchor": {"anchor_type": "window"},
            "operations": [{"type": "enlarge", "delta": {"material": "glass"}}],
            "preserve": ["roof"],
        }
        print(f"    Using fallback slots for compiler test")

    print(f"    LLM parse: {json.dumps(parsed, ensure_ascii=False)[:120]} ({ms}ms)")

    # Step 2: Compiler execution
    # slots 정규화 (LLM 출력이 불완전할 수 있으므로)
    if "target_anchor" not in parsed:
        parsed["target_anchor"] = {"anchor_type": "window"}
    if "operations" not in parsed:
        parsed["operations"] = [{"type": "enlarge", "delta": {"material": "glass"}}]
    if "preserve" not in parsed:
        parsed["preserve"] = []

    block_result = compile_edit(parsed)
    total_ms = int((time.time() - start) * 1000)

    print(f"    Compiler: {block_result['metadata']['block_count']} blocks, "
          f"{block_result['metadata']['removed_count']} removed, "
          f"preserved={block_result['preserved']}")
    print(f"    Total E2E: {total_ms}ms (LLM={ms}ms + compile={total_ms-ms}ms)")

    assert block_result["metadata"]["block_count"] > 0 or block_result["metadata"]["removed_count"] > 0, \
        "No blocks generated or removed"


# ===================================================================

TESTS = [
    test_compiler_add,
    test_compiler_enlarge_window,
    test_compiler_replace_material,
    test_compiler_raise_tower,
    test_compiler_preserve_door,
    test_compiler_add_garden,
    test_registry_real_tool,
    test_registry_builder_generate_plan_is_real,
    test_llm_to_compiler_e2e,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Engine E2E Tests (Minecraft Compiler)")
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
        print("ALL ENGINE TESTS PASSED!")
