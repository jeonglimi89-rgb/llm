"""Mock LLM adapters for testing: success, slow, broken, failure"""
from __future__ import annotations

import json
import time


class FakeLLMSuccess:
    """정상 JSON 반환"""
    provider_name = "fake_success"
    call_count = 0

    def generate(self, messages, max_tokens=512, temperature=0.1, timeout_s=120):
        self.call_count += 1
        user = messages[-1]["content"] if messages else ""
        return {
            "text": json.dumps({"intent": "create", "target": user[:20], "confidence": 0.9}, ensure_ascii=False),
            "prompt_tokens": 50,
            "completion_tokens": 30,
        }

    def is_available(self):
        return True


class FakeLLMSlow:
    """지연 후 반환 (timeout 유도용)"""
    provider_name = "fake_slow"
    call_count = 0

    def __init__(self, delay_s: float = 5.0):
        self.delay_s = delay_s

    def generate(self, messages, max_tokens=512, temperature=0.1, timeout_s=120):
        self.call_count += 1
        time.sleep(self.delay_s)
        return {
            "text": json.dumps({"slow": True}),
            "prompt_tokens": 50,
            "completion_tokens": 10,
        }

    def is_available(self):
        return True


class FakeLLMBrokenJSON:
    """깨진 JSON 반환"""
    provider_name = "fake_broken"
    call_count = 0

    def generate(self, messages, max_tokens=512, temperature=0.1, timeout_s=120):
        self.call_count += 1
        return {
            "text": '{"intent": "create", "target": broken no close',
            "prompt_tokens": 50,
            "completion_tokens": 20,
        }

    def is_available(self):
        return True


class FakeLLMFailure:
    """네트워크 에러 시뮬레이션"""
    provider_name = "fake_failure"
    call_count = 0

    def generate(self, messages, max_tokens=512, temperature=0.1, timeout_s=120):
        self.call_count += 1
        raise ConnectionError("Simulated network failure")

    def is_available(self):
        return False
