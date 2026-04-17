"""test_domain_profiles.py — Domain Profile Registry completeness tests."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.domain.profiles import load_domain_profiles

CONFIGS = Path(__file__).resolve().parent.parent.parent / "configs"

# Core domains that must always be present
_CORE_DOMAINS = {"cad", "builder", "minecraft", "animation", "product_design"}
# Extended domains (may be present, not required to have chains)
_EXTENDED_DOMAINS = {"resourcepack", "npc"}

def test_all_core_domains_have_profiles():
    profiles = load_domain_profiles(CONFIGS)
    assert _CORE_DOMAINS.issubset(set(profiles.keys())), (
        f"Missing core domains: {_CORE_DOMAINS - set(profiles.keys())}"
    )

def test_each_core_profile_has_required_fields():
    profiles = load_domain_profiles(CONFIGS)
    for domain, p in profiles.items():
        assert p.vocabulary, f"{domain}: empty vocabulary"
        assert p.task_signals, f"{domain}: empty task_signals"
        assert p.reasoning_template, f"{domain}: empty reasoning_template"
        assert p.required_output_keys, f"{domain}: empty required_output_keys"
        # chain_name and allowed_tools only required for core domains
        if domain in _CORE_DOMAINS:
            assert p.chain_name, f"{domain}: empty chain_name"
            assert p.allowed_tools, f"{domain}: empty allowed_tools"

def test_chain_names_exist_in_chains_config():
    profiles = load_domain_profiles(CONFIGS)
    from src.app.orchestration.task_chain import load_chain_definitions
    chains = load_chain_definitions(CONFIGS)
    for domain, p in profiles.items():
        if p.chain_name:  # extended domains may have null chain_name
            assert p.chain_name in chains, (
                f"{domain}: chain_name '{p.chain_name}' not found in task_chains.json. "
                f"Available: {list(chains.keys())}"
            )

def test_allowed_tools_exist_in_registry():
    profiles = load_domain_profiles(CONFIGS)
    from src.app.tools.registry import create_default_registry
    reg = create_default_registry()
    all_tools = set(reg.list_tools())
    for domain, p in profiles.items():
        for tool in p.allowed_tools:
            assert tool in all_tools, f"{domain}: tool '{tool}' not in registry"
