"""
test_gate_boundary.py — drift-prevention for the deterministic gate boundary.

Background
==========
T-tranche-6 (2026-04-08) split the test suite into three explicit slices
via pytest markers:

  - default (no marker)  — the deterministic gate, runs on `pytest tests/`
  - ``infra``            — requires a live external dependency (LLM HTTP
                           server, FastAPI subprocess, real network)
  - ``load``             — deterministic FakeLLM-backed throughput / soak

This file is the drift-prevention layer. If someone adds a new test file
that silently touches infra (e.g. adds a new ``VLLMHttpAdapter(<real_ip>)``
at module scope) or moves a deterministic load test into the main gate
without a marker, these tests catch it before it pollutes the gate.

The tests here are deterministic and fast — they do collection-level
inspection via the filesystem and AST, not pytest internals. They run as
part of the default gate itself, so marker drift fails the gate loudly.
"""
from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = REPO_ROOT / "tests"


# ---------------------------------------------------------------------------
# Canonical inventory — pinned. When you add or rename a test file in one
# of these categories, update the corresponding set here in lockstep.
# ---------------------------------------------------------------------------

EXPECTED_INFRA_FILES: frozenset[str] = frozenset({
    # 1) HTTP-server bootstrap e2e — spawns FastAPI subprocess.
    "tests/integration/test_http_e2e.py",
    # 2) Real LLM server load tests (192.168.57.105:8000).
    "tests/load/test_real_orchestrator.py",
    "tests/load/test_real_server_full.py",
})

EXPECTED_LOAD_FILES: frozenset[str] = frozenset({
    # FakeLLM-backed, deterministic, long-running (load/soak/throughput).
    "tests/load/test_overload_rejection.py",
    "tests/load/test_serial_40_case.py",
    "tests/load/test_soak_concurrency.py",
    "tests/load/test_timeout_recovery.py",
})

# Files with a *function-level* @pytest.mark.infra (rather than file-level
# pytestmark). These are otherwise-deterministic files that carry one
# single infra test hooked into a live LLM server.
EXPECTED_MIXED_INFRA_FILES: frozenset[str] = frozenset({
    "tests/integration/test_engine_e2e.py",       # test_llm_to_compiler_e2e
    "tests/integration/test_builder_engine_e2e.py",  # test_llm_to_planner_e2e
})


# ---------------------------------------------------------------------------
# AST helpers — pinned so the inspection itself is deterministic and does
# not import heavy test modules.
# ---------------------------------------------------------------------------

def _read_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _has_file_level_pytestmark(tree: ast.Module, marker_name: str) -> bool:
    """True if the module defines ``pytestmark = pytest.mark.<marker_name>``
    (either as a single attribute or inside a list)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            continue
        value = node.value
        # Normalize list / tuple / single to a flat list of attribute expressions.
        items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        for item in items:
            # Looking for pytest.mark.<marker_name>
            if (
                isinstance(item, ast.Attribute)
                and isinstance(item.value, ast.Attribute)
                and isinstance(item.value.value, ast.Name)
                and item.value.value.id == "pytest"
                and item.value.attr == "mark"
                and item.attr == marker_name
            ):
                return True
    return False


def _has_function_level_decorator(tree: ast.Module, marker_name: str) -> bool:
    """True if any function inside ``tree`` is decorated with
    ``@pytest.mark.<marker_name>``."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # @pytest.mark.infra
            if (
                isinstance(dec, ast.Attribute)
                and isinstance(dec.value, ast.Attribute)
                and isinstance(dec.value.value, ast.Name)
                and dec.value.value.id == "pytest"
                and dec.value.attr == "mark"
                and dec.attr == marker_name
            ):
                return True
    return False


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Test 1: every EXPECTED_INFRA_FILES has file-level pytestmark = pytest.mark.infra
# ---------------------------------------------------------------------------

def test_expected_infra_files_carry_file_level_infra_marker():
    print("  [1] file-level pytestmark = pytest.mark.infra is present on all expected infra files")
    missing = []
    for rel in sorted(EXPECTED_INFRA_FILES):
        path = REPO_ROOT / rel
        assert path.exists(), f"expected infra file missing: {rel}"
        tree = _read_ast(path)
        if not _has_file_level_pytestmark(tree, "infra"):
            missing.append(rel)
    assert missing == [], (
        f"these files are in EXPECTED_INFRA_FILES but lack file-level "
        f"pytestmark = pytest.mark.infra: {missing}"
    )
    print(f"    OK: {len(EXPECTED_INFRA_FILES)} files")


# ---------------------------------------------------------------------------
# Test 2: every EXPECTED_LOAD_FILES has file-level pytestmark = pytest.mark.load
# ---------------------------------------------------------------------------

def test_expected_load_files_carry_file_level_load_marker():
    print("  [2] file-level pytestmark = pytest.mark.load is present on all expected load files")
    missing = []
    for rel in sorted(EXPECTED_LOAD_FILES):
        path = REPO_ROOT / rel
        assert path.exists(), f"expected load file missing: {rel}"
        tree = _read_ast(path)
        if not _has_file_level_pytestmark(tree, "load"):
            missing.append(rel)
    assert missing == [], (
        f"these files are in EXPECTED_LOAD_FILES but lack file-level "
        f"pytestmark = pytest.mark.load: {missing}"
    )
    print(f"    OK: {len(EXPECTED_LOAD_FILES)} files")


