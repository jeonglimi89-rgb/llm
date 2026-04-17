"""
tools/registry_contract.py — single source of truth for the *expected*
default ToolRegistry composition.

This module is the single place that pins down "what counts as a real tool"
vs "what counts as a manifest tool" and the exact set of tools the
``create_default_registry()`` factory is supposed to produce.

Why this module exists
======================
Tests previously hard-coded magic numbers (``len(real) == 4``,
``len(tools) >= 14``) and a stale string (``"manifest_written"``) for the
default registry composition. Whenever someone added a tool, the magic
numbers drifted and the assertions started failing — three of those stale
assertions ("the pre-existing stale 3") sat in the suite for multiple
tranches.

This module fixes the drift hazard by:
  1. Defining the **vocabulary** (real tool vs manifest tool) in one place
     so test/code/docs use the same words.
  2. Defining the **expected canonical sets** as Python tuples that test
     code reads directly. Adding/removing/renaming a tool now requires
     updating exactly *one* place — and forgetting to do so makes the
     drift-prevention tests fail loudly.
  3. Providing ``verify_default_registry_contract(reg)`` so any test
     that builds a default registry can call one function and assert the
     full contract in lockstep.

Vocabulary (the contract — one sentence each)
=============================================
- **real tool**: a registry entry whose handler executes inline when
  ``ToolRegistry.call(name, params)`` is invoked. The handler returns a
  dict with ``"status": "executed"`` and the actual computed payload.
  Registered with ``register(name, func, real=True)``.

- **manifest tool**: a registry entry whose handler writes a job manifest
  file (via ``tools/manifest_writer.write_manifest``) instead of running
  the work inline. The handler returns a dict with
  ``"status": "manifest_written"`` and the manifest path. Registered with
  ``register(name, func, real=False)``.

Current state of ``create_default_registry()``
==============================================
- All 14 tools are **real**. The manifest-tool path is preserved in
  ``ToolRegistry.register()`` and ``ToolRegistry.list_manifest_tools()``
  for forward compatibility but **no production registration uses it**.
  ``manifest_writer.write_manifest`` is still imported by ``registry.py``
  but currently has zero callers inside the registry module itself; it
  remains exported so external integrations (or future deferred-job
  registrations) can use it without re-introducing the API.

The 14 real tools, alphabetically (= what ``list_real_tools()`` returns)
========================================================================
animation.check_continuity
animation.render_preview
animation.solve_shot
builder.export
builder.generate_plan
builder.validate
cad.generate_part
cad.route_drainage
cad.route_wiring
cad.solve_assembly
cad.validate_geometry
minecraft.compile_archetype
minecraft.place_blocks
minecraft.validate_palette

Per-domain breakdown
====================
- animation: 3
- builder:   3
- cad:       5
- minecraft: 3
- TOTAL:     14
"""
from __future__ import annotations

from typing import Iterable


# ---------------------------------------------------------------------------
# Source of truth — alphabetically sorted to match list_real_tools()
# ---------------------------------------------------------------------------

EXPECTED_DEFAULT_REAL_TOOLS: tuple[str, ...] = (
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

# Manifest path is currently empty in production. The constant exists to
# make the contract symmetric: tests assert against it, and any future
# manifest-tool registration must update both the registry *and* this
# constant in lockstep.
EXPECTED_DEFAULT_MANIFEST_TOOLS: tuple[str, ...] = ()

# Per-domain expected counts (must equal the partition of
# EXPECTED_DEFAULT_REAL_TOOLS by ``name.split('.')[0]``).
EXPECTED_REAL_TOOLS_BY_DOMAIN: dict[str, int] = {
    "animation": 3,
    "builder":   3,
    "cad":       5,
    "minecraft": 3,
}

EXPECTED_DEFAULT_TOTAL_TOOLS: int = (
    len(EXPECTED_DEFAULT_REAL_TOOLS) + len(EXPECTED_DEFAULT_MANIFEST_TOOLS)
)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

class RegistryContractError(AssertionError):
    """Raised when ``verify_default_registry_contract`` finds drift.

    Subclass of ``AssertionError`` so plain ``assert``-style test runners
    treat it as a test failure, while still being catchable in tools that
    want to handle it specifically.
    """


def _domain_of(tool_name: str) -> str:
    return tool_name.split(".", 1)[0] if "." in tool_name else tool_name


def real_tools_by_domain(tool_names: Iterable[str]) -> dict[str, int]:
    """Group a flat tool list by domain prefix and return counts."""
    out: dict[str, int] = {}
    for n in tool_names:
        d = _domain_of(n)
        out[d] = out.get(d, 0) + 1
    return out


def verify_default_registry_contract(reg) -> None:
    """Assert that ``reg`` matches the canonical default registry contract.

    Checks performed
    ----------------
    1. ``list_tools()`` size equals ``EXPECTED_DEFAULT_TOTAL_TOOLS``.
    2. ``list_real_tools()`` (already sorted) equals
       ``list(EXPECTED_DEFAULT_REAL_TOOLS)`` exactly.
    3. ``list_manifest_tools()`` equals ``list(EXPECTED_DEFAULT_MANIFEST_TOOLS)``
       — i.e. the manifest path is currently empty.
    4. The per-domain partition of real tools equals
       ``EXPECTED_REAL_TOOLS_BY_DOMAIN``.

    Raises ``RegistryContractError`` on any drift.
    """
    real = list(reg.list_real_tools())
    manifest = list(reg.list_manifest_tools())
    all_tools = list(reg.list_tools())

    if len(all_tools) != EXPECTED_DEFAULT_TOTAL_TOOLS:
        raise RegistryContractError(
            f"registry total drift: got {len(all_tools)} tools, "
            f"expected {EXPECTED_DEFAULT_TOTAL_TOOLS}"
        )
    if real != list(EXPECTED_DEFAULT_REAL_TOOLS):
        raise RegistryContractError(
            f"real tools drift:\n  got      {real}\n  expected {list(EXPECTED_DEFAULT_REAL_TOOLS)}"
        )
    if manifest != list(EXPECTED_DEFAULT_MANIFEST_TOOLS):
        raise RegistryContractError(
            f"manifest tools drift:\n  got      {manifest}\n  expected {list(EXPECTED_DEFAULT_MANIFEST_TOOLS)}"
        )
    by_domain = real_tools_by_domain(real)
    if by_domain != EXPECTED_REAL_TOOLS_BY_DOMAIN:
        raise RegistryContractError(
            f"per-domain partition drift:\n  got      {by_domain}\n  expected {EXPECTED_REAL_TOOLS_BY_DOMAIN}"
        )
