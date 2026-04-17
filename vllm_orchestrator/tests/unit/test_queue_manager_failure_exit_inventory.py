"""
test_queue_manager_failure_exit_inventory.py — static inventory drift-prevention
for QueueManager failure exit sites.

Background
==========
T-tranche-11 (2026-04-09) pinned the Dispatcher class's
``return TaskResult(...)`` inventory and enforced that every failure
return site carry a fallback explainability merge in its enclosing
function. But ``QueueManager.submit`` also has a failure return site:
its catch-all ``except Exception`` branch (``queue_manager.py`` lines
61-70) intercepts anything the handler (i.e. ``Dispatcher._execute``)
lets escape and returns its own ``TaskResult(status=TaskStatus.ERROR,
...)`` with ``retry_decision=None``. That result flows back up to
``Dispatcher.dispatch`` as the return value of ``queue.submit(...)``
and, before T-tranche-12, was passed through verbatim — bypassing
``_execute``'s own failure merge entirely.

T-tranche-12 (2026-04-09) closes that gap with **two layers**:

1. **Production wrapper** in ``Dispatcher.dispatch``: a post-hoc check
   that if the queue returned a failure ``TaskResult`` with
   ``retry_decision is None`` and the dispatcher has a ``fallback``,
   the fallback ``WaitDecision`` is recorded and merged into the
   returned ``TaskResult.retry_decision``.

2. **Static inventory drift layer** — this file. Unlike the
   ``Dispatcher`` counterpart, we do NOT require QueueManager's own
   failure returns to reference ``record_wait_decision`` or the merge
   helper — queue_manager is an execution primitive and should not
   know about fallback semantics. Instead we only pin the *count and
   shape* of queue_manager's return sites so that a new failure
   return (or a rename of the exception branch) fails this test
   immediately, prompting the author to verify whether
   ``Dispatcher.dispatch``'s post-hoc wrapper still covers the new
   shape. The runtime half of T-tranche-12's guard lives in
   ``tests/integration/test_artifact_explainability.py``.

Role separation
===============
- ``test_dispatcher_failure_exit_inventory.py`` = Dispatcher AST drift +
  per-branch merge requirement (same-function enforcement).
- ``test_queue_manager_failure_exit_inventory.py`` (this file) = pure
  inventory drift for the queue layer, no merge requirement (by
  design — queue is a lower-level primitive).
- ``tests/integration/test_artifact_explainability.py`` = runtime proof
  that the dispatcher post-hoc wrapper actually fires on a handler
  exception and enriches the resulting TaskResult.
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.app.core.enums import TaskStatus


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QUEUE_MANAGER_PATH = REPO_ROOT / "src" / "app" / "execution" / "queue_manager.py"


# ---------------------------------------------------------------------------
# Pinned inventory — T-tranche-12 baseline (2026-04-09)
# ---------------------------------------------------------------------------

# Queue manager currently has exactly 1 ``return TaskResult(...)`` site,
# in the ``submit`` method's exception catch-all branch. It carries
# ``status=TaskStatus.ERROR``. The ``OverloadError`` branch raises
# rather than returning a TaskResult, so it is not in this inventory.
EXPECTED_QUEUE_RETURN_STATUS_COUNTS: dict[str, int] = {
    "ERROR": 1,     # submit(): except Exception → TaskResult(status=ERROR)
}
EXPECTED_QUEUE_TOTAL_RETURNS = sum(EXPECTED_QUEUE_RETURN_STATUS_COUNTS.values())

# Failure status set (shared with the Dispatcher inventory file). Computed
# from the enum so a rename cascades. Used here only to confirm that
# every return site in queue_manager *is* a failure (if a future refactor
# ever adds a success return from queue_manager, this test will surface
# that unexpected shape change).
FAILURE_STATUSES = frozenset({
    TaskStatus.ERROR.name,
    TaskStatus.SHED.name,
    TaskStatus.TIMEOUT.name,
})


# ---------------------------------------------------------------------------
# AST helpers — kept local (NOT shared with the Dispatcher inventory file)
# so that refactoring one scanner does not silently break the other.
# ---------------------------------------------------------------------------

def _load_queue_manager_ast() -> ast.Module:
    return ast.parse(QUEUE_MANAGER_PATH.read_text(encoding="utf-8"))


def _find_queue_manager_class(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QueueManager":
            return node
    raise AssertionError("QueueManager class not found in queue_manager.py")


def _iter_functions(cls: ast.ClassDef):
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _extract_status_name(call: ast.Call) -> str | None:
    """From ``TaskResult(status=TaskStatus.ERROR, ...)`` return ``"ERROR"``."""
    func = call.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "TaskResult":
        return None
    for kw in call.keywords:
        if kw.arg == "status":
            v = kw.value
            if (
                isinstance(v, ast.Attribute)
                and isinstance(v.value, ast.Name)
                and v.value.id == "TaskStatus"
            ):
                return v.attr
            return None
    return None


def _collect_return_task_result_sites(fn: ast.FunctionDef) -> list[str]:
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_queue_manager_return_task_result_inventory_matches_pinned_counts():
    """The exact number of ``return TaskResult(...)`` sites inside the
    ``QueueManager`` class, broken down by ``status=TaskStatus.<NAME>``,
    must match ``EXPECTED_QUEUE_RETURN_STATUS_COUNTS``. If you add or
    remove a return site (or rename the exception branch), update that
    dict in lockstep and confirm the Dispatcher post-hoc wrapper still
    covers the new shape."""
    print("  [1] QueueManager return TaskResult inventory matches pinned counts")
    cls = _find_queue_manager_class(_load_queue_manager_ast())
    observed: dict[str, int] = {}
    for fn in _iter_functions(cls):
        for status_name in _collect_return_task_result_sites(fn):
            observed[status_name] = observed.get(status_name, 0) + 1

    total = sum(observed.values())
    assert total == EXPECTED_QUEUE_TOTAL_RETURNS, (
        f"QueueManager return TaskResult count drift: got {total}, "
        f"expected {EXPECTED_QUEUE_TOTAL_RETURNS}. Observed: {observed}"
    )
    assert observed == EXPECTED_QUEUE_RETURN_STATUS_COUNTS, (
        f"QueueManager return TaskResult per-status drift:\n"
        f"  observed: {observed}\n"
        f"  expected: {EXPECTED_QUEUE_RETURN_STATUS_COUNTS}"
    )
    print(f"    OK: {observed}")


def test_every_queue_manager_return_site_is_a_failure_status():
    """Defensive check: the QueueManager should only return a TaskResult
    from its failure catch-all branch; any success-path return from the
    queue layer would be a surprising shape change that the Dispatcher
    post-hoc wrapper is NOT designed for. If this invariant ever gets
    violated the author should think carefully about whether the
    dispatcher's wrapper needs to be updated."""
    print("  [2] every QueueManager return TaskResult site has a failure status")
    cls = _find_queue_manager_class(_load_queue_manager_ast())
    non_failure: list[str] = []
    total_seen = 0
    for fn in _iter_functions(cls):
        for status_name in _collect_return_task_result_sites(fn):
            total_seen += 1
            if status_name not in FAILURE_STATUSES:
                non_failure.append(f"{fn.name}: status={status_name}")
    assert total_seen >= 1, (
        "QueueManager return inventory scanner found zero TaskResult sites — "
        "either the class was renamed or the scanner is broken."
    )
    assert not non_failure, (
        f"QueueManager has return TaskResult sites with non-failure status: "
        f"{non_failure}. If this is intentional, update the Dispatcher "
        f"post-hoc wrapper AND this test in lockstep."
    )
    print(f"    OK: all {total_seen} return sites are failure statuses")


