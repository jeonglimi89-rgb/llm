"""
tests/test_vllm_live.py - 실제 vLLM/fallback 서버 연결 테스트

WSL에서 서버 시작 후 Windows에서 실행:
  cd LLM && python -X utf8 -m runtime_llm_gateway.tests.test_vllm_live

서버가 없으면 SKIP.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.providers.vllm_provider import VLLMProvider
from runtime_llm_gateway.core.envelope import RequestEnvelope, Message
from runtime_llm_gateway.execution.gateway_service import RuntimeGatewayService
from runtime_llm_gateway.execution.pipeline_service import PipelineService
from runtime_llm_gateway.telemetry.audit_logger import AuditLogger
import tempfile


BASE_URL = "http://localhost:8000"
API_KEY = "internal-token"


def check_server():
    """서버 연결 확인"""
    provider = VLLMProvider(base_url=BASE_URL, api_key=API_KEY)
    if not provider.is_available():
        print(f"[SKIP] vLLM server not available at {BASE_URL}")
        print("WSL Ubuntu에서 서버를 먼저 시작하세요:")
        print("  source ~/vllm-env/bin/activate")
        print("  vllm serve ... --host 0.0.0.0 --port 8000")
        print("  또는: python3 /mnt/c/Users/SUZZI/Downloads/LLM/fallback_server.py")
        return None
    print(f"[OK] Server connected at {BASE_URL}")
    return provider


def test_simple_chat(provider):
    """간단한 채팅 테스트"""
    print("\n  1. Simple chat")
    from runtime_llm_gateway.core.model_profile import DEFAULT_PROFILES
    profile = DEFAULT_PROFILES["fast-chat-pool"]
    profile.resolved_model = "qwen2.5-3b"  # 실제 모델명

    raw = provider.chat_freeform(profile, [
        {"role": "system", "content": "Answer in Korean. Be brief."},
        {"role": "user", "content": "안녕? 넌 뭐야?"},
    ])
    text, pt, ct = provider.parse_response(raw)
    print(f"    Response: {text[:100]}")
    print(f"    Tokens: prompt={pt}, completion={ct}")
    return True


def test_structured_output(provider):
    """Structured output 테스트"""
    print("\n  2. Structured JSON output")
    from runtime_llm_gateway.core.model_profile import DEFAULT_PROFILES
    profile = DEFAULT_PROFILES["strict-json-pool"]
    profile.resolved_model = "qwen2.5-3b"

    schema = {
        "type": "object",
        "required": ["intent_type", "target", "confidence"],
        "properties": {
            "intent_type": {"type": "string", "enum": ["create", "modify", "query"]},
            "target": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }

    raw = provider.chat_structured(profile, [
        {"role": "system", "content": "Parse the user request into structured JSON. Output ONLY JSON."},
        {"role": "user", "content": "중세풍 성 만들어줘"},
    ], schema)
    text, _, _ = provider.parse_response(raw)
    print(f"    Raw: {text[:200]}")

    try:
        parsed = json.loads(text)
        print(f"    Parsed: {parsed}")
        assert "intent_type" in parsed
        return True
    except json.JSONDecodeError:
        print(f"    [WARN] JSON parse failed, trying extract...")
        # JSON 부분만 추출
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            print(f"    Extracted: {parsed}")
            return True
        return False


def test_gateway_integration(provider):
    """Gateway 통합 테스트"""
    print("\n  3. Gateway integration")
    gw = RuntimeGatewayService(
        provider=provider,
        audit_logger=AuditLogger(tempfile.mkdtemp()),
    )

    req = RequestEnvelope(
        task_type="builder.requirement_parse",
        project_id="live_test",
        session_id="live_s1",
        messages=[Message(role="user", content="2층 주택 거실 크게 해줘")],
        schema_id="builder/requirement_v1",
    )

    start = time.time()
    resp = gw.process(req)
    elapsed = int((time.time() - start) * 1000)

    print(f"    Profile: {resp.model_profile}")
    print(f"    Schema OK: {resp.validation.schema_ok}")
    print(f"    Domain OK: {resp.validation.domain_ok}")
    print(f"    Latency: {elapsed}ms")
    if resp.structured_content:
        print(f"    Content keys: {list(resp.structured_content.keys())}")
    if resp.error_code:
        print(f"    Error: {resp.error_code}: {resp.error_message}")
    return resp.error_code is None


def test_cad_pipeline(provider):
    """CAD 풀 파이프라인 테스트"""
    print("\n  4. CAD full pipeline (planner→executor→critic)")
    pipe = PipelineService(
        provider=provider,
        audit_logger=AuditLogger(tempfile.mkdtemp()),
    )

    plan_schema = {
        "type": "object",
        "required": ["goal", "alternatives", "constraints", "uncertainties"],
        "properties": {
            "goal": {"type": "string"},
            "alternatives": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
    }

    exec_schema = {
        "type": "object",
        "required": ["systems", "constraints", "priorities"],
        "properties": {
            "systems": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "object"}},
            "priorities": {"type": "array", "items": {"type": "string"}},
        },
    }

    req = RequestEnvelope(
        task_type="cad.constraint_parse",
        project_id="live_cad",
        session_id="live_s2",
        messages=[Message(role="user", content="방수 샤워필터 설계, 배수 연결 + 전기 배선 포함")],
        schema_id="cad/constraint_v1",
    )

    start = time.time()
    result = pipe.run_full_pipeline(req, plan_schema, exec_schema)
    elapsed = int((time.time() - start) * 1000)

    print(f"    Plan: {json.dumps(result.get('plan', {}), ensure_ascii=False)[:150]}...")
    print(f"    Execution keys: {list(result.get('execution', {}).keys())}")
    print(f"    Critic verdict: {result.get('review', {}).get('verdict', '?')}")
    print(f"    Latency: {elapsed}ms")
    return "plan" in result and "execution" in result


if __name__ == "__main__":
    print("=" * 60)
    print("Live vLLM/Fallback Server Tests")
    print("=" * 60)

    provider = check_server()
    if provider is None:
        sys.exit(0)

    tests = [
        ("Simple chat", lambda: test_simple_chat(provider)),
        ("Structured output", lambda: test_structured_output(provider)),
        ("Gateway integration", lambda: test_gateway_integration(provider)),
        ("CAD pipeline", lambda: test_cad_pipeline(provider)),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            if fn():
                passed += 1
            else:
                print(f"    [WARN] {name} had issues")
                failed += 1
        except Exception as e:
            print(f"    [FAIL] {name}: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} passed")
    print("=" * 60)
