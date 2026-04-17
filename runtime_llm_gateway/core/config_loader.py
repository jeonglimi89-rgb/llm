"""
core/config_loader.py - server_config.json → Gateway 구성

GPU 서버 교체 시 server_config.json만 수정하면 됨.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .model_profile import ModelProfile, DEFAULT_PROFILES
from ..providers.vllm_provider import VLLMProvider, MockProvider
from ..routing.task_router import TaskRouter, ShardSelector
from ..execution.gateway_service import RuntimeGatewayService
from ..telemetry.audit_logger import AuditLogger


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "server_config.json"


def load_config(config_path: Optional[str] = None) -> dict:
    path = Path(config_path) if config_path else _CONFIG_PATH
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def build_gateway(config_path: Optional[str] = None) -> RuntimeGatewayService:
    """server_config.json으로 Gateway 전체를 구성"""
    cfg = load_config(config_path)

    # Provider
    base_url = cfg.get("base_url", "http://localhost:8000")
    api_key = cfg.get("api_key", "internal-token")
    provider = VLLMProvider(base_url=base_url, api_key=api_key)
    if not provider.is_available():
        print(f"[config] vLLM not available at {base_url}, using MockProvider")
        provider = MockProvider()

    # Profiles
    profiles = dict(DEFAULT_PROFILES)
    for pid, pcfg in cfg.get("profiles", {}).items():
        if pid in profiles:
            p = profiles[pid]
            if "resolved_model" in pcfg:
                p.resolved_model = pcfg["resolved_model"]
            if "temperature" in pcfg:
                p.temperature = pcfg["temperature"]
            if "top_p" in pcfg:
                p.top_p = pcfg["top_p"]
            if "max_output_tokens" in pcfg:
                p.max_output_tokens = pcfg["max_output_tokens"]
            if "timeout_ms" in pcfg:
                p.timeout_ms = pcfg["timeout_ms"]
            if "structured_only" in pcfg:
                p.structured_only = pcfg["structured_only"]
            if "enable_repair" in pcfg:
                p.enable_repair = pcfg["enable_repair"]

    # Router + Shard
    router = TaskRouter(profiles=profiles)
    shard = ShardSelector(shard_count=cfg.get("shard_count", 4))

    return RuntimeGatewayService(
        provider=provider,
        router=router,
        shard_selector=shard,
        audit_logger=AuditLogger(),
    )
