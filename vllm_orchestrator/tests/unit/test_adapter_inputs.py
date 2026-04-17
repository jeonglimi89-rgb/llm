"""
test_adapter_inputs.py — drift-prevention tests for
``src/app/tools/adapter_inputs.py``.

Background
==========
T-tranche-4 (2026-04-08) extracted the 14 canonical adapter input samples
into ``tools/adapter_inputs.py`` so the sample shapes live in exactly one
place. Before this tranche:

  1. Each adapter had its input shape documented in its own docstring.
  2. ``tools/registry.py`` handler closures read ``params.get(...)`` inline.
  3. ``tests/unit/test_tools_registry_contract.py`` carried an inline
     ``inputs = {...}`` dict as the fourth source of truth.

Source #3 is now gone — the contract test reads from the new module. This
test file is the dedicated drift-prevention layer for that module.

Invariants checked here
=======================
1. Constants self-consistency
   - ``ADAPTER_INPUT_SPECS`` has exactly 14 entries
   - Each spec's ``domain`` matches its ``tool_name`` prefix
   - The per-domain count partition is 3/3/5/3 (animation/builder/cad/minecraft)

2. Sync with the registry contract
   - ``ADAPTER_INPUT_SPECS.keys() == EXPECTED_DEFAULT_REAL_TOOLS`` (set equality)
   - ``verify_spec_table_matches_registry(reg)`` raises on real-vs-spec drift
     in either direction (missing / orphan)

3. Vocabulary: every canonical sample hits status="executed"
   - ``verify_sample_inputs_exercise_registry(reg)`` PASS on the live registry
   - No sample may produce ``{"error": ...}``
   - No sample may produce any status other than ``"executed"``

4. Drift fail-loud paths
   - Adding a phantom spec entry (orphan)  → ``AdapterInputSpecError``
   - Adding a phantom real tool (missing)   → ``AdapterInputSpecError``
   - Breaking a canonical sample            → ``AdapterInputSpecError``

5. Public helpers behave
   - ``get_spec(tool_name)`` returns the right spec
   - ``get_spec(unknown)`` raises ``KeyError`` with a helpful message
   - ``sample_inputs_dict()`` returns a flat ``{tool_name: sample}`` mapping
   - ``specs_by_domain()`` groups correctly
   - ``AdapterInputSpec.__post_init__`` catches domain/tool_name mismatch

6. The minecraft_palette_validator empty-blocks fix regression test
   - The T-tranche-4 fix ensures ``stats`` is always present in the output.
   - This test calls the registry handler with empty ``blocks`` and asserts
     the handler does not KeyError on ``stats["unique_block_types"]``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.registry import create_default_registry, ToolRegistry
from src.app.tools.registry_contract import EXPECTED_DEFAULT_REAL_TOOLS
from src.app.tools.adapter_inputs import (
    AdapterInputSpec,
    AdapterInputSpecError,
    ADAPTER_INPUT_SPECS,
    get_spec,
    sample_inputs_dict,
    verify_spec_table_matches_registry,
    verify_sample_inputs_exercise_registry,
    specs_by_domain,
)


# ---------------------------------------------------------------------------
# 1. ADAPTER_INPUT_SPECS table — internal consistency
# ---------------------------------------------------------------------------

def test_spec_table_has_exactly_fourteen_entries():
    print("  [1] ADAPTER_INPUT_SPECS has exactly 14 entries")
    assert len(ADAPTER_INPUT_SPECS) == 14
    print("    OK")


def test_spec_table_keys_match_registry_constant():
    print("  [2] ADAPTER_INPUT_SPECS.keys() == EXPECTED_DEFAULT_REAL_TOOLS (set equality)")
    assert set(ADAPTER_INPUT_SPECS.keys()) == set(EXPECTED_DEFAULT_REAL_TOOLS)
    print("    OK")


def test_each_spec_has_matching_domain_prefix():
    print("  [3] Each spec.domain equals the prefix of spec.tool_name")
    for name, spec in ADAPTER_INPUT_SPECS.items():
        expected_domain = name.split(".", 1)[0]
        assert spec.domain == expected_domain, (
            f"{name}: spec.domain={spec.domain!r}, expected {expected_domain!r}"
        )
    print("    OK")


def test_spec_domain_partition_matches_contract():
    print("  [4] per-domain partition is 3/3/5/3 (animation/builder/cad/minecraft)")
    grouped = specs_by_domain()
    assert {k: len(v) for k, v in grouped.items()} == {
        "animation": 3,
        "builder":   3,
        "cad":       5,
        "minecraft": 3,
    }
    print(f"    OK: {[(k, len(v)) for k, v in sorted(grouped.items())]}")


def test_every_spec_has_required_keys_present_in_sample():
    """If ``required_keys`` is non-empty and the sample is a dict, every
    required key must be present at the sample's top level."""
    print("  [5] Every spec.required_keys is a subset of sample top-level keys")
    failures = []
    for name, spec in ADAPTER_INPUT_SPECS.items():
        if not spec.required_keys:
            continue
        if not isinstance(spec.canonical_sample, dict):
            continue
        missing = [k for k in spec.required_keys if k not in spec.canonical_sample]
        if missing:
            failures.append(f"{name}: required_keys {missing} missing from sample")
    assert not failures, "\n  - ".join(["drifts:"] + failures)
    print("    OK")


