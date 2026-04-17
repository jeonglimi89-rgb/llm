"""
providers/vllm_provider.py - vLLM OpenAI 호환 클라이언트

vLLM 서버의 /v1/chat/completions 엔드포인트를 호출.
structured outputs (format=json_schema) 지원.

vLLM이 없으면 MockProvider로 fallback (개발/테스트용).
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any, Optional

from ..core.model_profile import ModelProfile


class VLLMProvider:
    """vLLM OpenAI 호환 HTTP 클라이언트 (외부 패키지 의존 없음)"""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "internal-token",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider_name = "vllm"
        self._supports_structured = self._check_structured_support()

    def chat_structured(
        self,
        profile: ModelProfile,
        messages: list[dict],
        schema: dict,
        request_id: str = "",
    ) -> dict:
        """structured output 강제 호출. vLLM structured_outputs + X-Request-Id 지원."""
        payload = {
            "model": profile.resolved_model,
            "messages": messages,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_output_tokens,
        }

        # vLLM 0.19+ OpenAI-compatible guided decoding (json_schema)
        if self._supports_structured and schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema,
                },
            }

        extra_headers = {}
        if request_id:
            extra_headers["X-Request-Id"] = request_id

        return self._post("/v1/chat/completions", payload, profile.timeout_ms, extra_headers)

    def chat_freeform(
        self,
        profile: ModelProfile,
        messages: list[dict],
    ) -> dict:
        """자유 텍스트 응답 (fast-chat-pool용)"""
        payload = {
            "model": profile.resolved_model,
            "messages": messages,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_output_tokens,
        }

        return self._post("/v1/chat/completions", payload, profile.timeout_ms)

    def embed(self, profile: ModelProfile, texts: list[str]) -> dict:
        """vLLM OpenAI 호환 Embeddings API 호출"""
        payload = {
            "model": profile.resolved_model,
            "input": texts,
        }
        return self._post("/v1/embeddings", payload, profile.timeout_ms)

    def parse_response(self, raw: dict) -> tuple[str, Optional[int], Optional[int]]:
        """raw 응답에서 텍스트 + 토큰 수 추출"""
        text = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = raw.get("usage", {})
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")

    def is_available(self) -> bool:
        """vLLM 서버 연결 확인"""
        try:
            url = f"{self.base_url}/v1/models"
            req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {self.api_key}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _check_structured_support(self) -> bool:
        """vLLM 네이티브 서버인지 (structured_outputs 지원 여부)"""
        try:
            url = f"{self.base_url}/v1/models"
            req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {self.api_key}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                # vLLM은 /v1/models에 "object":"list" 반환
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("object") == "list"
        except Exception:
            return False

    def _post(self, endpoint: str, payload: dict, timeout_ms: int, extra_headers: dict = None, _retry: int = 0) -> dict:
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        timeout_s = max(timeout_ms / 1000, 5)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            error_type = "timeout" if "timed out" in str(e).lower() else "connection"
            # 1회 자동 재시도 (timeout/connection)
            if _retry < 1:
                import time as _t
                _t.sleep(1)
                return self._post(endpoint, payload, timeout_ms, extra_headers, _retry + 1)
            raise ConnectionError(f"vLLM {error_type} failed after retry ({self.base_url}): {e}")


class MockProvider:
    """
    개발/테스트용 Mock Provider.
    vLLM 없이 Gateway 파이프라인을 검증할 수 있다.
    """

    def __init__(self):
        self.provider_name = "mock"
        self._call_count = 0

    def chat_structured(
        self,
        profile: ModelProfile,
        messages: list[dict],
        schema: dict,
    ) -> dict:
        """스키마 기반으로 기본값을 채운 mock 응답 생성"""
        self._call_count += 1
        mock_content = self._generate_from_schema(schema)

        return {
            "choices": [{"message": {"content": json.dumps(mock_content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

    def chat_freeform(
        self,
        profile: ModelProfile,
        messages: list[dict],
    ) -> dict:
        self._call_count += 1
        user_msg = messages[-1]["content"] if messages else ""
        return {
            "choices": [{"message": {"content": f"[mock] Acknowledged: {user_msg[:50]}"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

    def parse_response(self, raw: dict) -> tuple[str, Optional[int], Optional[int]]:
        text = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = raw.get("usage", {})
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")

    def is_available(self) -> bool:
        return True

    def _generate_from_schema(self, schema: dict) -> dict:
        """JSON Schema에서 기본값으로 채운 dict 생성"""
        return _fill_schema_defaults(schema)


def _fill_schema_defaults(schema: dict) -> Any:
    """재귀적으로 스키마 기본값 생성"""
    schema_type = schema.get("type", "object")

    if schema_type == "object":
        result = {}
        for prop_name, prop_schema in schema.get("properties", {}).items():
            result[prop_name] = _fill_schema_defaults(prop_schema)
        return result
    elif schema_type == "array":
        items = schema.get("items", {})
        return [_fill_schema_defaults(items)]
    elif schema_type == "string":
        enum = schema.get("enum")
        return enum[0] if enum else schema.get("default", "")
    elif schema_type == "integer":
        return schema.get("default", 1)
    elif schema_type == "number":
        return schema.get("default", 0.5)
    elif schema_type == "boolean":
        return schema.get("default", True)
    elif isinstance(schema_type, list):
        # Union type: pick first non-null
        for t in schema_type:
            if t != "null":
                return _fill_schema_defaults({"type": t})
        return None
    return None
