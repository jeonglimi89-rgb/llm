"""
test_false_positive_analyzer.py — review/false_positive_analyzer.py 통합 테스트

실제 runtime/manifests/ + runtime/human_review/review_data.json 를 읽어서
analyzer 가 다음을 만족하는지 검증한다:

  1. 현재 manifests/ 디렉터리 (12개) 를 모두 카운트
  2. cad concentration ≥ 60% (현재 75%)
  3. duplicate_param_groups ≥ 1 (cad.generate_part name=bracket 9회 등)
  4. 12 HR 케이스 분석 결과 false_positive_count ≥ 8
  5. 최소 5종류의 failure_category 등장
  6. 보고서가 디스크에 쓰이고 다시 읽힘
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.review.false_positive_analyzer import (
    analyze_manifests, analyze_human_review, build_report, write_report,
)


def _manifest_dir() -> Path:
    return _ROOT / "runtime" / "manifests"


def _hr_path() -> Path:
    return _ROOT / "runtime" / "human_review" / "review_data.json"


def test_manifest_analysis_real_dir():
    print("  [1] manifest analyzer reads real runtime/manifests/")
    summary = analyze_manifests(_manifest_dir())
    assert summary.total >= 1, "no manifests at all — fixture missing?"
    assert "by_tool" in summary.__dict__
    assert summary.cad_concentration_pct >= 0
    print(f"    OK: {summary.total} manifests, cad={summary.cad_concentration_pct}%")


def test_manifest_cad_concentration():
    print("  [2] cad concentration is high (≥60%)")
    summary = analyze_manifests(_manifest_dir())
    if summary.total < 5:
        print("    SKIP (too few manifests)")
        return
    assert summary.cad_concentration_pct >= 60.0, (
        f"cad share dropped to {summary.cad_concentration_pct}%"
    )
    print(f"    OK: cad={summary.cad_concentration_pct}%")


def test_manifest_duplicate_param_groups():
    print("  [3] duplicate (tool,params) signatures detected")
    summary = analyze_manifests(_manifest_dir())
    if summary.total < 2:
        print("    SKIP")
        return
    assert summary.duplicate_param_groups >= 1, (
        "expected at least one repeated (tool,params) signature"
    )
    print(f"    OK: {summary.duplicate_param_groups} duplicate groups")


def test_hr_analysis_blocks_known_false_positives():
    """Robust to both pre-fix loose state AND post-fix strict state.

    Original phrasing assumed the on-disk review_data.json still had loose
    ``auto_validated:true`` from the pre-04-06 dispatcher; ``is_false_positive``
    only fires on a True→False *transition*. After 2026-04-06 the dispatcher
    writes strict ``validated`` directly, so a re-export produces a file where
    the bad cases are *already* False — no transition, fp count 0.

    Either way the **invariant we actually want** is the same: the analyzer
    must report ≥10 categorized failures across HR-001..HR-012. We assert that.
    The 'false positive transition' count is reported in addition.
    """
    print("  [4] HR analysis surfaces ≥10 categorized failures")
    cases = analyze_human_review(_hr_path())
    if not cases:
        print("    SKIP (review_data.json missing)")
        return
    fp_transition = sum(1 for c in cases if c.is_false_positive)
    flagged_now = sum(1 for c in cases if c.failure_categories)
    not_validated_now = sum(1 for c in cases if not c.new_auto_validated)
    assert flagged_now >= 10, (
        f"only {flagged_now}/{len(cases)} cases have failure categories — "
        f"strict gate appears weakened"
    )
    assert not_validated_now >= 10, (
        f"only {not_validated_now}/{len(cases)} cases now lack auto_validated — "
        f"strict gate appears weakened"
    )
    print(
        f"    OK: flagged={flagged_now}/{len(cases)} "
        f"strict-reject={not_validated_now}/{len(cases)} "
        f"transition-fp={fp_transition}/{len(cases)}"
    )


def test_hr_analysis_per_case_specific_failures():
    print("  [5] specific HR cases get the right failure category")
    cases = analyze_human_review(_hr_path())
    if not cases:
        print("    SKIP")
        return
    by_id = {c.case_id: c for c in cases}

    # HR-001 must show wrong_key_locale
    if "HR-001" in by_id:
        assert "wrong_key_locale" in by_id["HR-001"].failure_categories, by_id["HR-001"].failure_categories
    # HR-004 must show validator_shaped_response
    if "HR-004" in by_id:
        assert "validator_shaped_response" in by_id["HR-004"].failure_categories
    # HR-008 must show css_property_leak
    if "HR-008" in by_id:
        assert "css_property_leak" in by_id["HR-008"].failure_categories
    # HR-011 must show hallucinated_external_reference
    if "HR-011" in by_id:
        assert "hallucinated_external_reference" in by_id["HR-011"].failure_categories
    # HR-012 must show semantic_mistranslation OR wrong_language
    if "HR-012" in by_id:
        cats = set(by_id["HR-012"].failure_categories)
        assert ({"semantic_mistranslation", "wrong_language"} & cats), cats

    print("    OK: per-case categories match")


def test_full_report_categories_diversity():
    print("  [6] full report has multiple failure categories")
    report = build_report(_manifest_dir(), _hr_path())
    assert report.total_hr >= 1
    cats = set(report.by_failure_category.keys())
    assert len(cats) >= 5, f"only {cats} found"
    print(f"    OK: {len(cats)} categories: {sorted(cats)}")


def test_report_writes_and_reloads(tmp_path_dir: Path | None = None):
    print("  [7] report writes and reloads from disk")
    out = _ROOT / "runtime" / "false_positive_report_test.json"
    try:
        report = build_report(_manifest_dir(), _hr_path())
        write_report(report, out)
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert "summary" in loaded
        assert loaded["summary"]["total_hr"] == report.total_hr
        assert "by_failure_category" in loaded
        print(f"    OK: round-trip ok, total_hr={loaded['summary']['total_hr']}")
    finally:
        if out.exists():
            out.unlink()


TESTS = [
    test_manifest_analysis_real_dir,
    test_manifest_cad_concentration,
    test_manifest_duplicate_param_groups,
    test_hr_analysis_blocks_known_false_positives,
    test_hr_analysis_per_case_specific_failures,
    test_full_report_categories_diversity,
    test_report_writes_and_reloads,
]


if __name__ == "__main__":
    print("=" * 60)
    print("False positive analyzer integration tests")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\nResults: {passed}/{len(TESTS)} passed")
