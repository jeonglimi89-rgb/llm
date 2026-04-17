"""
test_dispatcher_failure_exit_inventory.py — static inventory drift-prevention
for Dispatcher failure exit sites and their fallback explainability merge.

Background
==========
T-tranche-10 (2026-04-09) wired ``_merge_fallback_wait_into_retry_decision``
into two of the dispatcher's failure exit sites (CircuitOpenError and the
``parsed is None`` schema hard-fail branch). T-tranche-11 (2026-04-09) added
the third — the ``OverloadError`` queue-shed branch in ``Dispatcher.dispatch``
— to close the coverage asymmetry against the scheduler merge, which is
applied on every exit site.

This file is the **static inventory drift layer**. It parses
``src/app/orchestration/dispatcher.py`` via ``ast`` and enforces:

1. The exact set of ``return TaskResult(...)`` sites inside the ``Dispatcher``
   class matches the pinned inventory (count + per-status counts). If a new
   ``return TaskResult`` is added or an existing one removed, this test
   fails loud before the runtime layer even runs.

2. Every return site whose ``status=`` keyword maps onto a **failure**
   ``TaskStatus`` (``ERROR``, ``SHED``, ``TIMEOUT``) lives inside a function
   whose body references both ``record_wait_decision`` and
   ``_merge_fallback_wait_into_retry_decision``. This guarantees that any
   new failure branch added to the dispatcher carries the fallback
   explainability merge (because the function body containing the return
   must have both references, tested statically).

3. Success returns (``DONE``) are exempt from the merge requirement — the
   success path does not need a fallback explainability slot because no
   fallback wait conceptually applied there.

The test reads ``TaskStatus`` from ``src.app.core.enums`` directly, so
status renames in the enum cascade into updated expectations here.

Role separation
===============
- This file = **static inventory protection** (parses source, no runtime).
- ``tests/integration/test_artifact_explainability.py`` = **runtime
  explainability protection** (drives run_export / Dispatcher and reads
  the on-disk JSON artifact).

The two layers are complementary: the static layer catches *missing
wiring at the source level* before the runtime layer has a chance to
silently pass (e.g., if a new failure branch is added without a
fallback merge, the static layer fails immediately; the runtime layer
would only catch it if a dedicated runtime test for that new branch
happens to exist).
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.app.core.enums import TaskStatus


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DISPATCHER_PATH = REPO_ROOT / "src" / "app" / "orchestration" / "dispatcher.py"


# ---------------------------------------------------------------------------
# Pinned inventory — T-tranche-11 baseline (2026-04-09)
# ---------------------------------------------------------------------------

# Failure statuses as defined in src.app.core.enums.TaskStatus. Computed at
# import time so enum renames cascade immediately. The set is intentionally
# *closed* — adding a new failure state to the enum must also land here in
# lockstep (and in the dispatcher wiring).
FAILURE_STATUSES = frozenset({
    TaskStatus.ERROR.name,
    TaskStatus.SHED.name,
    TaskStatus.TIMEOUT.name,
})

# Expected number of ``return TaskResult(...)`` sites inside the Dispatcher
# class, pinned by status name. If a new return site is added the test below
# fails immediately with a diff — update this table in lockstep with the
# production change and the fallback merge wiring.
EXPECTED_RETURN_STATUS_COUNTS: dict[str, int] = {
    "ERROR": 2,     # _execute: CircuitOpen + parsed-is-None
    "SHED": 1,      # dispatch: OverloadError
    "DONE": 1,      # _execute: success path
}
# Sum = total return TaskResult(...) sites in Dispatcher (4 right now).
EXPECTED_TOTAL_RETURNS = sum(EXPECTED_RETURN_STATUS_COUNTS.values())

# Names that count as a fallback-explainability reference inside a function
# body. A failure branch must contain *both* the record call and the merge
# helper call (mere presence of either one alone is not enough).
FALLBACK_RECORD_NAME = "record_wait_decision"
FALLBACK_MERGE_NAME = "_merge_fallback_wait_into_retry_decision"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _load_dispatcher_ast() -> ast.Module:
    return ast.parse(DISPATCHER_PATH.read_text(encoding="utf-8"))


def _find_dispatcher_class(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Dispatcher":
            return node
    raise AssertionError("Dispatcher class not found in dispatcher.py")


def _iter_functions(cls: ast.ClassDef):
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _extract_status_name(call: ast.Call) -> str | None:
    """From ``TaskResult(status=TaskStatus.ERROR, ...)`` return ``"ERROR"``.

    Returns None if the call is not ``TaskResult(...)`` or if ``status=`` is
    missing / not a ``TaskStatus.<NAME>`` attribute reference.
    """
    # Must be a call to ``TaskResult`` (Name node or attribute ending in TaskResult).
    func = call.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "TaskResult":
        return None
    # Find the status= kwarg.
    for kw in call.keywords:
        if kw.arg == "status":
            v = kw.value
            # Expect TaskStatus.<NAME>
            if (
                isinstance(v, ast.Attribute)
                and isinstance(v.value, ast.Name)
                and v.value.id == "TaskStatus"
            ):
                return v.attr
            return None
    return None


def _collect_return_task_result_sites(fn: ast.FunctionDef) -> list[str]:
    """Return the list of status names for every ``return TaskResult(...)``
    site inside ``fn`` (nested inner functions are included because the
    dispatcher has a ``handler`` closure that may contain one)."""
    out: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        status_name = _extract_status_name(node.value)
        if status_name is not None:
            out.append(status_name)
    return out


def _function_body_refers_to(fn: ast.FunctionDef, names: set[str]) -> set[str]:
    """Return the subset of ``names`` that appear as either a ``Name`` node
    or an ``Attribute`` node's ``attr`` inside ``fn``'s body. Used to
    check 'does this function body reference both record_wait_decision and
    _merge_fallback_wait_into_retry_decision somewhere'."""
    seen: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in names:
            seen.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            seen.add(node.attr)
        if seen == names:
            return seen
    return seen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dispatcher_return_task_result_inventory_matches_pinned_counts():
    """The exact number of ``return TaskResult(...)`` sites inside the
    Dispatcher class, broken down by ``status=TaskStatus.<NAME>``, must
    match ``EXPECTED_RETURN_STATUS_COUNTS``. If you add or remove a return
    site, update that dict in lockstep."""
    print("  [1] Dispatcher return TaskResult inventory matches pinned counts")
    cls = _find_dispatcher_class(_load_dispatcher_ast())
    observed: dict[str, int] = {}
    for fn in _iter_functions(cls):
        for status_name in _collect_return_task_result_sites(fn):
            observed[status_name] = observed.get(status_name, 0) + 1

    total = sum(observed.values())
    assert total == EXPECTED_TOTAL_RETURNS, (
        f"Dispatcher return TaskResult count drift: got {total}, "
        f"expected {EXPECTED_TOTAL_RETURNS}. Observed: {observed}"
    )
    assert observed == EXPECTED_RETURN_STATUS_COUNTS, (
        f"Dispatcher return TaskResult per-status drift:\n"
        f"  observed: {observed}\n"
        f"  expected: {EXPECTED_RETURN_STATUS_COUNTS}"
    )
    print(f"    OK: {observed}")


