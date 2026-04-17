"""
test_dispatcher_layered_review.py — dispatcher 의 layered review 통합

dispatcher.py 가 더 이상 'JSON 파싱 성공 = validated=True' 를 박지 않고,
review/task_contracts.evaluate_task_contract 의 5게이트 결과만이
TaskResult.validated 가 되는지 확인한다.

LLM adapter 는 가짜로 주입한다 (FakeAdapter).
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.core.contracts import TaskRequest, TaskResult
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.execution.timeouts import TimeoutPolicy
from src.app.observability.health_registry import HealthRegistry
from src.app.llm.client import LLMClient
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher


class FakeAdapter:
    """LLMClient 가 호출하는 generate() 를 흉내내는 fake."""

    def __init__(self, output_text: str):
        self.output_text = output_text

    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        return {"text": self.output_text}

    def is_available(self) -> bool:
        return True


def _make_dispatcher(adapter: FakeAdapter) -> tuple[Dispatcher, Router]:
    health = HealthRegistry()
    cb = CircuitBreaker()
    queue = QueueManager(max_concurrency=1, max_depth=8)
    scheduler = Scheduler(cooldown_heavy_s=0.0, cooldown_light_s=0.0)
    llm = LLMClient(adapter, health, cb, max_retries=0)
    router = Router()
    disp = Dispatcher(llm_client=llm, queue=queue, scheduler=scheduler, timeouts=TimeoutPolicy())
    return disp, router


def test_dispatcher_chinese_keys_marked_invalid():
    """builder.requirement_parse 에 한자 키 응답이 오면 validated=False, layered_judgment 가 wrong_key_locale 로 표시."""
    print("  [1] Chinese-key payload → validated=False")
    fake = FakeAdapter('{"楼层": "2층", "户型": "모던"}')
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="builder", task_name="requirement_parse", user_input="2층 주택 모던")
    result = disp.dispatch(req, router.resolve(req))

    assert result.slots is not None, "parse should still succeed"
    assert result.validated is False, "should NOT be validated"
    assert result.layered_judgment is not None, "layered_judgment must be attached"
    cats = result.layered_judgment["failure_categories"]
    assert "wrong_key_locale" in cats, f"got: {cats}"
    print("    OK")


def test_dispatcher_validator_shape_marked_invalid():
    print("  [2] cad.constraint_parse validator-shape → validated=False")
    fake = FakeAdapter('{"valid": true, "message": "x", "error": null}')
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="cad", task_name="constraint_parse", user_input="방수 케이스")
    result = disp.dispatch(req, router.resolve(req))

    assert result.slots is not None
    assert result.validated is False
    cats = result.layered_judgment["failure_categories"]
    assert "validator_shaped_response" in cats
    print("    OK")


def test_dispatcher_url_hallucination_blocked():
    print("  [3] animation.camera_intent URL → validated=False")
    fake = FakeAdapter(json.dumps({
        "framing": "wide",
        "data": {"image_url": "https://example.com/img.jpg"},
    }))
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="animation", task_name="camera_intent_parse", user_input="공포 와이드샷")
    result = disp.dispatch(req, router.resolve(req))
    assert result.validated is False
    assert "hallucinated_external_reference" in result.layered_judgment["failure_categories"]
    print("    OK")


def test_dispatcher_clean_payload_validated():
    print("  [4] clean payload → validated=True")
    fake = FakeAdapter('{"intent": "거실 확장 및 창문 유지"}')
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="거실만 더 크게")
    result = disp.dispatch(req, router.resolve(req))
    assert result.slots is not None
    assert result.validated is True, (
        f"clean payload was rejected: {result.layered_judgment['failure_categories']}"
    )
    assert result.layered_judgment["final_judgment"] == "pass"
    print("    OK")


def test_dispatcher_to_dict_includes_layered():
    print("  [5] TaskResult.to_dict() includes layered_judgment field")
    fake = FakeAdapter('{"intent": "거실 확장"}')
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="builder", task_name="patch_intent_parse", user_input="거실만 키워")
    result = disp.dispatch(req, router.resolve(req))
    d = result.to_dict()
    assert "layered_judgment" in d
    assert d["validated"] in (True, False)
    print("    OK")


def test_dispatcher_parse_failure_marks_schema_fail():
    print("  [6] LLM emits non-JSON → validated=False, schema_failure")
    fake = FakeAdapter("not a json at all just garbage text")
    disp, router = _make_dispatcher(fake)
    req = TaskRequest(domain="builder", task_name="requirement_parse", user_input="2층 주택")
    result = disp.dispatch(req, router.resolve(req))
    assert result.status == "error", f"got status={result.status}"
    assert result.validated is False
    assert result.layered_judgment is not None
    cats = result.layered_judgment["failure_categories"]
    assert "schema_failure" in cats or "empty_output" in cats, cats
    print("    OK")


TESTS = [
    test_dispatcher_chinese_keys_marked_invalid,
    test_dispatcher_validator_shape_marked_invalid,
    test_dispatcher_url_hallucination_blocked,
    test_dispatcher_clean_payload_validated,
    test_dispatcher_to_dict_includes_layered,
    test_dispatcher_parse_failure_marks_schema_fail,
]


if __name__ == "__main__":
    print("=" * 60)
    print("dispatcher layered review integration tests")
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
