"""
tests/generate_comparison.py - CPU vs GPU 비교 리포트 생성

실행: cd LLM && python -X utf8 -m runtime_llm_gateway.tests.generate_comparison

출력:
  baselines/comparison_report.json
  baselines/comparison_report.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from runtime_llm_gateway.core.acceptance_criteria import judge

BASELINE_DIR = Path(_ROOT) / "baselines" / "2026_03_30_cpu_qwen_0_5b"
CURRENT_DIR = Path(_ROOT) / "baselines" / "full_eval"
OUTPUT_DIR = Path(_ROOT) / "baselines"


def load_summary(path: Path) -> dict:
    # full_eval 40-case summary 우선
    f2 = path / "full_eval" / "summary.json"
    if f2.exists():
        return json.loads(f2.read_text(encoding="utf-8"))
    f = path / "summary.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


def generate():
    baseline = load_summary(BASELINE_DIR)
    current = load_summary(CURRENT_DIR)

    if not baseline or not current:
        print("[ERROR] Summary files not found")
        return

    # 판정
    judgment = judge(current)

    # Delta 계산
    delta_pass = current.get("total_pass", 0) - baseline.get("total_pass", 0)
    delta_rate = round(current.get("pass_rate", 0) - baseline.get("pass_rate", 0), 1)
    delta_p50 = current.get("p50_ms", 0) - baseline.get("p50_ms", 0)
    delta_p95 = current.get("p95_ms", 0) - baseline.get("p95_ms", 0)
    speedup_p50 = round(baseline.get("p50_ms", 1) / max(current.get("p50_ms", 1), 1), 1)

    # 프로그램별 delta
    prog_deltas = {}
    for prog in ["builder", "cad", "minecraft", "animation"]:
        bl = baseline.get("by_program", {}).get(prog, {})
        cur = current.get("by_program", {}).get(prog, {})
        prog_deltas[prog] = {
            "baseline_pass": f"{bl.get('pass', 0)}/{bl.get('total', 10)}",
            "current_pass": f"{cur.get('pass', 0)}/{cur.get('total', 10)}",
            "delta": cur.get("pass", 0) - bl.get("pass", 0),
            "baseline_p50": bl.get("p50_ms", 0),
            "current_p50": cur.get("p50_ms", 0),
        }

    report = {
        "baseline": {
            "label": "CPU Qwen2.5-0.5B",
            "date": baseline.get("date", ""),
            "pass": baseline.get("total_pass", 0),
            "total": baseline.get("total_cases", 0),
            "rate": baseline.get("pass_rate", 0),
            "p50_ms": baseline.get("p50_ms", 0),
            "p95_ms": baseline.get("p95_ms", 0),
        },
        "candidate": {
            "label": "Current",
            "date": current.get("date", ""),
            "pass": current.get("total_pass", 0),
            "total": current.get("total_cases", 0),
            "rate": current.get("pass_rate", 0),
            "p50_ms": current.get("p50_ms", 0),
            "p95_ms": current.get("p95_ms", 0),
        },
        "delta": {
            "pass": delta_pass,
            "rate": delta_rate,
            "p50_ms": delta_p50,
            "p95_ms": delta_p95,
            "speedup_p50": speedup_p50,
        },
        "per_program": prog_deltas,
        "judgment": judgment,
    }

    # JSON 저장
    with open(OUTPUT_DIR / "comparison_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Markdown 저장
    md = _render_md(report)
    with open(OUTPUT_DIR / "comparison_report.md", "w", encoding="utf-8") as f:
        f.write(md)

    print(md)


def _render_md(r: dict) -> str:
    bl = r["baseline"]
    ca = r["candidate"]
    d = r["delta"]
    j = r["judgment"]

    lines = [
        f"# Comparison Report: {bl['label']} vs {ca['label']}",
        "",
        "## Summary",
        "",
        f"| Metric | Baseline | Current | Delta |",
        f"|--------|----------|---------|-------|",
        f"| Pass | {bl['pass']}/{bl['total']} | {ca['pass']}/{ca['total']} | {d['pass']:+d} |",
        f"| Rate | {bl['rate']}% | {ca['rate']}% | {d['rate']:+.1f}% |",
        f"| p50 | {bl['p50_ms']}ms | {ca['p50_ms']}ms | {d['p50_ms']:+d}ms ({d['speedup_p50']}x) |",
        f"| p95 | {bl['p95_ms']}ms | {ca['p95_ms']}ms | {d['p95_ms']:+d}ms |",
        "",
        "## Per-Program",
        "",
        f"| Program | Baseline | Current | Delta | p50 BL | p50 Cur |",
        f"|---------|----------|---------|-------|--------|---------|",
    ]

    for prog, pd in r["per_program"].items():
        lines.append(
            f"| {prog} | {pd['baseline_pass']} | {pd['current_pass']} | {pd['delta']:+d} | {pd['baseline_p50']}ms | {pd['current_p50']}ms |"
        )

    lines += [
        "",
        f"## Verdict: **{j['verdict']}**",
        "",
    ]

    if j["checks"]:
        lines.append("### Passed")
        for c in j["checks"]:
            lines.append(f"- {c}")

    if j["warnings"]:
        lines.append("")
        lines.append("### Warnings")
        for w in j["warnings"]:
            lines.append(f"- {w}")

    if j["blockers"]:
        lines.append("")
        lines.append("### Blockers")
        for b in j["blockers"]:
            lines.append(f"- {b}")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    generate()