def test_every_failure_return_site_has_fallback_merge_in_enclosing_function():
    """For every ``return TaskResult(status=<failure>)`` site inside the
    Dispatcher class, the *enclosing function body* must reference both
    ``record_wait_decision`` and ``_merge_fallback_wait_into_retry_decision``.

    This is the coverage-uniformity guard: adding a new failure branch
    without wiring the fallback merge fails this test immediately, even
    if the new branch happens to be never exercised by a runtime test.
    """
    print("  [2] every failure return site has fallback merge in its enclosing function")
    cls = _find_dispatcher_class(_load_dispatcher_ast())
    required = {FALLBACK_RECORD_NAME, FALLBACK_MERGE_NAME}
    violations: list[str] = []
    functions_with_failure_returns: dict[str, list[str]] = {}
    for fn in _iter_functions(cls):
        statuses = _collect_return_task_result_sites(fn)
        failure_statuses = [s for s in statuses if s in FAILURE_STATUSES]
        if not failure_statuses:
            continue
        functions_with_failure_returns[fn.name] = failure_statuses
        present = _function_body_refers_to(fn, required)
        missing = required - present
        if missing:
            violations.append(
                f"{fn.name}: failure returns {failure_statuses} but missing "
                f"{sorted(missing)}"
            )
    assert not violations, (
        "Dispatcher failure exit sites missing fallback explainability "
        "wiring:\n  " + "\n  ".join(violations)
    )
    # Defense-in-depth: we must have actually *seen* at least one failure
    # return site. If this test silently passed because no failure returns
    # were found at all, the drift layer would be useless.
    assert functions_with_failure_returns, (
        "No failure return sites detected in Dispatcher — inventory scan "
        "is broken or the class changed shape in an unexpected way."
    )
    print(f"    OK: failure returns covered: {functions_with_failure_returns}")


def test_success_path_does_not_require_fallback_merge():
    """Sanity / scope check: the ``DONE`` success return is the only
    non-failure site in the Dispatcher. It is **exempt** from the
    fallback-merge requirement (no fallback wait conceptually applies
    on success). If a new success-status return sneaks in without a
    DONE label, this test fails so the distinction stays explicit.
    """
    print("  [3] success return site exists and carries TaskStatus.DONE")
    cls = _find_dispatcher_class(_load_dispatcher_ast())
    done_count = 0
    for fn in _iter_functions(cls):
        for status_name in _collect_return_task_result_sites(fn):
            if status_name == "DONE":
                done_count += 1
    assert done_count == EXPECTED_RETURN_STATUS_COUNTS["DONE"], (
        f"Dispatcher DONE return count drift: got {done_count}, "
        f"expected {EXPECTED_RETURN_STATUS_COUNTS['DONE']}"
    )
    print(f"    OK: DONE count = {done_count}")


def test_failure_statuses_set_matches_enum_constants():
    """Drift-prevention for ``FAILURE_STATUSES``: the set above must only
    contain names that actually exist on ``TaskStatus``. If someone
    renames ``ERROR`` / ``SHED`` / ``TIMEOUT`` in the enum, this test
    fails with a clear message pointing at the stale literal."""
    print("  [4] FAILURE_STATUSES names all exist on TaskStatus")
    valid_names = {member.name for member in TaskStatus}
    unknown = FAILURE_STATUSES - valid_names
    assert not unknown, (
        f"FAILURE_STATUSES references names not on TaskStatus: {sorted(unknown)}. "
        f"Valid TaskStatus names: {sorted(valid_names)}"
    )
    # Defense-in-depth: at least one known failure status must be present
    # (ERROR always exists); otherwise the set is degenerate and the test
    # above would also pass silently on any drift.
    assert "ERROR" in FAILURE_STATUSES, "ERROR must always be in FAILURE_STATUSES"
    print(f"    OK: {sorted(FAILURE_STATUSES)} all valid")


TESTS = [
    test_dispatcher_return_task_result_inventory_matches_pinned_counts,
    test_every_failure_return_site_has_fallback_merge_in_enclosing_function,
    test_success_path_does_not_require_fallback_merge,
    test_failure_statuses_set_matches_enum_constants,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Dispatcher failure exit inventory tests (T-tranche-11)")
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