# ---------------------------------------------------------------------------
# Test 3: the two mixed-files carry a function-level @pytest.mark.infra
# ---------------------------------------------------------------------------

def test_mixed_files_carry_function_level_infra_marker():
    print("  [3] function-level @pytest.mark.infra is present on mixed files")
    missing = []
    for rel in sorted(EXPECTED_MIXED_INFRA_FILES):
        path = REPO_ROOT / rel
        assert path.exists(), f"expected mixed-infra file missing: {rel}"
        tree = _read_ast(path)
        if not _has_function_level_decorator(tree, "infra"):
            missing.append(rel)
    assert missing == [], (
        f"these files are in EXPECTED_MIXED_INFRA_FILES but no function "
        f"inside them carries a @pytest.mark.infra decorator: {missing}"
    )
    print(f"    OK: {len(EXPECTED_MIXED_INFRA_FILES)} files")


# ---------------------------------------------------------------------------
# Test 4: no file inside tests/load/ is unmarked — every load file MUST
# carry either @pytest.mark.infra or @pytest.mark.load, so a future load
# test cannot silently leak into the default gate without a marker.
# ---------------------------------------------------------------------------

def test_every_load_file_is_marked():
    print("  [4] every file in tests/load/ has file-level infra or load marker")
    load_dir = TESTS_DIR / "load"
    unmarked = []
    for path in sorted(load_dir.glob("test_*.py")):
        tree = _read_ast(path)
        if not (
            _has_file_level_pytestmark(tree, "infra")
            or _has_file_level_pytestmark(tree, "load")
        ):
            unmarked.append(_rel(path))
    assert unmarked == [], (
        f"these files under tests/load/ have no file-level infra or load "
        f"marker and would silently leak into the default gate: {unmarked}"
    )
    print(f"    OK: every load file carries a marker")


# ---------------------------------------------------------------------------
# Test 5: the inventory sets are disjoint (no file is both infra and load)
# ---------------------------------------------------------------------------

def test_inventory_sets_are_disjoint():
    print("  [5] EXPECTED_INFRA_FILES ∩ EXPECTED_LOAD_FILES == ∅")
    overlap = EXPECTED_INFRA_FILES & EXPECTED_LOAD_FILES
    assert overlap == frozenset(), f"overlap detected: {overlap}"
    overlap2 = EXPECTED_INFRA_FILES & EXPECTED_MIXED_INFRA_FILES
    assert overlap2 == frozenset(), f"mixed/infra overlap: {overlap2}"
    overlap3 = EXPECTED_LOAD_FILES & EXPECTED_MIXED_INFRA_FILES
    assert overlap3 == frozenset(), f"mixed/load overlap: {overlap3}"
    print("    OK")


# ---------------------------------------------------------------------------
# Test 6: pytest.ini registers both markers (so -W error::PytestUnknownMarkWarning
# would not fire against us) and sets the default filter.
# ---------------------------------------------------------------------------

def test_pytest_ini_registers_both_markers_and_default_filter():
    print("  [6] pytest.ini registers 'infra' + 'load' markers and default filter")
    ini = REPO_ROOT / "pytest.ini"
    assert ini.exists(), "pytest.ini missing at repo root"
    text = ini.read_text(encoding="utf-8")
    assert "\n    infra:" in text, "pytest.ini does not register 'infra' marker"
    assert "\n    load:" in text, "pytest.ini does not register 'load' marker"
    assert 'addopts = -m "not infra and not load"' in text, (
        "pytest.ini does not set the default deselection filter"
    )
    print("    OK")


# ---------------------------------------------------------------------------
# Test 7: the gate docs file exists and mentions both markers.
# ---------------------------------------------------------------------------

def test_gate_docs_exists_and_mentions_both_markers():
    print("  [7] docs/testing_gate.md exists and documents both markers")
    doc = REPO_ROOT / "docs" / "testing_gate.md"
    assert doc.exists(), "docs/testing_gate.md missing"
    text = doc.read_text(encoding="utf-8")
    assert "infra" in text, "docs/testing_gate.md does not mention 'infra'"
    assert "load" in text, "docs/testing_gate.md does not mention 'load'"
    assert "deterministic gate" in text.lower(), (
        "docs/testing_gate.md does not define 'deterministic gate'"
    )
    print("    OK")


TESTS = [
    test_expected_infra_files_carry_file_level_infra_marker,
    test_expected_load_files_carry_file_level_load_marker,
    test_mixed_files_carry_function_level_infra_marker,
    test_every_load_file_is_marked,
    test_inventory_sets_are_disjoint,
    test_pytest_ini_registers_both_markers_and_default_filter,
    test_gate_docs_exists_and_mentions_both_markers,
]


if __name__ == "__main__":
    print("=" * 60)
    print("gate-boundary drift-prevention tests (T-tranche-6)")
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
