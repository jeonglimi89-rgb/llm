"""
test_known_failures.py — datasets/known_failures.json 회귀 테스트

datasets/known_failures.json 에 보관된 모든 케이스 (HR-001..HR-012 + DG-* )
를 layered review gate 에 다시 통과시켜, 다음을 보장한다:

  1. expected_final_judgment 가 "fail" 인 케이스는 새 게이트로도 fail/needs_review 여야 한다 (즉 auto_validated == False).
  2. expected_final_judgment 가 "pass" 인 positive control 은 여전히 pass 여야 한다.
  3. expected_failure_categories 가 지정돼 있으면, 새 게이트가 그 카테고리들을 *모두* 보고해야 한다 (subset 매칭).

이 테스트가 깨지면:
  - 게이트가 약화돼 false positive 가 다시 통과한다 (1번/3번)
  - 게이트가 과도하게 빡세져 정상 출력도 fail 한다 (2번)

두 방향 모두 회귀이므로 절대 실패 케이스를 마음대로 dataset 에서 빼지 말 것.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from src.app.review.task_contracts import evaluate_task_contract  # noqa: E402

CORPUS_PATH = _ROOT / "datasets" / "known_failures.json"


def _load_corpus() -> list[dict]:
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def _evaluate_case(case: dict):
    task_type = f"{case['domain']}.{case['task']}"
    return evaluate_task_contract(
        task_type=task_type,
        user_input=case.get("user_input", ""),
        payload=case.get("parsed_payload"),
        schema_validated=True,
        artifact_id=case["id"],
    )


def test_corpus_loads():
    print("  [1] corpus loads")
    cases = _load_corpus()
    assert len(cases) >= 17, f"corpus shrank: {len(cases)} cases"
    ids = {c["id"] for c in cases}
    # Hard requirement: HR-001..HR-012 must all be present
    for i in range(1, 13):
        cid = f"HR-{i:03d}"
        assert cid in ids, f"missing {cid}"
    # Reconstructed D-grade cases
    for cid in (
        "DG-builder-patch_parse-01",
        "DG-cad-constraint-01",
        "DG-minecraft-style-01",
        "DG-animation-camera-url-01",
        "DG-animation-lighting-en-01",
    ):
        assert cid in ids, f"missing {cid}"
    print(f"    OK: {len(cases)} cases loaded ({len(ids)} unique ids)")


def test_negative_cases_blocked():
    """expected_final_judgment=fail 케이스는 새 게이트로 절대 통과하면 안 된다."""
    print("  [2] negative cases blocked")
    cases = _load_corpus()
    failures: list[str] = []
    for c in cases:
        if c.get("expected_final_judgment") != "fail":
            continue
        j = _evaluate_case(c)
        if j.auto_validated:
            failures.append(f"{c['id']} auto_validated=True (should be False)")
        if j.final_judgment == "pass":
            failures.append(f"{c['id']} final_judgment=pass (should not pass)")
    assert not failures, "regression: " + " | ".join(failures)
    n_neg = sum(1 for c in cases if c.get("expected_final_judgment") == "fail")
    print(f"    OK: {n_neg} negative cases all blocked")


def test_positive_controls_pass():
    """positive control (HR-003, HR-005) 는 여전히 pass 해야 한다."""
    print("  [3] positive controls pass")
    cases = _load_corpus()
    failures: list[str] = []
    for c in cases:
        if c.get("expected_final_judgment") != "pass":
            continue
        j = _evaluate_case(c)
        if not j.auto_validated:
            failures.append(
                f"{c['id']} auto_validated=False (should be True). "
                f"final={j.final_judgment} cats={j.failure_categories}"
            )
        if j.final_judgment != "pass":
            failures.append(f"{c['id']} final_judgment={j.final_judgment} (should be pass)")
    assert not failures, "regression: " + " | ".join(failures)
    n_pos = sum(1 for c in cases if c.get("expected_final_judgment") == "pass")
    print(f"    OK: {n_pos} positive controls all passed")


def test_expected_failure_categories():
    """expected_failure_categories 가 지정돼 있으면 새 게이트가 그것들을 모두 보고해야 한다."""
    print("  [4] expected failure categories present")
    cases = _load_corpus()
    failures: list[str] = []
    for c in cases:
        expected = c.get("expected_failure_categories")
        if not expected:
            continue
        j = _evaluate_case(c)
        observed = set(j.failure_categories)
        missing = [cat for cat in expected if cat not in observed]
        if missing:
            failures.append(
                f"{c['id']} missing categories {missing}. "
                f"observed={sorted(observed)}"
            )
    assert not failures, "regression: " + " | ".join(failures)
    print(f"    OK: every expected failure category was reported")


def test_severity_ranking():
    """fail 판정 케이스의 severity 가 medium/high/critical 안에 들어야 한다 (info 면 안 됨)."""
    print("  [5] severity ranking")
    cases = _load_corpus()
    bad: list[str] = []
    for c in cases:
        if c.get("expected_final_judgment") != "fail":
            continue
        j = _evaluate_case(c)
        if j.severity in ("info", "low"):
            bad.append(f"{c['id']} severity={j.severity} too low for fail")
    assert not bad, " | ".join(bad)
    print("    OK: all fail cases have severity >= medium")


TESTS = [
    test_corpus_loads,
    test_negative_cases_blocked,
    test_positive_controls_pass,
    test_expected_failure_categories,
    test_severity_ranking,
]


if __name__ == "__main__":
    print("=" * 60)
    print("Known-failure regression tests")
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
    if passed == len(TESTS):
        print("ALL REGRESSION TESTS PASSED!")
