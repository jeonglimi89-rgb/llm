"""tracing.py — OpenTelemetry distributed tracing integration.

환경변수:
  OTEL_ENABLED=1              — 계측 활성화
  OTEL_EXPORTER_OTLP_ENDPOINT — 수신 서버 (예: http://jaeger:4318 / http://tempo:4318)
  OTEL_SERVICE_NAME           — 이 서비스 이름 (기본: vllm-orchestrator)
  OTEL_TRACES_SAMPLER_ARG     — 샘플링 비율 0.0~1.0 (기본 1.0)

활성화 시:
  - FastAPI 자동 계측 (모든 HTTP 요청 span)
  - httpx/aiohttp 하향 호출 계측 (vLLM, Redis 등)
  - 커스텀 span: 캐시 조회, variant 생성, critic, 각 repair loop

추적 ID는 모든 로그 이벤트에 자동으로 traceId/spanId 가 주입됨.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_log = logging.getLogger("vllm_orch.tracing")

_initialized = False
_tracer = None
_noop = False


def _create_noop_tracer():
    """OpenTelemetry 비활성 시 반환할 dummy tracer."""
    class _NoopSpan:
        def set_attribute(self, *a, **k): return None
        def set_attributes(self, *a, **k): return None
        def set_status(self, *a, **k): return None
        def record_exception(self, *a, **k): return None
        def add_event(self, *a, **k): return None
        def end(self): return None
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _NoopTracer:
        def start_as_current_span(self, *a, **k): return _NoopSpan()
        def start_span(self, *a, **k): return _NoopSpan()

    return _NoopTracer()


def init_tracing(app=None) -> None:
    """부트스트랩에서 한 번만 호출. Idempotent."""
    global _initialized, _tracer, _noop
    if _initialized:
        return
    _initialized = True

    if os.getenv("OTEL_ENABLED", "0").lower() not in ("1", "true", "yes"):
        _tracer = _create_noop_tracer()
        _noop = True
        _log.info("OpenTelemetry disabled (OTEL_ENABLED=0)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ParentBased

        service_name = os.getenv("OTEL_SERVICE_NAME", "vllm-orchestrator")
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        try:
            sample_rate = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))
        except ValueError:
            sample_rate = 1.0

        resource = Resource.create({
            "service.name": service_name,
            "service.version": os.getenv("SERVICE_VERSION", "dev"),
            "deployment.environment": os.getenv("APP_ENV", "gpu"),
        })
        sampler = ParentBased(TraceIdRatioBased(max(0.0, min(1.0, sample_rate))))
        provider = TracerProvider(resource=resource, sampler=sampler)

        # OTLP HTTP exporter (Jaeger/Tempo 호환)
        otlp_traces_url = endpoint.rstrip("/") + "/v1/traces"
        exporter = OTLPSpanExporter(endpoint=otlp_traces_url)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

        # FastAPI 자동 계측
        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app, excluded_urls="/health.*,/metrics")
            except Exception as e:
                _log.warning(f"FastAPI instrumentation failed: {e}")

        # httpx / aiohttp-client 자동 계측 (vLLM downstream 호출)
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except Exception:
            pass
        try:
            from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
            AioHttpClientInstrumentor().instrument()
        except Exception:
            pass

        _log.info(
            f"OpenTelemetry enabled → service={service_name} endpoint={otlp_traces_url} sample={sample_rate}"
        )
    except Exception as e:
        _log.warning(f"OpenTelemetry init failed ({e}) → using noop tracer")
        _tracer = _create_noop_tracer()
        _noop = True


def tracer():
    """전역 tracer 반환. init_tracing 안 됐으면 noop."""
    global _tracer
    if _tracer is None:
        _tracer = _create_noop_tracer()
    return _tracer


@contextmanager
def span(name: str, **attrs) -> Iterator[Any]:
    """편의용 context manager — 코드 어디서든 with span('...'):으로 감쌈.
    span 종료 시 예외 자동 기록.
    """
    s = tracer().start_as_current_span(name)
    try:
        with s as current:
            if attrs:
                try:
                    for k, v in attrs.items():
                        current.set_attribute(k, v)
                except Exception:
                    pass
            yield current
    except Exception as e:
        try:
            current.record_exception(e)
        except Exception:
            pass
        raise


def add_span_attribute(key: str, value: Any) -> None:
    """현재 활성 span에 속성 추가 (no-op if no active span)."""
    if _noop:
        return
    try:
        from opentelemetry import trace
        s = trace.get_current_span()
        if s and s.is_recording():
            s.set_attribute(key, value)
    except Exception:
        pass


def record_event(name: str, attrs: Optional[dict] = None) -> None:
    """현재 span에 event 기록 (e.g. cache_hit, repair_triggered)."""
    if _noop:
        return
    try:
        from opentelemetry import trace
        s = trace.get_current_span()
        if s and s.is_recording():
            s.add_event(name, attributes=attrs or {})
    except Exception:
        pass


def current_trace_id() -> Optional[str]:
    """현재 trace ID를 hex string으로. 로그 컨텍스트 삽입용."""
    if _noop:
        return None
    try:
        from opentelemetry import trace
        s = trace.get_current_span()
        if s and s.get_span_context().is_valid:
            return format(s.get_span_context().trace_id, "032x")
    except Exception:
        pass
    return None
