"""health.py - /health/live, /health/ready, /health/detail"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])

# container에서 주입
_health_registry = None
_queue = None
_circuit = None


def init(health_registry, queue, circuit):
    global _health_registry, _queue, _circuit
    _health_registry = health_registry
    _queue = queue
    _circuit = circuit


@router.get("/live")
def live():
    return {"status": "alive"}


@router.get("/ready")
def ready():
    if _health_registry and not _health_registry.is_system_healthy():
        return {"status": "not_ready", "reason": "unhealthy components"}
    if _circuit and not _circuit.allow():
        return {"status": "not_ready", "reason": "circuit open"}
    return {"status": "ready"}


@router.get("/detail")
def detail():
    return {
        "health": _health_registry.snapshot() if _health_registry else {},
        "queue": _queue.snapshot() if _queue else {},
        "circuit": _circuit.snapshot() if _circuit else {},
    }
