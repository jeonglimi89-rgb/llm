"""
test_api_routes.py — unit tests for API route functions.

Tests the route handler functions directly (no HTTP server, no FastAPI
TestClient) by calling ``health.init(...)`` / ``tasks.init(...)`` with
controlled fakes and then invoking the handler functions. This keeps
the tests in the default deterministic gate without any infra dependency.

Coverage
========
- ``health.live()`` — always returns alive
- ``health.ready()`` — healthy vs unhealthy vs circuit-open
- ``health.detail()`` — snapshot structure
- ``tasks.submit_task(body)`` — success dispatch + fallback cache
- ``tasks.submit_task(body)`` — ValidationError → HTTPException 400
- ``tasks.init / health.init`` — injection wiring
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.api.routes import health as health_mod
from src.app.api.routes import tasks as tasks_mod
from src.app.core.errors import ValidationError
from src.app.core.enums import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_health_registry(healthy: bool = True):
    m = MagicMock()
    m.is_system_healthy.return_value = healthy
    m.snapshot.return_value = {"components": {"llm": {"healthy": healthy}}}
    return m


def _mock_circuit(allow: bool = True):
    m = MagicMock()
    m.allow.return_value = allow
    m.snapshot.return_value = {"state": "closed" if allow else "open", "failures": 0}
    return m


def _mock_queue():
    m = MagicMock()
    m.snapshot.return_value = {"depth": 0, "running": 0}
    return m


class _FakeResult:
    def __init__(self, status="done", slots=None):
        self.status = status
        self.slots = slots
        self.task_type = "builder.requirement_parse"
    def to_dict(self):
        return {"status": self.status, "slots": self.slots}


def _mock_router(*, raises: Exception | None = None):
    m = MagicMock()
    if raises:
        m.resolve.side_effect = raises
    else:
        m.resolve.return_value = MagicMock()  # TaskSpec
    return m


def _mock_dispatcher(result: _FakeResult | None = None):
    m = MagicMock()
    m.dispatch.return_value = result or _FakeResult()
    return m


def _mock_fallback():
    m = MagicMock()
    return m


# ===========================================================================
# Health routes
# ===========================================================================

def test_health_live_returns_alive():
    print("  [1] health.live always returns alive")
    health_mod.init(None, None, None)
    assert health_mod.live() == {"status": "alive"}
    print("    OK")


def test_health_ready_when_all_healthy():
    print("  [2] health.ready returns ready when system healthy + circuit closed")
    health_mod.init(_mock_health_registry(True), _mock_queue(), _mock_circuit(True))
    assert health_mod.ready() == {"status": "ready"}
    print("    OK")


def test_health_ready_when_unhealthy():
    print("  [3] health.ready returns not_ready when unhealthy")
    health_mod.init(_mock_health_registry(False), _mock_queue(), _mock_circuit(True))
    result = health_mod.ready()
    assert result["status"] == "not_ready"
    assert "unhealthy" in result["reason"]
    print("    OK")


def test_health_ready_when_circuit_open():
    print("  [4] health.ready returns not_ready when circuit open")
    health_mod.init(_mock_health_registry(True), _mock_queue(), _mock_circuit(False))
    result = health_mod.ready()
    assert result["status"] == "not_ready"
    assert "circuit" in result["reason"]
    print("    OK")


def test_health_detail_returns_snapshots():
    print("  [5] health.detail returns health/queue/circuit snapshots")
    hr = _mock_health_registry()
    q = _mock_queue()
    cb = _mock_circuit()
    health_mod.init(hr, q, cb)
    result = health_mod.detail()
    assert "health" in result
    assert "queue" in result
    assert "circuit" in result
    hr.snapshot.assert_called_once()
    q.snapshot.assert_called_once()
    cb.snapshot.assert_called_once()
    print("    OK")


def test_health_detail_tolerates_none_globals():
    print("  [6] health.detail returns empty dicts when globals are None")
    health_mod.init(None, None, None)
    result = health_mod.detail()
    assert result == {"health": {}, "queue": {}, "circuit": {}}
    print("    OK")


# ===========================================================================
# Tasks routes
# ===========================================================================

def test_submit_task_success_dispatches_and_caches():
    print("  [7] tasks.submit_task success: dispatches + caches fallback")
    fake_result = _FakeResult(status="done", slots={"intent": "test"})
    router = _mock_router()
    dispatcher = _mock_dispatcher(fake_result)
    fallback = _mock_fallback()
    tasks_mod.init(router, dispatcher, fallback)

    body = {"domain": "builder", "task_name": "requirement_parse", "user_input": "test"}
    result = tasks_mod.submit_task(body)

    assert result["status"] == "done"
    router.resolve.assert_called_once()
    dispatcher.dispatch.assert_called_once()
    # Fallback cache should be updated on success with slots.
    fallback.cache_good_result.assert_called_once_with(
        "builder.requirement_parse", {"intent": "test"}
    )
    print("    OK")


def test_submit_task_no_cache_when_slots_none():
    print("  [8] tasks.submit_task: no fallback cache when slots is None")
    fake_result = _FakeResult(status="error", slots=None)
    router = _mock_router()
    dispatcher = _mock_dispatcher(fake_result)
    fallback = _mock_fallback()
    tasks_mod.init(router, dispatcher, fallback)

    body = {"domain": "builder", "task_name": "requirement_parse", "user_input": "test"}
    result = tasks_mod.submit_task(body)

    assert result["status"] == "error"
    fallback.cache_good_result.assert_not_called()
    print("    OK")


def test_submit_task_validation_error_raises_400():
    print("  [9] tasks.submit_task: ValidationError → HTTPException 400")
    router = _mock_router(raises=ValidationError("bad domain"))
    dispatcher = _mock_dispatcher()
    tasks_mod.init(router, dispatcher, None)

    body = {"domain": "invalid", "task_name": "x", "user_input": "y"}
    try:
        tasks_mod.submit_task(body)
        assert False, "should have raised HTTPException"
    except Exception as e:
        # FastAPI HTTPException
        assert hasattr(e, "status_code") and e.status_code == 400
        assert "bad domain" in str(e.detail)
    print("    OK")


def test_submit_task_builds_task_request_from_body():
    print("  [10] tasks.submit_task: body fields map to TaskRequest")
    router = _mock_router()
    dispatcher = _mock_dispatcher()
    tasks_mod.init(router, dispatcher, None)

    body = {
        "domain": "cad",
        "task_name": "constraint_parse",
        "user_input": "test input",
        "priority": "high",
        "session_id": "s1",
        "project_id": "p1",
        "context": {"k": "v"},
    }
    tasks_mod.submit_task(body)

    # Check the TaskRequest that was passed to router.resolve
    call_args = router.resolve.call_args
    req = call_args[0][0]
    assert req.domain == "cad"
    assert req.task_name == "constraint_parse"
    assert req.user_input == "test input"
    assert req.priority == "high"
    assert req.session_id == "s1"
    assert req.project_id == "p1"
    assert req.context == {"k": "v"}
    print("    OK")


def test_submit_task_no_fallback_when_fallback_is_none():
    print("  [11] tasks.submit_task: fallback=None doesn't crash on success")
    fake_result = _FakeResult(status="done", slots={"intent": "test"})
    router = _mock_router()
    dispatcher = _mock_dispatcher(fake_result)
    tasks_mod.init(router, dispatcher, None)  # no fallback

    body = {"domain": "builder", "task_name": "requirement_parse", "user_input": "t"}
    result = tasks_mod.submit_task(body)
    assert result["status"] == "done"
    print("    OK")


TESTS = [
    test_health_live_returns_alive,
    test_health_ready_when_all_healthy,
    test_health_ready_when_unhealthy,
    test_health_ready_when_circuit_open,
    test_health_detail_returns_snapshots,
    test_health_detail_tolerates_none_globals,
    test_submit_task_success_dispatches_and_caches,
    test_submit_task_no_cache_when_slots_none,
    test_submit_task_validation_error_raises_400,
    test_submit_task_builds_task_request_from_body,
    test_submit_task_no_fallback_when_fallback_is_none,
]


if __name__ == "__main__":
    print("=" * 60)
    print("API route unit tests")
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
