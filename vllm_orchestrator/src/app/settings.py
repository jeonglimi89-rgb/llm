"""
settings.py - 모든 설정을 정규화

env → yaml → defaults 순서로 병합. 타입 안전.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent  # vllm_orchestrator/


@dataclass
class LLMSettings:
    base_url: str = "http://localhost:8000"
    api_key: str = "internal-token"
    # LLM model path — 기본값은 placeholder. env var LLM_MODEL 으로 실제 경로 덮어씀.
    # Container 배포 시 /models/Qwen2.5-14B-Instruct-AWQ 같은 정규화 경로 권장.
    model: str = "/models/Qwen2.5-14B-Instruct-AWQ"
    max_output_tokens: int = 768
    temperature: float = 0.1
    health_fail_threshold: int = 3      # 연속 N회 실패 시 unhealthy
    # 14B canonical (fits RTX 5070 12GB with AWQ)
    text_model_id: str = "core_text_14b"
    code_model_id: str = "core_text_14b"      # unified: 14B handles code too
    runtime_mode: str = "local_quantized"   # local_quantized | remote_full | remote_quantized
    quantization: str = "awq_marlin"
    enable_adapters: bool = False
    enable_guided_json: bool = True     # structured decoding by default
    max_context: int = 2048


@dataclass
class QueueSettings:
    max_concurrency: int = 4            # GPU: 동시 4건 (RTX 5070, 7B-AWQ)
    max_depth: int = 10                 # 대기열 최대
    task_timeout_s: int = 120           # 단일 태스크 timeout
    queue_timeout_s: int = 300          # 대기열 timeout
    drain_on_shutdown: bool = True


@dataclass
class TimeoutSettings:
    # 2026-04-07 (T-tranche): float seconds end-to-end. int still accepted
    # at construction time (Python int → float widening).
    strict_json_s: float = 15.0
    fast_chat_s: float = 8.0
    long_context_s: float = 30.0
    creative_json_s: float = 75.0       # 14B-AWQ enforce-eager: scene_graph 1200-token output ~50-65s
    embedding_s: float = 5.0
    hard_kill_s: float = 120.0          # 무조건 강제 종료 (creative_json 75s + 여유)


@dataclass
class FallbackSettings:
    enable_degraded: bool = True
    enable_cached: bool = True
    enable_mock: bool = True
    max_retries: int = 1
    retry_delay_s: float = 2.0


@dataclass
class AppSettings:
    env: str = "gpu"                    # cpu | gpu
    debug: bool = False
    base_dir: Path = _BASE
    runtime_dir: Path = _BASE / "runtime"
    logs_dir: Path = _BASE / "logs"
    configs_dir: Path = _BASE / "configs"
    prompts_dir: Path = _BASE / "prompts"
    schemas_dir: Path = _BASE / "schemas"

    llm: LLMSettings = field(default_factory=LLMSettings)
    queue: QueueSettings = field(default_factory=QueueSettings)
    timeouts: TimeoutSettings = field(default_factory=TimeoutSettings)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)

    @classmethod
    def from_env(cls) -> AppSettings:
        """환경변수에서 설정 로드"""
        s = cls()
        s.env = os.getenv("APP_ENV", "gpu")
        s.debug = os.getenv("APP_DEBUG", "").lower() in ("1", "true")
        s.llm.base_url = os.getenv("LLM_ENDPOINT", os.getenv("LLM_BASE_URL", s.llm.base_url))
        s.llm.api_key = os.getenv(os.getenv("LLM_API_KEY_ENV_NAME", "LLM_API_KEY"), s.llm.api_key)
        s.llm.model = os.getenv("LLM_MODEL", s.llm.model)
        s.llm.text_model_id = os.getenv("LLM_TEXT_MODEL_ID", s.llm.text_model_id)
        s.llm.code_model_id = os.getenv("LLM_CODE_MODEL_ID", s.llm.code_model_id)
        s.llm.runtime_mode = os.getenv("LLM_RUNTIME_MODE", s.llm.runtime_mode)
        s.llm.quantization = os.getenv("LLM_QUANTIZATION", s.llm.quantization)
        s.llm.enable_adapters = os.getenv("LLM_ENABLE_ADAPTERS", "").lower() in ("1", "true")
        s.llm.enable_guided_json = os.getenv("LLM_ENABLE_GUIDED_JSON", "true").lower() in ("1", "true", "yes")
        s.llm.max_context = int(os.getenv("LLM_MAX_CONTEXT", str(s.llm.max_context)))
        s.llm.max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", str(s.llm.max_output_tokens)))
        s.queue.max_concurrency = int(os.getenv("QUEUE_CONCURRENCY", str(s.queue.max_concurrency)))
        return s
