"""
review/false_positive_analyzer.py — runtime artifact 의 false positive 분석

목적
----
2026-04-04 시점 vllm_orchestrator/runtime/ 아래의 두 종류 artifact 를
정량 분석한다:

  1. runtime/manifests/*.json
       - tools/manifest_writer.py 가 쓴 pending tool job
       - 어떤 tool 이 얼마나 호출됐는지, cad 편중 원인 등 빈도 분석

  2. runtime/human_review/review_data.json
       - export_human_review.py 가 만든 LLM 슬롯 추출 케이스 (HR-001..HR-012)
       - 모두 ``auto_validated: true`` 였으나 실제로는 false positive 다수

분석 결과는 runtime/false_positive_report.json 으로 저장하고, 동시에 인메모리
``FalsePositiveReport`` 객체를 반환한다.

CLI 진입점은 vllm_orchestrator/scripts/analyze_review_artifacts.py 에 있다.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC, date
from pathlib import Path
from typing import Any, Optional

from .task_contracts import evaluate_task_contract
from .layered import LayeredJudgment, FailureCategory, LayeredVerdict


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HRCaseAnalysis:
    """단일 HR 케이스 재평가 결과"""
    case_id: str
    domain: str
    task: str
    input: str
    old_auto_validated: bool        # review_data.json 에 적혀 있던 값
    new_auto_validated: bool        # 새 layered judgment 결과
    new_final_judgment: str         # pass / needs_review / fail
    new_severity: str
    failure_categories: list[str]
    rationale: str
    is_false_positive: bool         # old=True, new!=pass


@dataclass
class ManifestSummary:
    """manifests/ 디렉터리 빈도 요약"""
    total: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    by_date: dict[str, int] = field(default_factory=dict)        # YYYY-MM-DD
    by_params_signature: dict[str, int] = field(default_factory=dict)
    cad_concentration_pct: float = 0.0
    duplicate_param_groups: int = 0
    note: str = ""


@dataclass
class FalsePositiveReport:
    generated_at: str
    manifests: ManifestSummary
    hr_cases: list[HRCaseAnalysis]
    total_hr: int = 0
    old_pass_count: int = 0          # old auto_validated=True
    new_pass_count: int = 0          # new auto_validated=True
    false_positive_count: int = 0    # old True, new not pass
    by_failure_category: dict[str, int] = field(default_factory=dict)
    by_domain_failure: dict[str, dict[str, int]] = field(default_factory=dict)
    by_task_failure: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "manifests": asdict(self.manifests),
            "summary": {
                "total_hr": self.total_hr,
                "old_pass_count": self.old_pass_count,
                "new_pass_count": self.new_pass_count,
                "false_positive_count": self.false_positive_count,
                "false_positive_rate": (
                    round(self.false_positive_count / self.total_hr, 3)
                    if self.total_hr else 0.0
                ),
            },
            "by_failure_category": self.by_failure_category,
            "by_domain_failure": self.by_domain_failure,
            "by_task_failure": self.by_task_failure,
            "hr_cases": [asdict(c) for c in self.hr_cases],
        }


# ---------------------------------------------------------------------------
# Manifest analysis
# ---------------------------------------------------------------------------

# 파일명: cad_generate_part_20260401_064128_566437.json
_MANIFEST_NAME_RE = re.compile(
    r"^(?P<tool>[a-z][a-z_]*)_(?P<date>\d{8})_(?P<time>\d{6})_\d+\.json$"
)


def _parse_manifest_filename(name: str) -> Optional[dict[str, str]]:
    m = _MANIFEST_NAME_RE.match(name)
    if not m:
        return None
    return {"tool": m["tool"], "date": m["date"], "time": m["time"]}


def analyze_manifests(manifests_dir: Path, *, since: Optional[date] = None) -> ManifestSummary:
    """runtime/manifests/ 디렉터리 분석.

    Parameters
    ----------
    since : 이 날짜 *이상* 의 manifest 만 카운트 (default: 2026-04-01)
    """
    if since is None:
        since = date(2026, 4, 1)

    summary = ManifestSummary()
    if not manifests_dir.exists():
        summary.note = f"manifests dir not found: {manifests_dir}"
        return summary

    by_tool: Counter[str] = Counter()
    by_domain: Counter[str] = Counter()
    by_date: Counter[str] = Counter()
    by_sig: Counter[str] = Counter()

    for path in sorted(manifests_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # 파일명에서 timestamp 추출
        meta = _parse_manifest_filename(path.name)
        if meta:
            try:
                d = datetime.strptime(meta["date"], "%Y%m%d").date()
            except ValueError:
                d = None
            if d and d < since:
                continue
            date_str = d.isoformat() if d else "unknown"
        else:
            date_str = "unknown"

        tool = data.get("tool", "unknown")
        domain = tool.split(".")[0] if "." in tool else tool
        by_tool[tool] += 1
        by_domain[domain] += 1
        by_date[date_str] += 1

        # params signature: 같은 파라미터 반복 (test:true, name:bracket 등) 추적
        params = data.get("params", {})
        try:
            sig = json.dumps(params, ensure_ascii=False, sort_keys=True)
        except Exception:
            sig = str(params)
        by_sig[f"{tool}::{sig}"] += 1

    summary.total = sum(by_tool.values())
    summary.by_tool = dict(by_tool)
    summary.by_domain = dict(by_domain)
    summary.by_date = dict(by_date)
    summary.by_params_signature = dict(by_sig)

    # cad concentration
    if summary.total > 0:
        cad_total = by_domain.get("cad", 0)
        summary.cad_concentration_pct = round(cad_total * 100.0 / summary.total, 1)

    # duplicate count: 같은 (tool, params) 가 2회 이상 등장한 그룹 수
    summary.duplicate_param_groups = sum(1 for v in by_sig.values() if v >= 2)

    return summary


# ---------------------------------------------------------------------------
# Human-review analysis
# ---------------------------------------------------------------------------

def analyze_human_review(review_data_path: Path) -> list[HRCaseAnalysis]:
    """review_data.json 의 각 케이스를 layered judgment 로 재평가."""
    if not review_data_path.exists():
        return []

    try:
        data = json.loads(review_data_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    out: list[HRCaseAnalysis] = []
    for entry in data:
        case_id = entry.get("case_id", "")
        domain = entry.get("domain", "")
        task = entry.get("task", "")
        user_input = entry.get("input", "")
        old_validated = bool(entry.get("auto_validated", False))
        payload = entry.get("parsed_slots")

        task_type = f"{domain}.{task}"
        judgment = evaluate_task_contract(
            task_type=task_type,
            user_input=user_input,
            payload=payload,
            schema_validated=True,   # parser 가 성공했으므로 schema 단계는 통과
            artifact_id=case_id,
        )

        is_fp = old_validated and not judgment.auto_validated
        out.append(HRCaseAnalysis(
            case_id=case_id,
            domain=domain,
            task=task,
            input=user_input,
            old_auto_validated=old_validated,
            new_auto_validated=judgment.auto_validated,
            new_final_judgment=judgment.final_judgment,
            new_severity=judgment.severity,
            failure_categories=list(judgment.failure_categories),
            rationale=judgment.rationale,
            is_false_positive=is_fp,
        ))

    return out


# ---------------------------------------------------------------------------
# Build full report
# ---------------------------------------------------------------------------

def build_report(
    manifests_dir: Path,
    review_data_path: Path,
    *,
    since: Optional[date] = None,
) -> FalsePositiveReport:
    """전체 분석 → FalsePositiveReport"""
    m = analyze_manifests(manifests_dir, since=since)
    cases = analyze_human_review(review_data_path)

    by_cat: Counter[str] = Counter()
    by_dom_fail: dict[str, Counter[str]] = defaultdict(Counter)
    by_task_fail: dict[str, Counter[str]] = defaultdict(Counter)

    old_pass = sum(1 for c in cases if c.old_auto_validated)
    new_pass = sum(1 for c in cases if c.new_auto_validated)
    fp_count = sum(1 for c in cases if c.is_false_positive)

    for c in cases:
        for cat in c.failure_categories:
            by_cat[cat] += 1
            by_dom_fail[c.domain][cat] += 1
            by_task_fail[f"{c.domain}.{c.task}"][cat] += 1

    return FalsePositiveReport(
        generated_at=datetime.now(UTC).isoformat(),
        manifests=m,
        hr_cases=cases,
        total_hr=len(cases),
        old_pass_count=old_pass,
        new_pass_count=new_pass,
        false_positive_count=fp_count,
        by_failure_category=dict(by_cat),
        by_domain_failure={k: dict(v) for k, v in by_dom_fail.items()},
        by_task_failure={k: dict(v) for k, v in by_task_fail.items()},
    )


def write_report(report: FalsePositiveReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
