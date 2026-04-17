"""
app.py - Runtime LLM Gateway 메인 앱

기본 동작: config → fallback 서버 순서로 연결 시도.
서버가 없으면 MockProvider (테스트용).

환경변수:
  VLLM_BASE_URL  (설정 시 config보다 우선)
  VLLM_API_KEY=internal-token
  GATEWAY_PORT=8100
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI

from .api.routes_runtime import router, set_provider
from .providers.vllm_provider import VLLMProvider, MockProvider
from .core.config_loader import load_config


# CPU fallback 서버 후보 주소 (순서대로 시도)
_FALLBACK_URLS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def _find_provider() -> tuple:
    """사용 가능한 provider를 찾는다. 환경변수 → config → fallback 순."""
    cfg = load_config()
    api_key = os.getenv("VLLM_API_KEY", cfg.get("api_key", "internal-token"))

    # 1. 환경변수 (최우선)
    env_url = os.getenv("VLLM_BASE_URL")
    if env_url:
        p = VLLMProvider(base_url=env_url, api_key=api_key)
        if p.is_available():
            return p, f"env: {env_url}"

    # 2. server_config.json
    cfg_url = cfg.get("base_url", "")
    if cfg_url:
        p = VLLMProvider(base_url=cfg_url, api_key=api_key)
        if p.is_available():
            return p, f"config: {cfg_url}"

    # 3. WSL fallback (동적 IP 탐지)
    try:
        import subprocess
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "root", "-e", "bash", "-c", "hostname -I | awk '{print $1}'"],
            capture_output=True, text=True, timeout=5
        )
        wsl_ip = result.stdout.strip()
        if wsl_ip:
            wsl_url = f"http://{wsl_ip}:8000"
            p = VLLMProvider(base_url=wsl_url, api_key=api_key)
            if p.is_available():
                return p, f"wsl: {wsl_url}"
    except Exception:
        pass

    # 4. localhost fallback
    for url in _FALLBACK_URLS:
        p = VLLMProvider(base_url=url, api_key=api_key)
        if p.is_available():
            return p, f"fallback: {url}"

    # 5. 전부 실패 → Mock
    return MockProvider(), "mock (no server found)"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Runtime LLM Gateway",
        description="4개 프로그램 공통 LLM Gateway (CPU/GPU 자동 전환)",
        version="2.0",
    )
    app.include_router(router)

    provider, source = _find_provider()
    set_provider(provider)
    print(f"[Gateway] Provider: {provider.provider_name} via {source}")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("GATEWAY_PORT", "8100"))
    uvicorn.run("runtime_llm_gateway.app:app", host="0.0.0.0", port=port, reload=True)
