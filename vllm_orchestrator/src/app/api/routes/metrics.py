"""metrics.py — /metrics endpoint for Prometheus scraping."""
from __future__ import annotations

from fastapi import APIRouter, Response

from ...observability.metrics import render_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint. Returns text/plain exposition format."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
