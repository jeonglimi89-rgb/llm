"""
tools/adapter_inputs.py — single source of truth for the 14 adapter input shapes.

Background
==========
T-tranche-3 (2026-04-08) pinned the registry *composition* (what tools exist,
what kind they are, how many per domain) in ``tools/registry_contract.py``.
But the *input shapes* each handler accepts were still scattered:

  1. Each adapter's own docstring (free-form comment)
  2. ``tools/registry.py`` handler closures (inline ``params.get(...)`` calls)
  3. ``tests/unit/test_tools_registry_contract.py::test_every_real_tool_returns_status_executed``
     (a hard-coded inline ``inputs = {...}`` fixture — T-tranche-3 remaining risk #2)

This module is the fourth place that *replaces* #3 and that tests and
documentation now read from. If you add / rename / repurpose an adapter, the
canonical spec lives here, and the contract tests will fail loudly if any
spec drifts out of sync with the registry.

Vocabulary
==========
- **tool_name** : the registry key, e.g. ``"cad.generate_part"``.
- **domain**    : the prefix before the first dot, e.g. ``"cad"``.
- **canonical sample input** : a minimal dict that, when passed to
  ``ToolRegistry.call(tool_name, sample)``, is accepted by the handler
  without an ``error`` key in the result. The sample is *minimum viable* —
  not exhaustive of all fields the adapter could accept.
- **required_keys** : keys that the handler's closure or the underlying
  adapter actively reads via ``params.get(...)``. Absence does not always
  raise (most handlers tolerate missing keys), but tests rely on these
  being present to exercise the happy path.

Invariants
==========
1. Every entry in ``EXPECTED_DEFAULT_REAL_TOOLS`` (from
   ``registry_contract.py``) has exactly one entry in ``ADAPTER_INPUT_SPECS``.
2. No entry exists in ``ADAPTER_INPUT_SPECS`` that is not in the registry.
3. Each canonical sample input, when passed through the live default
   registry, results in ``status="executed"`` (no ``error`` key).
4. Each spec's ``required_keys`` is a subset of its canonical sample's
   top-level keys (or of the top-level shape inferred from the sample if
   the sample is a list).

The first three invariants are enforced by
``tests/unit/test_adapter_inputs.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# AdapterInputSpec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdapterInputSpec:
    """Canonical per-tool input spec.

    Fields
    ------
    tool_name       : registry key
    domain          : prefix before the first dot (for per-domain sanity)
    required_keys   : keys the handler actually reads
                      (empty tuple if the handler is permissive on shape)
    canonical_sample: minimum viable input that the handler accepts
                      without returning an ``error`` dict
    docstring_shape : one-line human description of the expected shape
    """
    tool_name: str
    domain: str
    required_keys: tuple[str, ...]
    canonical_sample: Any
    docstring_shape: str

    def __post_init__(self) -> None:
        # Sanity: domain must equal the prefix of tool_name.
        prefix = self.tool_name.split(".", 1)[0] if "." in self.tool_name else self.tool_name
        if self.domain != prefix:
            raise ValueError(
                f"AdapterInputSpec domain mismatch: tool_name={self.tool_name!r}, "
                f"domain={self.domain!r}, expected prefix={prefix!r}"
            )


# ---------------------------------------------------------------------------
# The 14 canonical specs (single source of truth)
# ---------------------------------------------------------------------------
#
# Each spec MUST be kept in sync with:
#   - the handler closure in ``tools/registry.py::create_default_registry()``
#   - the underlying adapter function in ``tools/adapters/``
#
# When the handler changes shape, update the spec here first, then the
# handler / adapter docstring, then re-run the contract tests.

ADAPTER_INPUT_SPECS: dict[str, AdapterInputSpec] = {

    # ---- Animation (3) ---------------------------------------------------
    "animation.check_continuity": AdapterInputSpec(
        tool_name="animation.check_continuity",
        domain="animation",
        required_keys=(),
        canonical_sample=[
            {"shot_id": "s1", "duration_frames": 24,
             "camera": {"framing": "wide", "lens_mm": 35}},
        ],
        docstring_shape="list of shot dicts (or a single-shot dict wrapped) "
                        "for the continuity checker",
    ),
    "animation.render_preview": AdapterInputSpec(
        tool_name="animation.render_preview",
        domain="animation",
        required_keys=("duration_frames", "camera"),
        canonical_sample={
            "duration_frames": 24,
            "camera": {"framing": "medium", "lens_mm": 50},
        },
        docstring_shape="shot dict with duration_frames + camera metadata",
    ),
    "animation.solve_shot": AdapterInputSpec(
        tool_name="animation.solve_shot",
        domain="animation",
        required_keys=("framing", "mood"),
        canonical_sample={"framing": "medium", "mood": "neutral"},
        docstring_shape="partial shot-parse slots: {framing, mood, "
                        "[emotion_hint], [speed]}",
    ),

    # ---- Builder (3) -----------------------------------------------------
    "builder.export": AdapterInputSpec(
        tool_name="builder.export",
        domain="builder",
        required_keys=("floor_plans",),
        canonical_sample={"floor_plans": [{"floor": 1, "rooms": []}]},
        docstring_shape="builder.generate_plan result wrapper "
                        "({floor_plans: [...], metadata: {...}})",
    ),
    "builder.generate_plan": AdapterInputSpec(
        tool_name="builder.generate_plan",
        domain="builder",
        required_keys=("floors", "spaces"),
        canonical_sample={
            "floors": 1,
            "spaces": [
                {"type": "living_room", "count": 1},
                {"type": "bedroom", "count": 1},
                {"type": "bathroom", "count": 1},
            ],
        },
        docstring_shape="requirement-parse slots: "
                        "{project_type, floors, spaces, preferences, constraints}",
    ),
    "builder.validate": AdapterInputSpec(
        tool_name="builder.validate",
        domain="builder",
        required_keys=("floor_plans",),
        canonical_sample={"floor_plans": [{"floor": 1, "rooms": []}], "metadata": {}},
        docstring_shape="builder.generate_plan result",
    ),

    # ---- CAD (5) ---------------------------------------------------------
    "cad.generate_part": AdapterInputSpec(
        tool_name="cad.generate_part",
        domain="cad",
        required_keys=(),
        canonical_sample={"name": "bracket"},
        docstring_shape="constraint-parse slots; permissive — the adapter "
                        "fills defaults when absent",
    ),
    "cad.solve_assembly": AdapterInputSpec(
        tool_name="cad.solve_assembly",
        domain="cad",
        required_keys=("parts",),
        canonical_sample={"parts": [], "metadata": {}},
        docstring_shape="cad.generate_part result ({parts, interfaces, metadata})",
    ),
    "cad.route_wiring": AdapterInputSpec(
        tool_name="cad.route_wiring",
        domain="cad",
        required_keys=("parts", "interfaces"),
        canonical_sample={"parts": [], "interfaces": {"electrical": []}, "metadata": {}},
        docstring_shape="cad.generate_part result with electrical interfaces",
    ),
    "cad.route_drainage": AdapterInputSpec(
        tool_name="cad.route_drainage",
        domain="cad",
        required_keys=("parts", "interfaces"),
        canonical_sample={"parts": [], "interfaces": {"drainage": []}, "metadata": {}},
        docstring_shape="cad.generate_part result with drainage interfaces",
    ),
    "cad.validate_geometry": AdapterInputSpec(
        tool_name="cad.validate_geometry",
        domain="cad",
        required_keys=("parts", "interfaces"),
        canonical_sample={"parts": [], "interfaces": {}, "metadata": {}},
        docstring_shape="cad.generate_part result (optionally with "
                        "assembly/wiring/drainage via keyword routing)",
    ),

    # ---- Minecraft (3) ---------------------------------------------------
    "minecraft.compile_archetype": AdapterInputSpec(
        tool_name="minecraft.compile_archetype",
        domain="minecraft",
        required_keys=("target_anchor", "operations"),
        canonical_sample={
            "target_anchor": {"anchor_type": "facade"},
            "operations": [],
            "preserve": [],
        },
        docstring_shape="edit-parse slots: {target_anchor, operations, preserve, [scope]}",
    ),
    "minecraft.place_blocks": AdapterInputSpec(
        tool_name="minecraft.place_blocks",
        domain="minecraft",
        required_keys=("blocks",),
        canonical_sample={"blocks": []},
        docstring_shape="compile_archetype result OR {blocks: [...]} — handler "
                        "falls through to compile if blocks absent",
    ),
    "minecraft.validate_palette": AdapterInputSpec(
        tool_name="minecraft.validate_palette",
        domain="minecraft",
        required_keys=(),
        canonical_sample={"blocks": [], "metadata": {}},
        docstring_shape="minecraft.compile_archetype result (validator is "
                        "permissive on missing keys)",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class AdapterInputSpecError(AssertionError):
    """Raised when the adapter-input spec table is out of sync with the
    default registry or is internally inconsistent."""


def get_spec(tool_name: str) -> AdapterInputSpec:
    """Return the spec for ``tool_name`` or raise ``KeyError``."""
    try:
        return ADAPTER_INPUT_SPECS[tool_name]
    except KeyError as e:
        raise KeyError(
            f"no AdapterInputSpec for {tool_name!r}; "
            f"known tools: {sorted(ADAPTER_INPUT_SPECS.keys())}"
        ) from e


def sample_inputs_dict() -> dict[str, Any]:
    """Return the canonical inputs as a flat ``{tool_name: sample}`` dict.

    This is what legacy tests used to hard-code inline. Those tests now call
    this function instead so they read from one authoritative place.
    """
    return {name: spec.canonical_sample for name, spec in ADAPTER_INPUT_SPECS.items()}


def verify_spec_table_matches_registry(reg) -> None:
    """Assert that ``reg``'s real-tool set equals ``ADAPTER_INPUT_SPECS`` keys.

    Raises ``AdapterInputSpecError`` if:
      - the registry has a real tool with no spec (spec table is missing an entry)
      - the spec table has a name that is not a registered real tool
        (spec table has an orphan)

    This catches the "someone added a tool but forgot to add its input spec"
    drift class *and* the reverse "someone removed a tool but left its spec"
    drift class.
    """
    real = set(reg.list_real_tools())
    specs = set(ADAPTER_INPUT_SPECS.keys())
    missing = sorted(real - specs)
    orphan = sorted(specs - real)
    if missing or orphan:
        raise AdapterInputSpecError(
            f"adapter input spec drift:\n"
            f"  real tools without a spec : {missing}\n"
            f"  specs without a real tool : {orphan}"
        )


def verify_sample_inputs_exercise_registry(reg) -> None:
    """Assert that every canonical sample actually flows through the registry
    without landing on the error path.

    Raises ``AdapterInputSpecError`` listing every tool whose canonical sample
    produced an ``error`` dict or a ``status`` that is not ``"executed"``.
    """
    verify_spec_table_matches_registry(reg)   # pre-condition
    failures: list[str] = []
    for name, spec in ADAPTER_INPUT_SPECS.items():
        result = reg.call(name, spec.canonical_sample)
        if not isinstance(result, dict):
            failures.append(f"{name}: result is not a dict ({type(result).__name__})")
            continue
        if "error" in result:
            failures.append(f"{name}: canonical sample hit error path → {result['error']!r}")
            continue
        if result.get("status") != "executed":
            failures.append(
                f"{name}: expected status='executed', got {result.get('status')!r}"
            )
    if failures:
        raise AdapterInputSpecError(
            "canonical sample inputs do not exercise the registry cleanly:\n  - "
            + "\n  - ".join(failures)
        )


def specs_by_domain() -> dict[str, list[AdapterInputSpec]]:
    """Group specs by their ``domain`` field."""
    out: dict[str, list[AdapterInputSpec]] = {}
    for spec in ADAPTER_INPUT_SPECS.values():
        out.setdefault(spec.domain, []).append(spec)
    return out
