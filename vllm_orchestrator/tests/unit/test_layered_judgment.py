"""
test_layered_judgment.py — review/layered.py 단위 테스트

5개 게이트가 어떻게 final_judgment / auto_validated 로 합성되는지 검증.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.review.layered import (
    LayeredJudgment, GateResult, FailureCategory, LayeredVerdict,
    compose_judgment, passing_judgment, severity_max,
)
from src.app.review.judgment import Severity


def _gate(name, passed, sev=Severity.INFO.value, cats=None):
    return GateResult(name=name, passed=passed, severity=sev, failure_categories=list(cats or []))


def _all_pass_gates():
    return [
        _gate("schema", True),
        _gate("language", True),
        _gate("semantic", True),
        _gate("domain_guard", True),
        _gate("contract", True),
    ]


def test_all_pass_yields_pass():
    print("  [1] all gates pass → PASS + auto_validated=True")
    j = compose_judgment("a1", "builder", "builder.x", _all_pass_gates())
    assert j.auto_validated is True
    assert j.final_judgment == "pass"
    assert j.severity == "info"
    assert j.failure_categories == []
    print("    OK")


def test_schema_fail_forces_fail():
    print("  [2] schema fail → final FAIL no matter what")
    gates = _all_pass_gates()
    gates[0] = _gate("schema", False, Severity.CRITICAL.value, [FailureCategory.SCHEMA_FAILURE.value])
    j = compose_judgment("a2", "cad", "cad.x", gates)
    assert j.final_judgment == "fail"
    assert j.auto_validated is False
    assert "schema_failure" in j.failure_categories
    print("    OK")


def test_critical_in_any_gate_fails():
    print("  [3] critical severity in any gate → FAIL")
    gates = _all_pass_gates()
    gates[3] = _gate("domain_guard", False, Severity.CRITICAL.value,
                     [FailureCategory.HALLUCINATED_EXTERNAL_REFERENCE.value])
    j = compose_judgment("a3", "animation", "animation.x", gates)
    assert j.final_judgment == "fail"
    assert j.auto_validated is False
    print("    OK")


def test_high_severity_fails():
    print("  [4] high severity → FAIL")
    gates = _all_pass_gates()
    gates[4] = _gate("contract", False, Severity.HIGH.value,
                     [FailureCategory.TASK_CONTRACT_VIOLATION.value])
    j = compose_judgment("a4", "minecraft", "minecraft.x", gates)
    assert j.final_judgment == "fail"
    print("    OK")


def test_medium_severity_needs_review():
    print("  [5] medium severity → NEEDS_REVIEW")
    gates = _all_pass_gates()
    gates[2] = _gate("semantic", False, Severity.MEDIUM.value, ["semantic_mistranslation"])
    j = compose_judgment("a5", "animation", "animation.x", gates)
    assert j.final_judgment == "needs_review"
    assert j.auto_validated is False
    print("    OK")


def test_low_severity_needs_review():
    print("  [6] low severity → NEEDS_REVIEW")
    gates = _all_pass_gates()
    gates[1] = _gate("language", False, Severity.LOW.value, ["wrong_language"])
    j = compose_judgment("a6", "builder", "builder.x", gates)
    assert j.final_judgment == "needs_review"
    print("    OK")


def test_failed_gate_with_info_severity_needs_review():
    """edge case: gate failed but severity stayed info → 보수적으로 needs_review."""
    print("  [7] failed gate but severity=info → NEEDS_REVIEW (conservative)")
    gates = _all_pass_gates()
    gates[2] = _gate("semantic", False, Severity.INFO.value, [])
    j = compose_judgment("a7", "cad", "cad.x", gates)
    assert j.final_judgment == "needs_review"
    assert j.auto_validated is False  # any False gate prevents auto_validated
    print("    OK")


def test_evidence_carries_gate_name():
    print("  [8] evidence is annotated with gate name")
    g = _gate("language", False, Severity.HIGH.value, ["wrong_key_locale"])
    g.evidence = [{"path": "$.楼层", "key": "楼层"}]
    gates = _all_pass_gates()
    gates[1] = g
    j = compose_judgment("a8", "builder", "builder.x", gates)
    assert any(e.get("gate") == "language" for e in j.evidence)
    print("    OK")


def test_to_dict_serialization_complete():
    print("  [9] to_dict has all required keys")
    j = passing_judgment("a9", "builder", "builder.x", parsed_payload={"x": 1})
    d = j.to_dict()
    for k in (
        "schema_validated", "language_validated", "semantic_validated",
        "domain_guard_validated", "contract_validated", "auto_validated",
        "final_judgment", "severity", "failure_categories", "rationale",
        "evidence", "gates",
    ):
        assert k in d, f"missing {k}"
    j2 = json.loads(j.to_json())
    assert j2["auto_validated"] is True
    print("    OK")


def test_severity_max_helper():
    print("  [10] severity_max ranks correctly")
    assert severity_max(["info", "low", "high", "medium"]) == "high"
    assert severity_max(["info"]) == "info"
    assert severity_max(["critical", "low"]) == "critical"
    assert severity_max([]) == "info"
    print("    OK")


def test_passing_judgment_helper():
    print("  [11] passing_judgment helper produces clean PASS")
    j = passing_judgment("a11", "minecraft", "minecraft.x")
    assert j.auto_validated is True
    assert j.final_judgment == "pass"
    assert j.severity == "info"
    assert all(g.passed for g in j.gates)
    print("    OK")


def test_multiple_failure_categories_merge():
    print("  [12] multiple gates with categories merge into list")
    gates = _all_pass_gates()
    gates[1] = _gate("language", False, Severity.HIGH.value, ["wrong_language"])
    gates[2] = _gate("semantic", False, Severity.HIGH.value, ["semantic_mistranslation"])
    j = compose_judgment("a12", "animation", "animation.x", gates)
    assert "wrong_language" in j.failure_categories
    assert "semantic_mistranslation" in j.failure_categories
    assert j.final_judgment == "fail"
    print("    OK")


TESTS = [
    test_all_pass_yields_pass,
    test_schema_fail_forces_fail,
    test_critical_in_any_gate_fails,
    test_high_severity_fails,
    test_medium_severity_needs_review,
    test_low_severity_needs_review,
    test_failed_gate_with_info_severity_needs_review,
    test_evidence_carries_gate_name,
    test_to_dict_serialization_complete,
    test_severity_max_helper,
    test_passing_judgment_helper,
    test_multiple_failure_categories_merge,
]


if __name__ == "__main__":
    print("=" * 60)
    print("layered judgment unit tests")
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
