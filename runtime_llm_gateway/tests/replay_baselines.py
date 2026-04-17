"""
tests/replay_baselines.py - Baseline replay + 비교

기존 baseline을 현재 서버 설정으로 다시 실행하고 비교.
GPU 서버 교체 후 이것만 돌리면 된다.

실행: cd LLM && python -X utf8 -m runtime_llm_gateway.tests.replay_baselines
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.config_loader import build_gateway
from runtime_llm_gateway.core.envelope import RequestEnvelope, Message


BASELINE_DIR = Path(_ROOT) / "baselines" / "2026_03_30_cpu_qwen_0_5b"


def load_baselines() -> list[dict]:
    """baseline input 파일들 로드"""
    cases = []
    for f in sorted(BASELINE_DIR.glob("*_input.json")):
        name = f.stem.replace("_input", "")
        output_f = BASELINE_DIR / f"{name}_output.json"
        input_data = json.loads(f.read_text(encoding="utf-8"))
        output_data = json.loads(output_f.read_text(encoding="utf-8")) if output_f.exists() else {}
        cases.append({
            "name": name,
            "input": input_data,
            "baseline_output": output_data,
        })
    return cases


def replay_and_compare():
    """현재 서버 설정으로 baseline 재실행 + 비교"""
    gw = build_gateway()
    cases = load_baselines()

    if not cases:
        print("[SKIP] No baselines found in", BASELINE_DIR)
        return

    print(f"{'Task':<35} {'Baseline':>10} {'Current':>10} {'SpeedUp':>10} {'Schema':>8} {'Domain':>8}")
    print("-" * 85)

    for case in cases:
        inp = case["input"]
        bl = case["baseline_output"]
        bl_ms = bl.get("_latency_ms", bl.get("latency_ms", 0))

        req = RequestEnvelope(
            task_type=inp["task_type"],
            project_id="replay",
            session_id="replay",
            messages=[Message(role="user", content=inp["text"])],
            schema_id=inp["schema_id"],
        )

        start = time.time()
        resp = gw.process(req)
        cur_ms = int((time.time() - start) * 1000)

        speedup = f"{bl_ms / cur_ms:.1f}x" if cur_ms > 0 else "N/A"
        schema = "OK" if resp.validation.schema_ok else "FAIL"
        domain = "OK" if resp.validation.domain_ok else "FAIL"

        print(f"{inp['task_type']:<35} {bl_ms:>8}ms {cur_ms:>8}ms {speedup:>10} {schema:>8} {domain:>8}")

        # 현재 결과도 저장
        out_dir = Path(_ROOT) / "baselines" / "latest_replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / f"{case['name']}_output.json", "w", encoding="utf-8") as f:
            data = resp.to_dict()
            data["_latency_ms"] = cur_ms
            json.dump(data, f, ensure_ascii=False, indent=2)

    print()
    print(f"[INFO] Replay results saved to baselines/latest_replay/")


if __name__ == "__main__":
    print("=" * 85)
    print("Baseline Replay & Comparison")
    print("=" * 85)
    replay_and_compare()
    print("=" * 85)
