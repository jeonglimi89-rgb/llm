"""
test_task_registry_spec.py — parametrized sweep of TASK_REGISTRY specs.

Locks every TaskSpec field for all 23 registered tasks so that adding,
removing, renaming, or changing a field (pool_type, timeout_class,
is_heavy, domain, enabled) fails the default gate immediately.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.domain.registry import TASK_REGISTRY, TaskSpec, get_task_spec, list_enabled_tasks
from src.app.execution.scheduler import HEAVY_TASKS


# ---------------------------------------------------------------------------
# Pinned inventory — the exact 18 specs as of the current repo state.
# Any addition, removal, or field change must update this table in lockstep.
# ---------------------------------------------------------------------------

EXPECTED_SPECS: list[dict] = [
    # Builder (5)
    {"task_type": "builder.requirement_parse", "domain": "builder", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "builder.patch_intent_parse", "domain": "builder", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "builder.zone_priority_parse", "domain": "builder", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "builder.exterior_style_parse", "domain": "builder", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "builder.context_query", "domain": "builder", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # Minecraft (3)
    {"task_type": "minecraft.edit_parse", "domain": "minecraft", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "minecraft.style_check", "domain": "minecraft", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "minecraft.anchor_resolution", "domain": "minecraft", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    # Animation (5)
    {"task_type": "animation.shot_parse", "domain": "animation", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "animation.camera_intent_parse", "domain": "animation", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "animation.lighting_intent_parse", "domain": "animation", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "animation.edit_patch_parse", "domain": "animation", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "animation.context_query", "domain": "animation", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # CAD (5)
    {"task_type": "cad.constraint_parse", "domain": "cad", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "cad.patch_parse", "domain": "cad", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "cad.system_split_parse", "domain": "cad", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "cad.priority_parse", "domain": "cad", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "cad.context_query", "domain": "cad", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # Product Design (5)
    {"task_type": "product_design.requirement_parse", "domain": "product_design", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "product_design.concept_parse", "domain": "product_design", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "product_design.bom_parse", "domain": "product_design", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "product_design.patch_parse", "domain": "product_design", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "product_design.context_query", "domain": "product_design", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # Minecraft build (1)
    {"task_type": "minecraft.build_parse", "domain": "minecraft", "pool_type": "strict_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # Minecraft — LLM Active Orchestration (4)
    {"task_type": "minecraft.build_planner", "domain": "minecraft", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "minecraft.variant_planner", "domain": "minecraft", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "minecraft.build_critic", "domain": "minecraft", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "minecraft.repair_planner", "domain": "minecraft", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    # Animation creative direction (1)
    {"task_type": "animation.creative_direction", "domain": "animation", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    # Resource Pack (3)
    {"task_type": "resourcepack.style_parse", "domain": "resourcepack", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "resourcepack.rp_planner", "domain": "resourcepack", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "resourcepack.rp_critic", "domain": "resourcepack", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    # NPC (4)
    {"task_type": "npc.character_parse", "domain": "npc", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "npc.dialogue_generate", "domain": "npc", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
    {"task_type": "npc.npc_planner", "domain": "npc", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": True,  "enabled": True},
    {"task_type": "npc.npc_critic", "domain": "npc", "pool_type": "creative_json", "timeout_class": "strict_json", "is_heavy": False, "enabled": True},
]


# ===========================================================================
# Parametrized spec field sweep
# ===========================================================================

@pytest.mark.parametrize(
    "expected",
    EXPECTED_SPECS,
    ids=[s["task_type"] for s in EXPECTED_SPECS],
)
def test_task_spec_fields_match_pinned_values(expected):
    """Every TaskSpec in TASK_REGISTRY must match the pinned field values
    in EXPECTED_SPECS. Changing a pool_type, timeout_class, is_heavy,
    domain, or enabled flag requires updating this table in lockstep."""
    tt = expected["task_type"]
    spec = get_task_spec(tt)
    assert spec is not None, f"TASK_REGISTRY missing {tt}"
    assert spec.task_type == tt
    assert spec.domain == expected["domain"], (tt, spec.domain)
    assert spec.pool_type == expected["pool_type"], (tt, spec.pool_type)
    assert spec.timeout_class == expected["timeout_class"], (tt, spec.timeout_class)
    assert spec.is_heavy == expected["is_heavy"], (tt, spec.is_heavy)
    assert spec.enabled == expected["enabled"], (tt, spec.enabled)


# ===========================================================================
# Inventory counts
# ===========================================================================

def test_task_registry_has_exactly_36_entries():
    """Pin the registry size so additions/removals are caught."""
    print(f"  [count] TASK_REGISTRY has {len(TASK_REGISTRY)} entries")
    assert len(TASK_REGISTRY) == 36, (
        f"expected 36, got {len(TASK_REGISTRY)}: {sorted(TASK_REGISTRY.keys())}"
    )
    print("    OK")


def test_all_36_tasks_are_enabled():
    """All current tasks are enabled."""
    print(f"  [enabled] all {len(TASK_REGISTRY)} tasks are enabled")
    enabled = list_enabled_tasks()
    assert len(enabled) == 36
    assert set(enabled) == set(TASK_REGISTRY.keys())
    print("    OK")


def test_per_domain_counts():
    """Pin the per-domain partition."""
    print("  [domains] per-domain task count matches")
    by_domain: dict[str, int] = {}
    for spec in TASK_REGISTRY.values():
        by_domain[spec.domain] = by_domain.get(spec.domain, 0) + 1
    assert by_domain == {
        "builder": 5,
        "minecraft": 8,
        "animation": 6,
        "cad": 5,
        "product_design": 5,
        "resourcepack": 3,
        "npc": 4,
    }, by_domain
    print("    OK")


# ===========================================================================
# Structural invariants
# ===========================================================================

def test_task_type_equals_domain_dot_task_name():
    """``task_type`` must always equal ``f"{domain}.{task_name}"``."""
    print("  [consistency] task_type == domain.task_name for all specs")
    for tt, spec in TASK_REGISTRY.items():
        expected = f"{spec.domain}.{spec.task_name}"
        assert spec.task_type == expected, (tt, spec.task_type, expected)
        assert tt == spec.task_type, (tt, spec.task_type)
    print("    OK")


def test_is_heavy_flag_consistent_with_scheduler_heavy_tasks():
    """``TaskSpec.is_heavy == True`` must exactly match membership in
    ``scheduler.HEAVY_TASKS`` for every task in TASK_REGISTRY. If they
    drift, the scheduler's cooldown kind selection and the registry's
    metadata disagree silently."""
    print("  [heavy sync] is_heavy flag matches scheduler.HEAVY_TASKS")
    registry_heavy = {tt for tt, s in TASK_REGISTRY.items() if s.is_heavy}

    # Tasks marked is_heavy=True in TASK_REGISTRY must be in HEAVY_TASKS.
    not_in_scheduler = registry_heavy - HEAVY_TASKS
    assert not not_in_scheduler, (
        f"is_heavy=True in TASK_REGISTRY but NOT in scheduler.HEAVY_TASKS: "
        f"{not_in_scheduler}"
    )

    # Tasks with is_heavy=False must NOT be in HEAVY_TASKS.
    registry_light = {tt for tt, s in TASK_REGISTRY.items() if not s.is_heavy}
    accidentally_heavy = registry_light & HEAVY_TASKS
    assert not accidentally_heavy, (
        f"is_heavy=False in TASK_REGISTRY but ARE in scheduler.HEAVY_TASKS: "
        f"{accidentally_heavy}"
    )
    print(f"    OK: {len(registry_heavy)} heavy tasks consistent")


def test_expected_specs_table_covers_entire_registry():
    """Drift-prevention: ``EXPECTED_SPECS`` must cover every key in
    ``TASK_REGISTRY``. A new task added to the registry without
    updating EXPECTED_SPECS fails here before the parametrized sweep
    can silently miss it."""
    print("  [drift] EXPECTED_SPECS covers entire TASK_REGISTRY")
    in_table = {s["task_type"] for s in EXPECTED_SPECS}
    in_registry = set(TASK_REGISTRY.keys())
    missing = in_registry - in_table
    extra = in_table - in_registry
    assert not missing, f"registry has tasks not in EXPECTED_SPECS: {sorted(missing)}"
    assert not extra, f"EXPECTED_SPECS has tasks not in registry: {sorted(extra)}"
    assert len(EXPECTED_SPECS) == len(TASK_REGISTRY)
    print("    OK")


TESTS = [
    test_task_registry_has_exactly_36_entries,
    test_all_36_tasks_are_enabled,
    test_per_domain_counts,
    test_task_type_equals_domain_dot_task_name,
    test_is_heavy_flag_consistent_with_scheduler_heavy_tasks,
    test_expected_specs_table_covers_entire_registry,
]


if __name__ == "__main__":
    print("=" * 60)
    print("TASK_REGISTRY spec sweep tests")
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
