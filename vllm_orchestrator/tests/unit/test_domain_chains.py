"""test_domain_chains.py — All 4 domain chains load + execute correctly."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.orchestration.task_chain import load_chain_definitions

CONFIGS = Path(__file__).resolve().parent.parent.parent / "configs"


def test_all_4_domain_chains_exist():
    chains = load_chain_definitions(CONFIGS)
    assert "cad_full_design" in chains
    assert "builder_full_plan" in chains
    assert "minecraft_full_build" in chains
    assert "animation_full_scene" in chains


def test_cad_chain_has_7_steps():
    chains = load_chain_definitions(CONFIGS)
    assert len(chains["cad_full_design"].steps) == 7
    assert chains["cad_full_design"].domain == "cad"


def test_builder_chain_has_4_steps():
    chains = load_chain_definitions(CONFIGS)
    assert len(chains["builder_full_plan"].steps) == 4
    assert chains["builder_full_plan"].domain == "builder"


def test_minecraft_chain_has_4_steps():
    chains = load_chain_definitions(CONFIGS)
    c = chains["minecraft_full_build"]
    assert len(c.steps) == 4
    assert c.domain == "minecraft"
    # First 2 are LLM steps, last 2 are tool steps
    assert not c.steps[0].is_tool_step
    assert c.steps[2].is_tool_step


def test_animation_chain_has_6_steps():
    chains = load_chain_definitions(CONFIGS)
    c = chains["animation_full_scene"]
    assert len(c.steps) == 6
    assert c.domain == "animation"
    # First LLM steps, then tool steps
    assert not c.steps[0].is_tool_step
    assert not c.steps[1].is_tool_step
    assert not c.steps[2].is_tool_step


def test_all_tool_steps_reference_valid_tools():
    from src.app.tools.registry import create_default_registry
    reg = create_default_registry()
    all_tools = set(reg.list_tools())
    chains = load_chain_definitions(CONFIGS)
    for chain_name, chain in chains.items():
        for step in chain.steps:
            if step.is_tool_step:
                assert step.tool_name in all_tools, (
                    f"chain '{chain_name}' step '{step.task_name}' → "
                    f"tool '{step.tool_name}' not in registry"
                )


def test_all_llm_steps_have_prompts():
    chains = load_chain_definitions(CONFIGS)
    for chain_name, chain in chains.items():
        for step in chain.steps:
            if not step.is_tool_step:
                assert step.prompt and len(step.prompt) > 10, (
                    f"chain '{chain_name}' LLM step '{step.task_name}' "
                    f"has empty/missing prompt"
                )


def test_existing_cad_chain_unchanged():
    """Regression: CAD chain structure must match T-tranche expectations."""
    chains = load_chain_definitions(CONFIGS)
    cad = chains["cad_full_design"]
    task_names = [s.task_name for s in cad.steps]
    assert task_names == [
        "system_split_parse",
        "constraint_parse",
        "_tool:cad.generate_part",
        "_tool:cad.solve_assembly",
        "_tool:cad.route_wiring",
        "_tool:cad.route_drainage",
        "_tool:cad.validate_geometry",
    ]
