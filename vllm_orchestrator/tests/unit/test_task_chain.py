"""
test_task_chain.py — Multi-step chain engine + product template tests.
Default gate, deterministic, no LLM calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.orchestration.task_chain import (
    TaskChainEngine, ChainDefinition, ChainStepDef, ChainResult,
    load_chain_definitions,
)
from src.app.domain.product_templates import (
    load_product_templates, match_template, ProductTemplate,
)
from src.app.tools.registry import create_default_registry
from src.app.tools.adapters.cad_part_generator import generate_part
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.execution.timeouts import TimeoutPolicy
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.llm.client import LLMClient
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


class _FakeLLM:
    provider_name = "fake"
    def __init__(self, responses=None):
        self._responses = responses or []
        self._call_idx = 0
    def is_available(self): return True
    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        if self._call_idx < len(self._responses):
            text = self._responses[self._call_idx]
        else:
            text = '{"fallback": true}'
        self._call_idx += 1
        return {"text": text, "prompt_tokens": 0, "completion_tokens": 0}


def _build_chain_engine(responses=None):
    adapter = _FakeLLM(responses)
    llm = LLMClient(adapter, HealthRegistry(), CircuitBreaker(), max_retries=0)
    router = Router()
    dispatcher = Dispatcher(
        llm_client=llm,
        queue=QueueManager(max_concurrency=1, max_depth=10),
        scheduler=Scheduler(),
        timeouts=TimeoutPolicy(),
        prompts_dir=Path(__file__).resolve().parent.parent.parent / "prompts",
    )
    tools = create_default_registry()
    return TaskChainEngine(dispatcher, router, tools)


# ===========================================================================
# Test 1: Chain loads from configs
# ===========================================================================

def test_chain_definitions_load_from_json():
    print("  [1] Chain definitions load from configs/task_chains.json")
    chains = load_chain_definitions(CONFIGS_DIR)
    assert "cad_full_design" in chains
    assert chains["cad_full_design"].domain == "cad"
    assert len(chains["cad_full_design"].steps) == 7
    # First 2 are LLM steps, rest are tool steps
    assert not chains["cad_full_design"].steps[0].is_tool_step
    assert chains["cad_full_design"].steps[2].is_tool_step
    assert chains["cad_full_design"].steps[2].tool_name == "cad.generate_part"
    print(f"    OK: {len(chains)} chains loaded, cad_full_design has 7 steps")


# ===========================================================================
# Test 2: Product template matching
# ===========================================================================

def test_product_template_match():
    print("  [2] Product template: '샤워필터' → shower_filter")
    templates = load_product_templates(CONFIGS_DIR)
    assert len(templates) >= 2
    match = match_template("충전식 샤워필터 설계해줘", templates)
    assert match is not None
    assert match.product_id == "shower_filter"
    assert "plumbing" in match.systems
    assert len(match.default_parts) >= 5
    print(f"    OK: matched {match.product_id}, {len(match.default_parts)} parts")


def test_product_template_no_match():
    print("  [3] Product template: unrelated input → None")
    templates = load_product_templates(CONFIGS_DIR)
    match = match_template("오늘 날씨 좋다", templates)
    assert match is None
    print("    OK: no match")


# ===========================================================================
# Test 3: Template enrichment → generate_part with real parts
# ===========================================================================

def test_template_enrichment_produces_real_parts():
    print("  [4] Template enrichment: shower_filter → 9 real parts")
    templates = load_product_templates(CONFIGS_DIR)
    template = templates["shower_filter"]
    enrichment = template.to_slots_enrichment()

    # 직접 generate_part 에 enrichment 전달
    result = generate_part(enrichment)
    part_names = [p["name"] for p in result["parts"]]

    assert len(result["parts"]) >= 5, f"expected 5+ parts, got {len(result['parts'])}"
    assert "filter_housing" in part_names
    assert "filter_cartridge" in part_names
    assert "usb_charge_port" in part_names
    assert "battery_cell" in part_names
    assert result["metadata"]["waterproof"] is True

    # 치수 확인: filter_housing 은 70x70x220mm
    housing = next(p for p in result["parts"] if p["name"] == "filter_housing")
    assert housing["estimated_dims_mm"]["z"] == 220, housing
    assert housing["material"] == "PP (식품용)", housing

    print(f"    OK: {len(result['parts'])} parts, waterproof={result['metadata']['waterproof']}")
    for p in result["parts"][:5]:
        print(f"      {p['part_id']} {p['name']}: {p['material']} {p['estimated_dims_mm']}")


# ===========================================================================
# Test 4: Chain engine executes LLM + tool steps
# ===========================================================================

def test_chain_engine_executes_mixed_steps():
    print("  [5] Chain engine: 2 LLM steps + 1 tool step")
    engine = _build_chain_engine(responses=[
        # Step 0 (system_split): LLM response
        '{"systems": [{"name": "mechanical"}, {"name": "electrical"}]}',
        # Step 1 (constraint): LLM response
        '{"constraints": [{"constraint_type": "방수", "description": "IP67", "category": "기계"}]}',
    ])

    chain = ChainDefinition(
        domain="cad",
        name="test_chain",
        steps=[
            ChainStepDef(task_name="system_split_parse", prompt="Extract systems as JSON."),
            ChainStepDef(task_name="constraint_parse", prompt="Extract constraints as JSON."),
            ChainStepDef(task_name="_tool:cad.generate_part", prompt=None),
        ],
    )
    result = engine.execute_chain(chain, "방수 제품 설계", context={})

    assert len(result.steps_completed) == 3
    assert result.steps_completed[0].success  # LLM step
    assert result.steps_completed[1].success  # LLM step
    assert result.steps_completed[2].success  # tool step
    assert result.steps_completed[2].is_tool
    assert result.steps_completed[2].tool_output is not None
    # Tool step 결과에 parts 가 있어야 함
    tool_out = result.steps_completed[2].tool_output
    assert "parts" in tool_out.get("result", tool_out), tool_out
    print(f"    OK: 3 steps completed, chain success={result.success}")


# ===========================================================================
# Test 5: Chain with template enrichment E2E
# ===========================================================================

def test_chain_with_template_enrichment_e2e():
    print("  [6] Chain + template: shower_filter enrichment → real parts in chain output")
    templates = load_product_templates(CONFIGS_DIR)
    template = templates["shower_filter"]
    enrichment = template.to_slots_enrichment()

    engine = _build_chain_engine(responses=[
        '{"systems": [{"name": "mechanical"}, {"name": "electrical"}, {"name": "plumbing"}]}',
        '{"constraints": [{"constraint_type": "방수", "description": "IP67 방수", "category": "기계"}]}',
    ])

    chain = ChainDefinition(
        domain="cad",
        name="cad_full_design",
        steps=[
            ChainStepDef(task_name="system_split_parse", prompt="Systems extraction."),
            ChainStepDef(task_name="constraint_parse", prompt="Constraints extraction."),
            ChainStepDef(task_name="_tool:cad.generate_part", prompt=None),
        ],
    )
    result = engine.execute_chain(chain, "충전식 샤워필터", enrichment=enrichment)

    assert result.success
    # generate_part 가 template 부품으로 실행되었는지 확인
    tool_step = result.steps_completed[2]
    assert tool_step.success
    tool_out = tool_step.tool_output
    inner = tool_out.get("result", tool_out)
    part_names = [p["name"] for p in inner.get("parts", [])]
    # Template 부품이 나와야 함 (generic housing/frame 이 아님)
    assert "filter_housing" in part_names or "filter_cartridge" in part_names, (
        f"expected template parts, got {part_names}"
    )
    print(f"    OK: chain with template enrichment produced {len(part_names)} parts")


TESTS = [
    test_chain_definitions_load_from_json,
    test_product_template_match,
    test_product_template_no_match,
    test_template_enrichment_produces_real_parts,
    test_chain_engine_executes_mixed_steps,
    test_chain_with_template_enrichment_e2e,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Task Chain + Product Template Tests")
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
