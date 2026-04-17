"""
test_tools_registry_contract.py — drift-prevention tests for the canonical
``tools.registry`` composition.

Background
==========
T-tranche-3 (2026-04-08) cleanup converted three pre-existing stale
assertions (the "stale 3":
``test_core.py::test_tools_registry``,
``test_builder_pipeline.py::test_real_tools_count``,
``test_minecraft_pipeline.py::test_real_tools_count``)
into contract-anchored tests that read the expected real-tool list from
``src/app/tools/registry_contract.py``.

This file is the *dedicated* drift-prevention layer. The fixed tests in
``test_core.py`` / `test_builder_pipeline.py` / ``test_minecraft_pipeline.py``
exercise the contract from their domain perspective; this file exercises
the contract module itself in isolation, including:

  - vocabulary correctness (real vs manifest definition)
  - per-domain partition equality
  - source-of-truth equality (registered set == constant set)
  - call-status correctness for every real tool
  - the ``RegistryContractError`` raise path
  - the ``manifest_writer`` import is still re-exported (forward compat)

If you add or rename a tool, update ``registry_contract.py`` first; this
file's tests will tell you what else moved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.registry import (
    ToolRegistry,
    create_default_registry,
    write_manifest,  # forward-compat re-export
)
from src.app.tools.registry_contract import (
    EXPECTED_DEFAULT_REAL_TOOLS,
    EXPECTED_DEFAULT_MANIFEST_TOOLS,
    EXPECTED_DEFAULT_TOTAL_TOOLS,
    EXPECTED_REAL_TOOLS_BY_DOMAIN,
    real_tools_by_domain,
    verify_default_registry_contract,
    RegistryContractError,
    _domain_of,
)


# ---------------------------------------------------------------------------
# 1. Source-of-truth constants are internally consistent
# ---------------------------------------------------------------------------

def test_constants_internal_consistency():
    """The constants in registry_contract must be self-consistent."""
    print("  [1] EXPECTED constants are internally consistent")
    # Total = real + manifest
    assert EXPECTED_DEFAULT_TOTAL_TOOLS == (
        len(EXPECTED_DEFAULT_REAL_TOOLS) + len(EXPECTED_DEFAULT_MANIFEST_TOOLS)
    )
    # Per-domain partition sums to len(real)
    assert sum(EXPECTED_REAL_TOOLS_BY_DOMAIN.values()) == len(EXPECTED_DEFAULT_REAL_TOOLS)
    # Real tools list is sorted (== list_real_tools() shape)
    assert list(EXPECTED_DEFAULT_REAL_TOOLS) == sorted(EXPECTED_DEFAULT_REAL_TOOLS)
    # No duplicates
    assert len(set(EXPECTED_DEFAULT_REAL_TOOLS)) == len(EXPECTED_DEFAULT_REAL_TOOLS)
    # Real and manifest sets are disjoint
    assert not (set(EXPECTED_DEFAULT_REAL_TOOLS) & set(EXPECTED_DEFAULT_MANIFEST_TOOLS))
    # Per-domain count from list-derivation == per-domain constant
    assert real_tools_by_domain(EXPECTED_DEFAULT_REAL_TOOLS) == EXPECTED_REAL_TOOLS_BY_DOMAIN
    print("    OK")


def test_constants_canonical_values():
    """The exact constant values are pinned. If you legitimately change the
    registry, change these constants in lockstep — that's the whole point."""
    print("  [2] EXPECTED constants have the canonical values for T-tranche-3")
    assert EXPECTED_DEFAULT_TOTAL_TOOLS == 14
    assert len(EXPECTED_DEFAULT_REAL_TOOLS) == 14
    assert EXPECTED_DEFAULT_MANIFEST_TOOLS == ()
    assert EXPECTED_REAL_TOOLS_BY_DOMAIN == {
        "animation": 3,
        "builder":   3,
        "cad":       5,
        "minecraft": 3,
    }
    print("    OK")


def test_constants_full_alphabetical_listing():
    """The 14 names — pinned alphabetically."""
    print("  [3] EXPECTED real tools list is exactly the documented 14")
    assert EXPECTED_DEFAULT_REAL_TOOLS == (
        "animation.check_continuity",
        "animation.render_preview",
        "animation.solve_shot",
        "builder.export",
        "builder.generate_plan",
        "builder.validate",
        "cad.generate_part",
        "cad.route_drainage",
        "cad.route_wiring",
        "cad.solve_assembly",
        "cad.validate_geometry",
        "minecraft.compile_archetype",
        "minecraft.place_blocks",
        "minecraft.validate_palette",
    )
    print("    OK")


