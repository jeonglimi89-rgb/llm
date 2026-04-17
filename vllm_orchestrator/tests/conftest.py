import os
import sys
from pathlib import Path

# src/ 도 importable (app.* 와 src.app.* 둘 다 시도)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# 기본 환경: 재현 가능한 테스트를 위해 외부 의존 최소화
os.environ.setdefault("REQUEST_CACHE_DISABLED", "0")
os.environ.setdefault("REQUEST_CACHE_BACKEND", "memory")
os.environ.setdefault("SEMANTIC_CACHE_DISABLED", "0")
os.environ.setdefault("LLM_CRITIC_MODE", "off")
os.environ.setdefault("VARIANT_SAMPLING_DISABLED", "1")
os.environ.setdefault("OTEL_ENABLED", "0")
os.environ.setdefault("API_KEY_REQUIRED", "0")
