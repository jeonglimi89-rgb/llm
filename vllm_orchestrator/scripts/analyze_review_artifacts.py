"""
analyze_review_artifacts.py — runtime artifact false-positive 분석 CLI

실행:
    cd vllm_orchestrator
    python scripts/analyze_review_artifacts.py

기본 입출력
-----------
- runtime/manifests/                       (since 2026-04-01)
- runtime/human_review/review_data.json
- 출력: runtime/false_positive_report.json
- 콘솔: 핵심 요약 (false positive 개수, 카테고리 분포, manifest 편중 등)
"""
from __future__ import annotations

import sys
import json
from datetime import date
from pathlib import Path

# 프로젝트 루트 sys.path 등록
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.review.false_positive_analyzer import build_report, write_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    manifests_dir = _ROOT / "runtime" / "manifests"
    review_path = _ROOT / "runtime" / "human_review" / "review_data.json"
    out_path = _ROOT / "runtime" / "false_positive_report.json"
    since_str = None

    # 매우 간단한 인자 파싱
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--manifests" and i + 1 < len(argv):
            manifests_dir = Path(argv[i + 1]); i += 2
        elif a == "--review" and i + 1 < len(argv):
            review_path = Path(argv[i + 1]); i += 2
        elif a == "--out" and i + 1 < len(argv):
            out_path = Path(argv[i + 1]); i += 2
        elif a == "--since" and i + 1 < len(argv):
            since_str = argv[i + 1]; i += 2
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"unknown arg: {a}")
            return 2

    since = None
    if since_str:
        try:
            since = date.fromisoformat(since_str)
        except ValueError:
            print(f"--since must be YYYY-MM-DD, got: {since_str}")
            return 2

    report = build_report(manifests_dir, review_path, since=since)
    write_report(report, out_path)

    # 콘솔 요약
    print("=" * 64)
    print("False Positive Analysis Report")
    print("=" * 64)
    print(f"output: {out_path}")
    print()
    print(f"manifests scanned        : {report.manifests.total}")
    print(f"  by tool                : {report.manifests.by_tool}")
    print(f"  cad concentration %    : {report.manifests.cad_concentration_pct}")
    print(f"  duplicate param groups : {report.manifests.duplicate_param_groups}")
    print(f"  by date                : {report.manifests.by_date}")
    print()
    print(f"HR cases analyzed        : {report.total_hr}")
    print(f"  old auto_validated=True: {report.old_pass_count}")
    print(f"  new auto_validated=True: {report.new_pass_count}")
    print(f"  false positives        : {report.false_positive_count}")
    print()
    print("failure category counts:")
    for cat, n in sorted(report.by_failure_category.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:42s} {n}")
    print()
    print("by-task failure counts:")
    for task, cats in sorted(report.by_task_failure.items()):
        cat_str = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        print(f"  {task:42s} {cat_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