# ---------------------------------------------------------------------------
# 2. The actual default registry matches the constants
# ---------------------------------------------------------------------------

def test_default_registry_matches_contract():
    """``create_default_registry()`` produces a registry that matches every
    expected constant exactly. This is the single drift fail-loud."""
    print("  [4] create_default_registry() satisfies the full contract")
    reg = create_default_registry()
    verify_default_registry_contract(reg)
    print("    OK")


def test_default_registry_total_count():
    print("  [5] reg.list_tools() count == EXPECTED_DEFAULT_TOTAL_TOOLS")
    reg = create_default_registry()
    assert len(reg.list_tools()) == EXPECTED_DEFAULT_TOTAL_TOOLS == 14
    print("    OK")


def test_default_registry_real_set_equals_constant():
    print("  [6] reg.list_real_tools() == EXPECTED_DEFAULT_REAL_TOOLS exactly")
    reg = create_default_registry()
    assert tuple(reg.list_real_tools()) == EXPECTED_DEFAULT_REAL_TOOLS
    print("    OK")


def test_default_registry_manifest_set_is_empty():
    print("  [7] reg.list_manifest_tools() is empty (no manifest tools today)")
    reg = create_default_registry()
    assert tuple(reg.list_manifest_tools()) == EXPECTED_DEFAULT_MANIFEST_TOOLS == ()
    print("    OK")


def test_default_registry_per_domain_partition():
    print("  [8] per-domain real tool counts match the constants")
    reg = create_default_registry()
    observed = real_tools_by_domain(reg.list_real_tools())
    assert observed == EXPECTED_REAL_TOOLS_BY_DOMAIN
    print(f"    OK: {observed}")


# ---------------------------------------------------------------------------
# 3. Vocabulary contract: real tools return status="executed", not "manifest_written"
# ---------------------------------------------------------------------------

def test_every_real_tool_returns_status_executed():
    """Every entry in ``EXPECTED_DEFAULT_REAL_TOOLS`` must, when called via
    ``ToolRegistry.call(...)``, return a dict containing ``status="executed"``.

    This is the *contract definition* of "real tool". It catches any future
    accidental flip from real → manifest stub.

    T-tranche-4 (2026-04-08) cleanup
    ---------------------------------
    Canonical sample inputs previously lived as an inline ``inputs = {...}``
    dict in this test (T-tranche-3 remaining risk #2). They have been
    extracted into ``tools/adapter_inputs.py`` so the sample shapes live in
    one place and any future adapter / handler change raises a single
    ``AdapterInputSpecError`` via ``verify_sample_inputs_exercise_registry``.
    This test now reads from ``sample_inputs_dict()``; if you add or rename
    a tool, update ``ADAPTER_INPUT_SPECS`` first.

    Additionally, this version upgrades the error-tolerance clause: previously
    an ``{"error": ...}`` result was silently tolerated (which would have
    masked the minecraft.validate_palette KeyError bug that T-tranche-4
    surfaced). Now an error path is a test failure, because the canonical
    samples are expected to produce a clean ``status="executed"``.
    """
    print("  [9] every real tool returns status='executed' (vocabulary contract)")
    from src.app.tools.adapter_inputs import sample_inputs_dict
    reg = create_default_registry()
    inputs = sample_inputs_dict()
    assert set(inputs.keys()) == set(EXPECTED_DEFAULT_REAL_TOOLS), (
        "adapter_inputs spec table is out of sync with registry_contract"
    )
    for name in EXPECTED_DEFAULT_REAL_TOOLS:
        params = inputs[name]
        result = reg.call(name, params)
        assert isinstance(result, dict), f"{name}: result is not a dict"
        # Legacy manifest_written marker is forbidden — that was the pre-2026-04-06
        # stub return value.
        assert result.get("status") != "manifest_written", (
            f"{name}: returned legacy manifest_written status — real-tool contract broken"
        )
        # T-tranche-4: the canonical samples MUST hit the executed path. If a
        # handler now errors on its own canonical sample, either the sample
        # drifted out of date or a real bug got introduced; both cases are
        # things the test should surface, not silently tolerate.
        assert "error" not in result, (
            f"{name}: canonical sample hit the error path — result={result.get('error')!r}. "
            f"Update ADAPTER_INPUT_SPECS[{name!r}] or fix the underlying adapter."
        )
        assert result.get("status") == "executed", (
            f"{name}: expected status='executed', got {result.get('status')!r}"
        )
    print(f"    OK: all {len(EXPECTED_DEFAULT_REAL_TOOLS)} real tools satisfy the vocabulary contract")


