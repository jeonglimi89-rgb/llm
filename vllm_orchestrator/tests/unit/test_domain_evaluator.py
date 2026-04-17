"""test_domain_evaluator.py — Domain Evaluator scoring tests."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.domain.profiles import load_domain_profiles
from src.app.orchestration.domain_router import DomainRouter
from src.app.orchestration.requirement_extractor import RequirementExtractor
from src.app.review.domain_evaluator import DomainEvaluator

CONFIGS = Path(__file__).resolve().parent.parent.parent / "configs"
_profiles = load_domain_profiles(CONFIGS)
_router = DomainRouter(_profiles)
_extractor = RequirementExtractor(_profiles)
_evaluator = DomainEvaluator(_profiles)


def test_evaluator_detects_missing_constraints():
    classification = _router.route("120x80mm 알루미늄 방수 케이스 설계")
    envelope = _extractor.extract("120x80mm 알루미늄 방수 케이스 설계", "cad", "constraint_parse")
    # Output missing the dimensions and material
    slots = {"constraints": [{"constraint_type": "기타"}]}
    ev = _evaluator.evaluate(classification, envelope, _profiles["cad"], slots)
    assert ev.constraint_coverage < 1.0, f"should have low coverage: {ev.constraint_coverage}"
    assert len(ev.missing_constraints) >= 1, f"should have missing: {ev.missing_constraints}"
    assert len(ev.issues) >= 1, f"should flag constraint_coverage issue: {ev.issues}"


def test_evaluator_catches_generic_output():
    classification = _router.route("방수 제품 설계")
    envelope = _extractor.extract("방수 제품 설계", "cad", "constraint_parse")
    # Generic / placeholder output
    slots = {"result": "일반적으로 방수 제품은 IP67이 좋습니다. 추후 결정."}
    ev = _evaluator.evaluate(classification, envelope, _profiles["cad"], slots)
    assert ev.genericness_penalty > 0, f"should detect generic, got {ev.genericness_penalty}"
    assert any("generic" in i for i in ev.issues) or ev.genericness_penalty > 0


def test_evaluator_good_output_passes():
    classification = _router.route("방수 샤워필터 설계")
    envelope = _extractor.extract("방수 샤워필터 설계", "cad", "constraint_parse")
    # Good domain output
    slots = {
        "constraints": [
            {"constraint_type": "방수", "description": "IP67 방수 처리", "category": "기계"}
        ],
        "systems": [{"name": "mechanical"}, {"name": "plumbing"}],
    }
    ev = _evaluator.evaluate(classification, envelope, _profiles["cad"], slots)
    assert ev.overall_score > 0.3
    assert ev.output_schema_compliance > 0


def test_evaluator_returns_structured_dict():
    classification = _router.route("제품 설계")
    envelope = _extractor.extract("제품 설계", "cad", "constraint_parse")
    slots = {"constraints": []}
    ev = _evaluator.evaluate(classification, envelope, _profiles["cad"], slots)
    d = ev.to_dict()
    assert "scores" in d
    assert "pass" in d
    assert "repair_applied" in d
    assert "issues" in d
    assert isinstance(d["scores"], dict)
    assert "genericness_penalty" in d["scores"]
