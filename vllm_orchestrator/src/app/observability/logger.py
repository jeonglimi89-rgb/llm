"""구조화된 로거"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, UTC
from pathlib import Path


_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str, log_dir: str | Path | None = None) -> logging.Logger:
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(f"vllm_orch.{name}")
    logger.setLevel(logging.DEBUG)

    # console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)

    # file — RotatingFileHandler로 디스크 무한 증가 방지
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        try:
            max_bytes = int(os.getenv("LOG_MAX_BYTES", str(50 * 1024 * 1024)))  # 50MB
            backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))
        except ValueError:
            max_bytes, backup_count = 50 * 1024 * 1024, 5
        fh = RotatingFileHandler(
            os.path.join(str(log_dir), f"{name}.log"),
            maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

    _loggers[name] = logger
    return logger


def log_event(logger: logging.Logger, event: str, **kwargs) -> None:
    """구조화 이벤트 로그 (OpenTelemetry trace_id 자동 주입)."""
    entry = {"event": event, "ts": datetime.now(UTC).isoformat(), **kwargs}
    # Trace correlation (optional)
    try:
        from .tracing import current_trace_id
        tid = current_trace_id()
        if tid:
            entry["trace_id"] = tid
    except Exception:
        pass
    logger.info(json.dumps(entry, ensure_ascii=False, default=str))
