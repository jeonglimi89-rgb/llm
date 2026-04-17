"""metrics.py — Prometheus instrumentation.

모든 중요 이벤트를 Counter/Histogram/Gauge로 노출. /metrics 엔드포인트에서 scrape.

Labels 규칙:
  - task_type: "minecraft.scene_graph" 등
  - status: "success" | "error" | "timeout" | "shed"
  - phase: "llm_generate" | "critic" | "repair" | "heuristic_repair"
  - reason: "cache_hit" | "cache_miss" | "bypassed" | "expired"
"""
from __future__ import annotations

from prometheus_client import (
    Counter, Histogram, Gauge, CollectorRegistry,
    CONTENT_TYPE_LATEST, generate_latest,
)


# ── Single registry (아주 중요: bootstrap.py 에서 한 번만 호출) ──────────────
registry = CollectorRegistry()


# ── Counters ────────────────────────────────────────────────────────────────

request_total = Counter(
    "orchestrator_requests_total",
    "Total /tasks/submit requests by task_type and final status.",
    ["task_type", "status"],
    registry=registry,
)

cache_events = Counter(
    "orchestrator_cache_events_total",
    "Cache lookup results (hit/miss/store/bypass/evict/expire).",
    ["task_type", "event"],
    registry=registry,
)

critic_runs = Counter(
    "orchestrator_critic_runs_total",
    "LLM critic executions.",
    ["task_type", "repair_needed"],  # repair_needed: "true"|"false"
    registry=registry,
)

repair_events = Counter(
    "orchestrator_repair_events_total",
    "Repair events — heuristic (scene_graph_repair) and LLM (critic-guided).",
    ["task_type", "kind"],  # kind: "heuristic" | "critic_driven"
    registry=registry,
)

variant_events = Counter(
    "orchestrator_variant_events_total",
    "Multi-variant planning events.",
    ["task_type", "family"],  # family: "safe_baseline" | "creative_variant" | ...
    registry=registry,
)

auth_rejections = Counter(
    "orchestrator_auth_rejections_total",
    "Rate-limit or auth rejections.",
    ["reason"],  # reason: "rate_limited" | "bad_api_key" | "missing_api_key"
    registry=registry,
)


# ── Histograms (latency distributions) ─────────────────────────────────────

_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)

request_latency = Histogram(
    "orchestrator_request_latency_seconds",
    "End-to-end /tasks/submit latency in seconds.",
    ["task_type", "status", "cache_hit"],  # cache_hit: "true"|"false"
    buckets=_LATENCY_BUCKETS,
    registry=registry,
)

llm_latency = Histogram(
    "orchestrator_llm_latency_seconds",
    "Underlying LLM call latency in seconds.",
    ["task_type", "phase"],  # phase: "generate" | "critic" | "repair"
    buckets=_LATENCY_BUCKETS,
    registry=registry,
)

critic_quality = Histogram(
    "orchestrator_critic_quality_score",
    "Critic overall_quality distribution (0.0~1.0).",
    ["task_type"],
    buckets=(0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
    registry=registry,
)


# ── Gauges ─────────────────────────────────────────────────────────────────

cache_size = Gauge(
    "orchestrator_cache_size",
    "Current request cache size (entries).",
    registry=registry,
)

cache_hit_rate = Gauge(
    "orchestrator_cache_hit_rate",
    "Running cache hit rate (0~1). Updated on each cache event.",
    registry=registry,
)

queue_depth = Gauge(
    "orchestrator_queue_depth",
    "Current queue depth.",
    registry=registry,
)

llm_circuit_open = Gauge(
    "orchestrator_llm_circuit_open",
    "1 if LLM circuit breaker is open, else 0.",
    registry=registry,
)


# ── Convenience helpers (optional import errors safe) ─────────────────────

def observe_cache(task_type: str, event: str) -> None:
    """event: hit|miss|store|bypass|evict|expire"""
    try:
        cache_events.labels(task_type=task_type, event=event).inc()
    except Exception:
        pass


def observe_critic(task_type: str, repair_needed: bool, quality: float, latency_s: float) -> None:
    try:
        critic_runs.labels(task_type=task_type, repair_needed=str(repair_needed).lower()).inc()
        critic_quality.labels(task_type=task_type).observe(max(0.0, min(1.0, quality)))
        llm_latency.labels(task_type=task_type, phase="critic").observe(latency_s)
    except Exception:
        pass


def observe_repair(task_type: str, kind: str) -> None:
    """kind: heuristic | critic_driven"""
    try:
        repair_events.labels(task_type=task_type, kind=kind).inc()
    except Exception:
        pass


def observe_request(task_type: str, status: str, latency_s: float, cache_hit: bool) -> None:
    try:
        request_total.labels(task_type=task_type, status=status).inc()
        request_latency.labels(
            task_type=task_type, status=status, cache_hit=str(cache_hit).lower()
        ).observe(latency_s)
    except Exception:
        pass


def update_cache_stats(size: int, hit_rate: float) -> None:
    try:
        cache_size.set(size)
        cache_hit_rate.set(max(0.0, min(1.0, hit_rate)))
    except Exception:
        pass


def render_metrics() -> tuple[bytes, str]:
    """Returns (body, content_type) for /metrics HTTP response."""
    return generate_latest(registry), CONTENT_TYPE_LATEST
