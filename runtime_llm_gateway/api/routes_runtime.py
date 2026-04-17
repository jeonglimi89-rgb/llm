"""
api/routes_runtime.py - Gateway HTTP 엔드포인트

POST /api/gateway/process      - 메인 단발 파이프라인
POST /api/gateway/pipeline     - Planner→Executor→Critic 3단계
GET  /api/gateway/status       - 상태 + 메트릭
GET  /api/gateway/health       - 헬스체크
GET  /api/gateway/metrics      - Prometheus 호환 메트릭
"""

from __future__ import annotations

from fastapi import APIRouter, Body, FastAPI
from fastapi.responses import PlainTextResponse

from ..core.envelope import RequestEnvelope, Message
from ..execution.gateway_service import RuntimeGatewayService
from ..execution.pipeline_service import PipelineService
from ..providers.vllm_provider import MockProvider
from ..telemetry.audit_logger import AuditLogger

# ---------------------------------------------------------------------------
# 초기화
# ---------------------------------------------------------------------------

_audit = AuditLogger()
_provider = MockProvider()
_gateway = RuntimeGatewayService(provider=_provider, audit_logger=_audit)
_pipeline = PipelineService(provider=_provider, audit_logger=_audit)

router = APIRouter(prefix="/api/gateway", tags=["runtime-llm-gateway"])


def set_provider(provider) -> None:
    """vLLM 연결 시 provider 교체"""
    global _gateway, _pipeline, _provider
    _provider = provider
    _gateway = RuntimeGatewayService(provider=provider, audit_logger=_audit)
    _pipeline = PipelineService(provider=provider, audit_logger=_audit)


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/process")
def process_request(body: dict = Body(...)):
    """
    단발 Gateway 파이프라인.
    Request body: RequestEnvelope JSON
    Response: ResponseEnvelope JSON
    """
    envelope = RequestEnvelope.from_dict(body)
    response = _gateway.process(envelope)
    return response.to_dict()


@router.post("/pipeline")
def pipeline_request(body: dict = Body(...)):
    """
    Planner → Executor → Critic 3단계 파이프라인.

    Request body:
    {
        "request": { ...RequestEnvelope... },
        "plan_schema": { ...JSON Schema for planner... },
        "exec_schema": { ...JSON Schema for executor... }
    }
    """
    req_data = body.get("request", body)
    plan_schema = body.get("plan_schema", {
        "type": "object",
        "required": ["goal", "alternatives", "constraints", "uncertainties"],
        "properties": {
            "goal": {"type": "string"},
            "alternatives": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
    })
    exec_schema = body.get("exec_schema", {"type": "object"})

    envelope = RequestEnvelope.from_dict(req_data)
    result = _pipeline.run_full_pipeline(envelope, plan_schema, exec_schema)
    return result


@router.get("/status")
def gateway_status():
    """상태 + 메트릭"""
    return {
        "provider": _provider.provider_name,
        "provider_available": _provider.is_available(),
        "metrics": _audit.get_metrics(),
    }


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus 호환 메트릭 엔드포인트"""
    m = _audit.get_metrics()
    task_metrics = _audit.get_task_metrics() if hasattr(_audit, "get_task_metrics") else {}

    lines = [
        "# HELP gateway_requests_total Total gateway requests",
        "# TYPE gateway_requests_total counter",
        f'gateway_requests_total {m.get("total", 0)}',
        "",
        "# HELP gateway_success_total Successful requests",
        "# TYPE gateway_success_total counter",
        f'gateway_success_total {m.get("success", 0)}',
        "",
        "# HELP gateway_failure_total Failed requests",
        "# TYPE gateway_failure_total counter",
        f'gateway_failure_total {m.get("failure", 0)}',
        "",
        "# HELP gateway_repair_total Repair attempts",
        "# TYPE gateway_repair_total counter",
        f'gateway_repair_attempted_total {m.get("repair_attempted", 0)}',
        f'gateway_repair_success_total {m.get("repair_success", 0)}',
        "",
        "# HELP gateway_success_rate Success rate",
        "# TYPE gateway_success_rate gauge",
        f'gateway_success_rate {m.get("success_rate", 0.0):.4f}',
    ]

    # task_type별 메트릭
    for task_type, tm in task_metrics.items():
        safe_label = task_type.replace(".", "_")
        lines.append(f'gateway_task_latency_ms{{task_type="{task_type}"}} {tm.get("avg_latency_ms", 0)}')
        lines.append(f'gateway_task_requests_total{{task_type="{task_type}"}} {tm.get("count", 0)}')

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 독립 실행
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Runtime LLM Gateway",
        description="4개 프로그램 공통 vLLM Gateway - structured output + validation + routing",
        version="1.0",
    )
    app.include_router(router)
    return app
