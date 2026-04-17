"""
review/domain_evaluator.py — 도메인 전문성 사후 평가.

기존 5-gate review 이후 실행되는 6번째 레이어.
기존 gate 를 무변경으로 유지하면서 도메인 전문성을 추가 평가.
모든 체크는 rule-based (LLM 0회).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..domain.profiles import DomainProfile
from ..orchestration.domain_classifier import ClassificationResult
from ..orchestration.requirement_extractor import RequirementEnvelope


@dataclass
class DomainEvaluation:
    domain_match: float = 0.0
    constraint_coverage: float = 0.0
    terminology_accuracy: float = 0.0
    output_schema_compliance: float = 0.0
    actionability: float = 0.0
    hallucination_risk: float = 0.0
    genericness_penalty: float = 0.0  # 높을수록 generic (나쁨)

    overall_score: float = 0.0
    needs_repair: bool = False
    passed: bool = False

    missing_constraints: list[str] = field(default_factory=list)
    terminology_issues: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    repair_applied: bool = False

    def to_dict(self) -> dict:
        return {
            "scores": {
                "domain_match": self.domain_match,
                "constraint_coverage": self.constraint_coverage,
                "terminology_accuracy": self.terminology_accuracy,
                "output_schema_compliance": self.output_schema_compliance,
                "actionability": self.actionability,
                "hallucination_risk": self.hallucination_risk,
                "genericness_penalty": self.genericness_penalty,
            },
            "overall_score": self.overall_score,
            "pass": self.passed,
            "repair_applied": self.repair_applied,
            "issues": self.issues,
            "missing_constraints": self.missing_constraints,
            "terminology_issues": self.terminology_issues,
        }


# Score weights
_WEIGHTS = {
    "constraint_coverage": 0.30,
    "terminology_accuracy": 0.20,
    "output_schema_compliance": 0.20,
    "actionability": 0.15,
    "hallucination_risk": 0.10,
    "domain_match": 0.05,
}

# Placeholder / generic 탐지 패턴
_PLACEHOLDER_VALUES = {"", "TBD", "N/A", "unknown", "none", "null", "미정", "추후"}


class DomainEvaluator:
    def __init__(
        self,
        profiles: dict[str, DomainProfile],
        repair_threshold: float = 0.5,
    ):
        self._profiles = profiles
        self._repair_threshold = repair_threshold

    def evaluate(
        self,
        classification: ClassificationResult,
        envelope: RequirementEnvelope,
        profile: DomainProfile,
        slots: Optional[dict[str, Any]],
    ) -> DomainEvaluation:
        if slots is None:
            return DomainEvaluation(
                overall_score=0.0,
                needs_repair=True,
                missing_constraints=envelope.hard_constraints[:],
            )

        slots_str = _flatten_to_string(slots)

        # 1. Domain match (accepts both ClassificationResult and RoutingResult)
        classified_domain = getattr(classification, "primary_domain", None) or getattr(getattr(classification, "top", None), "domain", "")
        domain_match = 1.0 if classified_domain == envelope.target_domain else 0.5

        # 2. Constraint coverage
        missing = []
        if envelope.hard_constraints:
            covered = 0
            for c in envelope.hard_constraints:
                # 제약에서 핵심 키워드 추출해 slots 에서 검색
                keywords = [w for w in c.split() if len(w) >= 2]
                if any(kw.lower() in slots_str.lower() for kw in keywords):
                    covered += 1
                else:
                    missing.append(c)
            constraint_coverage = covered / len(envelope.hard_constraints)
        else:
            constraint_coverage = 1.0  # 제약 없으면 만점

        # 3. Terminology accuracy
        term_issues = []
        vocab = profile.vocabulary
        domain_terms_in_output = sum(
            1 for term in vocab if term.lower() in slots_str.lower()
        )
        # 다른 도메인 용어가 섞여 있으면 감점
        other_domain_terms = 0
        for other_domain, other_profile in self._profiles.items():
            if other_domain == profile.domain:
                continue
            for term, weight in other_profile.vocabulary.items():
                if weight >= 2.0 and term.lower() in slots_str.lower():
                    # 높은 가중치의 다른 도메인 용어가 있으면 문제
                    if term.lower() not in {t.lower() for t in vocab}:
                        other_domain_terms += 1
                        term_issues.append(f"cross-domain term: {term}")
        if domain_terms_in_output + other_domain_terms > 0:
            terminology_accuracy = domain_terms_in_output / (domain_terms_in_output + other_domain_terms)
        else:
            terminology_accuracy = 0.5  # 판단 불가

        # 4. Output schema compliance
        if profile.required_output_keys:
            present = sum(1 for k in profile.required_output_keys if k in slots)
            output_schema_compliance = present / len(profile.required_output_keys)
        else:
            output_schema_compliance = 1.0

        # 5. Actionability (placeholder 값 비율)
        all_values = list(_all_leaf_values(slots))
        if all_values:
            non_placeholder = sum(
                1 for v in all_values
                if str(v).strip().lower() not in _PLACEHOLDER_VALUES
                and v is not None
                and v != 0
                and v != []
            )
            actionability = non_placeholder / len(all_values)
        else:
            actionability = 0.0

        # 6. Hallucination risk (간단: 숫자 값이 user_input 에 없는데 output 에 있으면)
        import re
        input_numbers = set(re.findall(r'\d+(?:\.\d+)?', envelope.user_intent))
        output_numbers = set(re.findall(r'\d+(?:\.\d+)?', slots_str))
        invented = output_numbers - input_numbers - {"0", "1", "0.0", "1.0"}
        hallucination_risk = min(len(invented) / max(len(output_numbers), 1), 1.0)
        # 낮을수록 좋음 → 반전
        hallucination_score = 1.0 - hallucination_risk

        # 7. Genericness penalty: fluent but generic output detection
        generic_signals = 0
        _GENERIC_PHRASES = {"일반적으로", "보통", "typically", "generally", "usually",
                            "TBD", "추후", "미정", "N/A", "placeholder"}
        for phrase in _GENERIC_PHRASES:
            if phrase.lower() in slots_str.lower():
                generic_signals += 1
        # High actionability + low domain terms = suspiciously generic
        if domain_terms_in_output == 0 and actionability > 0.5:
            generic_signals += 2
        genericness_penalty = min(generic_signals / 5.0, 1.0)

        # Overall (genericness reduces the score)
        overall = (
            _WEIGHTS["domain_match"] * domain_match
            + _WEIGHTS["constraint_coverage"] * constraint_coverage
            + _WEIGHTS["terminology_accuracy"] * terminology_accuracy
            + _WEIGHTS["output_schema_compliance"] * output_schema_compliance
            + _WEIGHTS["actionability"] * actionability
            + _WEIGHTS["hallucination_risk"] * hallucination_score
        ) * (1.0 - 0.3 * genericness_penalty)  # generic 이면 최대 30% 감점

        passed = overall >= self._repair_threshold
        issues = []
        if constraint_coverage < 0.5:
            issues.append(f"constraint_coverage too low ({constraint_coverage:.2f})")
        if terminology_accuracy < 0.5:
            issues.append(f"terminology_accuracy too low ({terminology_accuracy:.2f})")
        if output_schema_compliance < 0.5:
            issues.append(f"missing required output keys")
        if genericness_penalty > 0.5:
            issues.append(f"output appears generic (penalty={genericness_penalty:.2f})")
        if hallucination_risk > 0.5:
            issues.append(f"high hallucination risk ({hallucination_risk:.2f})")

        return DomainEvaluation(
            domain_match=round(domain_match, 3),
            constraint_coverage=round(constraint_coverage, 3),
            terminology_accuracy=round(terminology_accuracy, 3),
            output_schema_compliance=round(output_schema_compliance, 3),
            actionability=round(actionability, 3),
            hallucination_risk=round(hallucination_risk, 3),
            genericness_penalty=round(genericness_penalty, 3),
            overall_score=round(overall, 3),
            needs_repair=not passed,
            passed=passed,
            missing_constraints=missing,
            terminology_issues=term_issues,
            issues=issues,
        )


def _flatten_to_string(d: Any) -> str:
    """dict/list 를 재귀적으로 문자열로 평탄화."""
    if isinstance(d, dict):
        parts = []
        for k, v in d.items():
            parts.append(str(k))
            parts.append(_flatten_to_string(v))
        return " ".join(parts)
    elif isinstance(d, list):
        return " ".join(_flatten_to_string(item) for item in d)
    else:
        return str(d) if d is not None else ""


def _all_leaf_values(d: Any) -> list:
    """dict/list 에서 모든 말단 값을 추출."""
    if isinstance(d, dict):
        out = []
        for v in d.values():
            out.extend(_all_leaf_values(v))
        return out
    elif isinstance(d, list):
        out = []
        for item in d:
            out.extend(_all_leaf_values(item))
        return out
    else:
        return [d]
