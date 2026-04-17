"""tools/manifest_writer.py - 실제 엔진 대신 job manifest 파일 생성"""
from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path

_MANIFEST_DIR = Path(__file__).resolve().parent.parent.parent.parent / "runtime" / "manifests"


def write_manifest(tool_name: str, params: dict) -> str:
    """manifest JSON 파일 생성. 나중에 엔진이 이 파일을 읽어서 실행."""
    os.makedirs(_MANIFEST_DIR, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{tool_name.replace('.', '_')}_{ts}.json"
    path = _MANIFEST_DIR / filename

    manifest = {
        "tool": tool_name,
        "params": params,
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return str(path)
