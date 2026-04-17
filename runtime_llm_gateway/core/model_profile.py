"""
core/model_profile.py - 논리 모델 프로필

앱/프로그램 엔진은 실제 모델명을 모른다. 논리 프로필만 안다.
CPU 기본값은 0.5B + 긴 timeout. GPU 전환 시 server_config.json에서 덮어씀.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelProfile:
    profile_id: str
    resolved_model: str
    pool_name: str
    temperature: float = 0.1
    top_p: float = 0.95
    max_output_tokens: int = 512
    timeout_ms: int = 15000        # GPU 기본: 15초
    structured_only: bool = True
    sticky_by: str = "project_session"
    enable_repair: bool = True


# ---------------------------------------------------------------------------
# 기본 프로필 (CPU 안전 모드)
# server_config.json이 이 값을 덮어씀
# ---------------------------------------------------------------------------

DEFAULT_PROFILES: dict[str, ModelProfile] = {
    "strict-json-pool": ModelProfile(
        profile_id="strict-json-pool",
        resolved_model="/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ",
        pool_name="strict-json-pool",
        temperature=0.1,
        top_p=0.95,
        max_output_tokens=512,
        timeout_ms=15000,           # GPU: 15초
        structured_only=True,
        enable_repair=True,
    ),
    "fast-chat-pool": ModelProfile(
        profile_id="fast-chat-pool",
        resolved_model="/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ",
        pool_name="fast-chat-pool",
        temperature=0.3,
        top_p=0.9,
        max_output_tokens=128,      # CPU: 짧게 제한
        timeout_ms=8000,            # GPU: 8초
        structured_only=False,
        enable_repair=False,
    ),
    "long-context-pool": ModelProfile(
        profile_id="long-context-pool",
        resolved_model="/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ",
        pool_name="long-context-pool",
        temperature=0.1,
        top_p=0.95,
        max_output_tokens=512,
        timeout_ms=30000,           # GPU: 30초
        structured_only=True,
        enable_repair=True,
    ),
    "embedding-pool": ModelProfile(
        profile_id="embedding-pool",
        resolved_model="/mnt/d/LLM/models/Qwen2.5-14B-Instruct-AWQ",
        pool_name="embedding-pool",
        temperature=0.0,
        top_p=1.0,
        max_output_tokens=1,
        timeout_ms=30000,
        structured_only=False,
        sticky_by="project",
        enable_repair=False,
    ),
}