def test_docstring_shape_is_nonempty_string():
    print("  [6] Every spec.docstring_shape is a non-empty string")
    for name, spec in ADAPTER_INPUT_SPECS.items():
        assert isinstance(spec.docstring_shape, str), f"{name}: docstring_shape not str"
        assert len(spec.docstring_shape) > 0, f"{name}: docstring_shape empty"
    print("    OK")


# ---------------------------------------------------------------------------
# 2. Sync with the registry contract
# ---------------------------------------------------------------------------

def test_verify_spec_table_matches_registry_passes_on_default():
    print("  [7] verify_spec_table_matches_registry(default_registry) PASS")
    reg = create_default_registry()
    verify_spec_table_matches_registry(reg)
    print("    OK")


def test_verify_spec_table_raises_on_missing_entry():
    """Add a real tool with no spec → spec table is missing an entry → raise."""
    print("  [8] verify_spec_table_matches_registry raises when a real tool has no spec")
    reg = create_default_registry()
    reg.register("phantom.new_tool", lambda p: {"status": "executed"}, real=True)
    raised = False
    try:
        verify_spec_table_matches_registry(reg)
    except AdapterInputSpecError as e:
        assert "phantom.new_tool" in str(e)
        assert "real tools without a spec" in str(e)
        raised = True
    assert raised, "expected AdapterInputSpecError"
    print("    OK")


def test_verify_spec_table_raises_on_orphan_entry():
    """A fake empty registry (ToolRegistry with no real tools) → every spec
    becomes an orphan → raise."""
    print("  [9] verify_spec_table_matches_registry raises on orphan specs (empty reg)")
    empty = ToolRegistry()
    raised = False
    try:
        verify_spec_table_matches_registry(empty)
    except AdapterInputSpecError as e:
        assert "specs without a real tool" in str(e)
        raised = True
    assert raised, "expected AdapterInputSpecError"
    print("    OK")


# ---------------------------------------------------------------------------
# 3. Vocabulary contract: every sample hits status="executed" on the live reg
# ---------------------------------------------------------------------------

def test_verify_sample_inputs_exercise_registry_passes_on_default():
    print("  [10] verify_sample_inputs_exercise_registry(default_registry) PASS")
    reg = create_default_registry()
    verify_sample_inputs_exercise_registry(reg)
    print("    OK")


def test_every_canonical_sample_produces_executed_status():
    """Exhaustive — call every handler with its canonical sample and assert
    ``status == "executed"`` and no ``error`` key."""
    print("  [11] every canonical sample produces status='executed' (exhaustive)")
    reg = create_default_registry()
    for name, spec in ADAPTER_INPUT_SPECS.items():
        result = reg.call(name, spec.canonical_sample)
        assert "error" not in result, f"{name}: {result.get('error')!r}"
        assert result.get("status") == "executed", (
            f"{name}: got {result.get('status')!r}"
        )
    print(f"    OK: {len(ADAPTER_INPUT_SPECS)} samples all clean")


# ---------------------------------------------------------------------------
# 4. Public helpers
# ---------------------------------------------------------------------------

def test_get_spec_returns_right_spec():
    print("  [12] get_spec returns the right AdapterInputSpec")
    spec = get_spec("cad.generate_part")
    assert isinstance(spec, AdapterInputSpec)
    assert spec.tool_name == "cad.generate_part"
    assert spec.domain == "cad"
    assert spec.canonical_sample == {"name": "bracket"}
    print("    OK")


def test_get_spec_raises_on_unknown_tool():
    print("  [13] get_spec raises KeyError on unknown tool name")
    raised = False
    try:
        get_spec("doesnotexist.at_all")
    except KeyError as e:
        assert "doesnotexist.at_all" in str(e)
        assert "known tools" in str(e)
        raised = True
    assert raised, "expected KeyError"
    print("    OK")


def test_sample_inputs_dict_returns_flat_mapping():
    print("  [14] sample_inputs_dict returns {tool_name: canonical_sample}")
    d = sample_inputs_dict()
    assert len(d) == len(ADAPTER_INPUT_SPECS) == 14
    assert set(d.keys()) == set(ADAPTER_INPUT_SPECS.keys())
    # Spot-check a couple
    assert d["cad.generate_part"] == {"name": "bracket"}
    assert d["animation.solve_shot"] == {"framing": "medium", "mood": "neutral"}
    print("    OK")


