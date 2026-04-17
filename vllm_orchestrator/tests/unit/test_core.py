"""unit tests - core components"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.core.contracts import TaskRequest, TaskResult
from src.app.core.enums import TaskStatus, FallbackMode
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.observability.health_registry import HealthRegistry
from src.app.llm.response_parser import extract_json, repair_json, parse_llm_output
from src.app.llm.token_budget import trim_prompt, get_output_budget
from src.app.domain.registry import get_task_spec, list_enabled_tasks
from src.app.orchestration.router import Router
from src.app.fallback.degraded_modes import DegradedModeHandler
from src.app.tools.registry import create_default_registry
from src.app.settings import AppSettings


def test_task_request():
    r = TaskRequest(domain="builder", task_name="requirement_parse", user_input="2층 주택")
    assert r.task_type == "builder.requirement_parse"
    assert r.request_id.startswith("req_")
    print("  [OK] TaskRequest")


def test_circuit_breaker():
    cb = CircuitBreaker(fail_threshold=2, reset_timeout_s=0.1)
    assert cb.allow()
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert not cb.allow()  # open
    cb.record_success()
    assert cb.allow()  # closed
    print("  [OK] CircuitBreaker")


def test_health_registry():
    h = HealthRegistry(fail_threshold=2)
    h.register("llm")
    h.record_success("llm", 100)
    assert h.is_healthy("llm")
    h.record_failure("llm")
    h.record_failure("llm")
    assert not h.is_healthy("llm")
    snap = h.snapshot()
    assert snap["components"]["llm"]["consecutive_failures"] == 2
    print("  [OK] HealthRegistry")


def test_queue_manager():
    q = QueueManager(max_concurrency=1, max_depth=2)
    r = TaskRequest(domain="cad", task_name="test", user_input="x")
    result = q.submit(r, lambda req: TaskResult(request_id=req.request_id, task_id=req.task_id, task_type=req.task_type))
    assert result.status == "done"
    assert q.total_completed == 1
    print("  [OK] QueueManager")


def test_response_parser():
    # markdown fence
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == '{"a": 1}'
    # trailing comma
    assert repair_json('{"a":1,}') == '{"a":1}'
    # full pipeline
    parsed, logs = parse_llm_output('some text ```json\n{"key":"val"}\n``` more text')
    assert parsed == {"key": "val"}
    print("  [OK] ResponseParser")


def test_token_budget():
    assert get_output_budget("strict_json") == 256  # CPU 최적화: 512→256
    assert get_output_budget("fast_chat") == 64  # CPU 최적화: 128→64
    long = "a" * 5000
    trimmed = trim_prompt(long, "strict_json")
    assert len(trimmed) <= 2020
    print("  [OK] TokenBudget")


def test_domain_registry():
    spec = get_task_spec("builder.requirement_parse")
    assert spec is not None
    assert spec.domain == "builder"
    tasks = list_enabled_tasks()
    assert len(tasks) >= 18
    print(f"  [OK] DomainRegistry ({len(tasks)} tasks)")


def test_router():
    r = Router()
    req = TaskRequest(domain="cad", task_name="constraint_parse", user_input="test")
    spec = r.resolve(req)
    assert spec.domain == "cad"
    print("  [OK] Router")


def test_fallback():
    fb = DegradedModeHandler()
    req = TaskRequest(domain="builder", task_name="test", user_input="x")
    result = fb.handle_failure(req, ["test error"])
    assert result.status == TaskStatus.DEGRADED
    assert result.fallback_mode == FallbackMode.MOCK
    # cache
    fb.cache_good_result("builder.test", {"cached": True})
    result2 = fb.handle_failure(req, ["test"])
    assert result2.fallback_mode == FallbackMode.CACHED
    print("  [OK] Fallback")


def test_tools_registry():
    """T-tranche-3 (2026-04-08) cleanup: re-anchored to the canonical
    registry contract. Both the count check and the call-status check use
    the single source of truth in ``tools/registry_contract.py`` and the
    documented "real tool returns status=executed" rule. The pre-existing
    stale ``"manifest_written"`` assertion is gone — that string was the
    return value from the era when ``cad.generate_part`` was a manifest
    stub, before it was promoted to a real adapter."""
    from src.app.tools.registry_contract import (
        EXPECTED_DEFAULT_TOTAL_TOOLS,
        EXPECTED_DEFAULT_REAL_TOOLS,
        EXPECTED_DEFAULT_MANIFEST_TOOLS,
        verify_default_registry_contract,
    )
    reg = create_default_registry()
    tools = reg.list_tools()

    # Exact contract assertion via the single source of truth.
    verify_default_registry_contract(reg)
    assert len(tools) == EXPECTED_DEFAULT_TOTAL_TOOLS
    assert sorted(reg.list_real_tools()) == list(EXPECTED_DEFAULT_REAL_TOOLS)
    assert list(reg.list_manifest_tools()) == list(EXPECTED_DEFAULT_MANIFEST_TOOLS)

    # cad.generate_part is a real tool → its handler returns status=executed,
    # not the legacy manifest_written marker.
    result = reg.call("cad.generate_part", {"name": "bracket"})
    assert result["status"] == "executed", (
        f"cad.generate_part is a real tool; expected status='executed', got {result.get('status')!r}"
    )
    print(f"  [OK] ToolsRegistry ({len(tools)} tools, contract verified, status=executed)")


def test_settings():
    s = AppSettings.from_env()
    assert s.env in ("cpu", "gpu")
    assert s.queue.max_concurrency >= 1
    assert s.llm.base_url.startswith("http")
    print("  [OK] Settings")


TESTS = [
    test_task_request,
    test_circuit_breaker,
    test_health_registry,
    test_queue_manager,
    test_response_parser,
    test_token_budget,
    test_domain_registry,
    test_router,
    test_fallback,
    test_tools_registry,
    test_settings,
]

if __name__ == "__main__":
    print("=" * 60)
    print("vllm_orchestrator unit tests")
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
    if passed == len(TESTS):
        print("ALL UNIT TESTS PASSED!")
