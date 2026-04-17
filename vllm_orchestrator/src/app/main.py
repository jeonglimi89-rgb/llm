"""
main.py - 엔트리포인트

실행: cd vllm_orchestrator && python -m src.app.main
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 패키지 경로
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .bootstrap import Container
from .api.routes import health as health_routes
from .api.routes import tasks as task_routes
from .api.routes import scene as scene_routes
from .api.routes import minecraft as minecraft_routes
from .api.routes import resourcepack as resourcepack_routes
from .api.routes import npc as npc_routes
from .api.routes import builder as builder_routes
from .api.routes import cad as cad_routes
from .api.routes import gateway_shim as gateway_shim_routes
from .api.routes import metrics as metrics_routes
from .api.routes import stream as stream_routes
from .api.routes import feedback as feedback_routes


def create_app() -> FastAPI:
    app = FastAPI(
        title="vLLM Orchestrator",
        description="CPU-safe LLM orchestrator with queue, circuit breaker, fallback",
        version="1.0",
    )

    # CORS — 화이트리스트 기반. env var CORS_ALLOW_ORIGINS (comma-separated).
    # 비워두면 기본 dev origin만 허용. 절대 "*" 사용 금지 (프로덕션 보안).
    _cors_raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    _cors_list = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    if not _cors_list:
        _cors_list = [
            "http://localhost:5173", "http://localhost:3457",
            "http://127.0.0.1:5173", "http://127.0.0.1:3457",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    )

    # API key + rate limit (환경변수로 on/off)
    from .api.auth_middleware import AuthRateLimitMiddleware
    app.add_middleware(AuthRateLimitMiddleware)

    # OpenTelemetry tracing (env OTEL_ENABLED=1 gated)
    from .observability.tracing import init_tracing
    init_tracing(app)

    c = Container()

    # 라우트에 의존성 주입
    health_routes.init(c.health, c.queue, c.circuit)
    task_routes.init(c.router, c.dispatcher, c.fallback)
    task_routes.init_orchestrated(c.orchestrated_pipeline)
    scene_routes.init(c.orchestrated_pipeline)
    minecraft_routes.init(c.orchestrated_pipeline)
    resourcepack_routes.init(c.orchestrated_pipeline)
    npc_routes.init(c.orchestrated_pipeline)
    builder_routes.init(c.orchestrated_pipeline)
    cad_routes.init(c.orchestrated_pipeline)
    gateway_shim_routes.init(c.orchestrated_pipeline)
    stream_routes.init(c.router, c.dispatcher, c.fallback)

    app.include_router(health_routes.router)
    app.include_router(stream_routes.router)
    app.include_router(task_routes.router)
    app.include_router(scene_routes.router)
    app.include_router(minecraft_routes.router)
    app.include_router(resourcepack_routes.router)
    app.include_router(npc_routes.router)
    app.include_router(builder_routes.router)
    app.include_router(cad_routes.router)
    app.include_router(gateway_shim_routes.router)
    app.include_router(metrics_routes.router)
    app.include_router(feedback_routes.router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8100"))
    uvicorn.run("src.app.main:app", host="0.0.0.0", port=port)
