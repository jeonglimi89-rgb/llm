"""paths.py — 중앙화된 스토리지 경로 resolver.

모든 데이터/로그/어댑터 경로를 여기서 결정. 환경변수 또는 기본값:
  - STORAGE_DIR (기본: <BASE_DIR>/storage)
  - LOGS_DIR    (기본: <BASE_DIR>/logs)
  - FEEDBACK_LOG_PATH, TRAINING_DATA_DIR, ADAPTERS_DIR, BENCHMARK_DIR

절대경로 입력 시 그대로. 상대경로면 STORAGE_DIR 기준.

BASE_DIR 선택 순서:
  1. ORCHESTRATOR_BASE_DIR env
  2. vllm_orchestrator/ (auto-detect — 이 파일 기준 3단계 위)
"""
from __future__ import annotations

import os
from pathlib import Path


def base_dir() -> Path:
    v = os.getenv("ORCHESTRATOR_BASE_DIR")
    if v:
        return Path(v)
    # src/app/storage/paths.py → src/app/storage → src/app → src → vllm_orchestrator
    return Path(__file__).resolve().parent.parent.parent.parent


def _resolve(env_var: str, default_under_storage: str) -> Path:
    """env_var가 설정되어 있으면 그 값 (절대/상대 모두 지원).
    없으면 STORAGE_DIR/default_under_storage 기본값."""
    v = os.getenv(env_var)
    if v:
        p = Path(v)
        return p if p.is_absolute() else base_dir() / v
    storage = os.getenv("STORAGE_DIR")
    if storage:
        s = Path(storage)
        if not s.is_absolute():
            s = base_dir() / storage
    else:
        s = base_dir() / "storage"
    return s / default_under_storage


def storage_dir() -> Path:
    v = os.getenv("STORAGE_DIR")
    if v:
        p = Path(v)
        return p if p.is_absolute() else base_dir() / v
    return base_dir() / "storage"


def logs_dir() -> Path:
    v = os.getenv("LOGS_DIR")
    if v:
        p = Path(v)
        return p if p.is_absolute() else base_dir() / v
    return base_dir() / "logs"


def feedback_log_path() -> Path:
    v = os.getenv("FEEDBACK_LOG_PATH")
    if v:
        p = Path(v)
        return p if p.is_absolute() else base_dir() / v
    return logs_dir() / "feedback.jsonl"


def training_data_dir() -> Path:
    return _resolve("TRAINING_DATA_DIR", "training_data")


def adapters_dir() -> Path:
    return _resolve("ADAPTERS_DIR", "adapters")


def benchmark_dir() -> Path:
    return _resolve("BENCHMARK_DIR", "benchmark_scores")


def model_path() -> str:
    """LLM model path (local or HF hub name). 환경변수 우선."""
    return os.getenv("LLM_MODEL", "/models/Qwen2.5-14B-Instruct-AWQ")


def ensure_dirs() -> None:
    """모든 데이터 디렉토리를 생성 (idempotent). startup에서 호출."""
    for p in [storage_dir(), logs_dir(), training_data_dir(), adapters_dir(), benchmark_dir()]:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    # feedback 상위 디렉토리도
    try:
        feedback_log_path().parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