def test_specs_by_domain_groups_correctly():
    print("  [15] specs_by_domain groups 14 specs into 4 domains")
    grouped = specs_by_domain()
    assert set(grouped.keys()) == {"animation", "builder", "cad", "minecraft"}
    for spec_list in grouped.values():
        for s in spec_list:
            assert isinstance(s, AdapterInputSpec)
    # Domain partition sizes
    assert len(grouped["animation"]) == 3
    assert len(grouped["builder"]) == 3
    assert len(grouped["cad"]) == 5
    assert len(grouped["minecraft"]) == 3
    print("    OK")


def test_spec_post_init_catches_domain_mismatch():
    print("  [16] AdapterInputSpec.__post_init__ raises when domain prefix mismatches")
    raised = False
    try:
        AdapterInputSpec(
            tool_name="cad.generate_part",
            domain="builder",  # ← wrong
            required_keys=(),
            canonical_sample={},
            docstring_shape="x",
        )
    except ValueError as e:
        assert "domain mismatch" in str(e)
        raised = True
    assert raised, "expected ValueError"
    print("    OK")


# ---------------------------------------------------------------------------
# 5. Regression: T-tranche-4 minecraft_palette_validator empty-blocks fix
# ---------------------------------------------------------------------------

def test_minecraft_validate_palette_accepts_empty_blocks_without_keyerror():
    """Regression for the T-tranche-4 adapter bug.

    Before the fix, calling ``minecraft.validate_palette`` with an empty
    ``blocks`` list made the registry handler KeyError on
    ``result["stats"]["unique_block_types"]`` because the adapter's
    early-return branch dropped ``stats``. The fix ensures ``stats`` is
    always present; this test pins the fix.
    """
    print("  [17] minecraft.validate_palette with empty blocks → status=executed (no KeyError)")
    reg = create_default_registry()
    result = reg.call("minecraft.validate_palette", {"blocks": [], "metadata": {}})
    assert "error" not in result, f"unexpected error: {result.get('error')!r}"
    assert result.get("status") == "executed"
    assert "unique_types" in result
    assert result["unique_types"] == 0
    print("    OK")


def test_minecraft_validate_palette_still_works_on_non_empty_blocks():
    """Sanity: the fix did not regress the non-empty path."""
    print("  [18] minecraft.validate_palette with non-empty blocks still works")
    reg = create_default_registry()
    result = reg.call("minecraft.validate_palette", {
        "blocks": [
            {"x": 0, "y": 0, "z": 0, "block_type": "minecraft:stone"},
            {"x": 1, "y": 0, "z": 0, "block_type": "minecraft:oak_planks"},
        ],
        "metadata": {"block_count": 2, "removed_count": 0},
    })
    assert "error" not in result
    assert result.get("status") == "executed"
    assert result["unique_types"] == 2
    print("    OK")


# ---------------------------------------------------------------------------
# 6. Integration: contract-test / adapter-input module handshake
# ---------------------------------------------------------------------------

def test_sample_inputs_dict_covers_every_real_tool():
    """The single source of truth must cover every real tool in the contract."""
    print("  [19] sample_inputs_dict covers every EXPECTED_DEFAULT_REAL_TOOL")
    covered = set(sample_inputs_dict().keys())
    expected = set(EXPECTED_DEFAULT_REAL_TOOLS)
    missing = sorted(expected - covered)
    orphan  = sorted(covered - expected)
    assert not missing, f"missing samples: {missing}"
    assert not orphan, f"orphan samples: {orphan}"
    print("    OK")


TESTS = [
    test_spec_table_has_exactly_fourteen_entries,
    test_spec_table_keys_match_registry_constant,
    test_each_spec_has_matching_domain_prefix,
    test_spec_domain_partition_matches_contract,
    test_every_spec_has_required_keys_present_in_sample,
    test_docstring_shape_is_nonempty_string,
    test_verify_spec_table_matches_registry_passes_on_default,
    test_verify_spec_table_raises_on_missing_entry,
    test_verify_spec_table_raises_on_orphan_entry,
    test_verify_sample_inputs_exercise_registry_passes_on_default,
    test_every_canonical_sample_produces_executed_status,
    test_get_spec_returns_right_spec,
    test_get_spec_raises_on_unknown_tool,
    test_sample_inputs_dict_returns_flat_mapping,
    test_specs_by_domain_groups_correctly,
    test_spec_post_init_catches_domain_mismatch,
    test_minecraft_validate_palette_accepts_empty_blocks_without_keyerror,
    test_minecraft_validate_palette_still_works_on_non_empty_blocks,
    test_sample_inputs_dict_covers_every_real_tool,
]


if __name__ == "__main__":
    print("=" * 60)
    print("adapter inputs drift-prevention tests")
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