def test_queue_manager_has_no_fallback_merge_by_design():
    """Architectural invariant: the QueueManager class body must NOT
    reference ``_merge_fallback_wait_into_retry_decision`` or
    ``record_wait_decision``. Queue manager is a lower-level execution
    primitive; fallback concerns live in the dispatcher (post-hoc
    wrapper) and in DegradedModeHandler, not in the queue layer.

    Negative assertion — catches the day someone accidentally couples
    the queue layer to the fallback handler.
    """
    print("  [3] QueueManager is architecturally decoupled from fallback merge")
    cls = _find_queue_manager_class(_load_queue_manager_ast())
    forbidden = {
        "_merge_fallback_wait_into_retry_decision",
        "record_wait_decision",
    }
    found: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Name) and node.id in forbidden:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in forbidden:
            found.add(node.attr)
    assert not found, (
        f"QueueManager leaked fallback-merge coupling: {sorted(found)}. "
        f"Queue should stay decoupled; fallback enrichment lives in "
        f"Dispatcher.dispatch()'s post-hoc wrapper."
    )
    print("    OK: no fallback-merge coupling in queue layer")


TESTS = [
    test_queue_manager_return_task_result_inventory_matches_pinned_counts,
    test_every_queue_manager_return_site_is_a_failure_status,
    test_queue_manager_has_no_fallback_merge_by_design,
]


if __name__ == "__main__":
    print("=" * 60)
    print("QueueManager failure exit inventory tests (T-tranche-12)")
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