# ---------------------------------------------------------------------------
# 4. Drift-fail path: verify_default_registry_contract raises on drift
# ---------------------------------------------------------------------------

def test_verify_raises_when_real_set_drifts():
    print("  [10] verify_default_registry_contract raises on real-set drift")
    reg = create_default_registry()
    # Add a phantom tool that's NOT in the constants → drift
    reg.register("evil.phantom", lambda p: {"status": "executed"}, real=True)
    raised = False
    try:
        verify_default_registry_contract(reg)
    except RegistryContractError as e:
        assert "real tools drift" in str(e) or "registry total drift" in str(e)
        raised = True
    assert raised, "expected RegistryContractError"
    print("    OK")


def test_verify_raises_when_manifest_path_used():
    print("  [11] verify_default_registry_contract raises if a manifest tool appears")
    reg = create_default_registry()
    reg.register("evil.manifest_only", lambda p: {"status": "manifest_written"}, real=False)
    raised = False
    try:
        verify_default_registry_contract(reg)
    except RegistryContractError:
        raised = True
    assert raised, "expected RegistryContractError"
    print("    OK")


def test_verify_raises_on_per_domain_drift():
    print("  [12] verify_default_registry_contract raises on per-domain drift")
    reg = create_default_registry()
    # Add a tool in a brand-new domain → per-domain partition mismatch
    reg.register("zzz.something", lambda p: {"status": "executed"}, real=True)
    raised = False
    try:
        verify_default_registry_contract(reg)
    except RegistryContractError:
        raised = True
    assert raised
    print("    OK")


# ---------------------------------------------------------------------------
# 5. Public API surface preservation
# ---------------------------------------------------------------------------

def test_registry_exposes_real_and_manifest_lists():
    print("  [13] ToolRegistry exposes list_real_tools / list_manifest_tools")
    reg = create_default_registry()
    assert callable(reg.list_real_tools)
    assert callable(reg.list_manifest_tools)
    assert callable(reg.list_tools)
    print("    OK")


def test_write_manifest_re_exported_from_registry_module():
    """``write_manifest`` is intentionally re-exported from
    ``tools.registry`` for forward compatibility (deferred-job tools).
    Removing the import would silently break that contract."""
    print("  [14] tools.registry re-exports write_manifest for forward compat")
    assert callable(write_manifest)
    print("    OK")


def test_register_supports_both_real_and_manifest_paths():
    """The ``register(..., real=True/False)`` lever still works in both
    directions even though production currently uses only ``real=True``."""
    print("  [15] ToolRegistry.register accepts both real=True and real=False")
    reg = ToolRegistry()
    reg.register("a.real", lambda p: {"status": "executed"}, real=True)
    reg.register("b.manifest", lambda p: {"status": "manifest_written"}, real=False)
    assert reg.list_real_tools() == ["a.real"]
    assert reg.list_manifest_tools() == ["b.manifest"]
    assert sorted(reg.list_tools()) == ["a.real", "b.manifest"]
    print("    OK")


# ---------------------------------------------------------------------------
# 6. _domain_of helper sanity
# ---------------------------------------------------------------------------

def test_domain_of_helper():
    print("  [16] _domain_of returns prefix before first dot")
    assert _domain_of("animation.solve_shot") == "animation"
    assert _domain_of("cad.generate_part") == "cad"
    assert _domain_of("plain") == "plain"
    print("    OK")


TESTS = [
    test_constants_internal_consistency,
    test_constants_canonical_values,
    test_constants_full_alphabetical_listing,
    test_default_registry_matches_contract,
    test_default_registry_total_count,
    test_default_registry_real_set_equals_constant,
    test_default_registry_manifest_set_is_empty,
    test_default_registry_per_domain_partition,
    test_every_real_tool_returns_status_executed,
    test_verify_raises_when_real_set_drifts,
    test_verify_raises_when_manifest_path_used,
    test_verify_raises_on_per_domain_drift,
    test_registry_exposes_real_and_manifest_lists,
    test_write_manifest_re_exported_from_registry_module,
    test_register_supports_both_real_and_manifest_paths,
    test_domain_of_helper,
]


if __name__ == "__main__":
    print("=" * 60)
    print("tools registry contract drift-prevention tests")
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
