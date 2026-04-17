"""
review/layered.py — 다층 인간 검수 판정 (layered semantic gate)

기존 review/judgment.py의 ReviewJudgment 가 "스키마/엔진 결과" 중심이라면,
이 모듈은 LLM 슬롯 추출 결과(human_review/review_data.json 라인의 부모)에
대해 의미 검증까지 합친 다층 판정을 표현한다.

배경
----
2026-04-04 시점 vllm_orchestrator/runtime/human_review/review_data.json 의
HR-001 ~ HR-012 모든 항목이 ``auto_validated: true`` 였으나, 실제 출력은:

  - HR-001: builder 출력 키가 중국어 (楼层, 户型)
  - HR-004: cad constraint_parse 가 {valid, message, error} 형태
  - HR-008: minecraft style_check 가 font_family/padding 같은 CSS 속성
  - HR-011: animation camera_intent 가 example.com URL 환각
  - HR-012: animation lighting_intent 가 "비 오는 밤" → "outside night"

같은 거짓 양성을 다수 포함했다. 원인은 dispatcher 가 JSON 파싱만 성공하면
``TaskResult.validated = True`` 로 표시했기 때문이다 (orchestration/dispatcher.py).

이 모듈은 단일 boolean 대신 다섯 개 게이트를 분리한다:

  1. schema_validated     : JSON 형태가 유효한가
  2. language_validated   : 출력 언어/문자 종류가 도메인 요구를 만족하는가
  3. semantic_validated   : 의미 보존 (간단한 anchor 토큰 매칭)
  4. domain_guard_validated : 도메인 가드 (validator-shape, css leakage 등)
  5. contract_validated   : 태스크 별 contract (allowed/forbidden keys 등)

``auto_validated`` 는 위 다섯이 모두 True 일 때만 True 가 된다.
``final_judgment`` 은 severity 와 게이트 결과를 결합해 PASS / NEEDS_REVIEW / FAIL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from enum import Enum
from typing import Any, Optional, Iterable

from .judgment import Severity  # 기존 severity 재사용


class LayeredVerdict(str, Enum):
    """다층 판정 최종 결과"""
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"


class FailureCategory(str, Enum):
    """false positive 분류용 정규화된 카테고리.

    semantic_validators / task_contracts / false_positive_analyzer 가 모두 공유.
    """
    NONE = "none"

    # 언어/문자 류
    WRONG_LANGUAGE = "wrong_language"
    WRONG_KEY_LOCALE = "wrong_key_locale"

    # 형상/구조 류
    VALIDATOR_SHAPED_RESPONSE = "validator_shaped_response"
    INVALID_DOMAIN_VOCAB = "invalid_domain_vocab"
    CSS_PROPERTY_LEAK = "css_property_leak"

    # 환각 / 외부 참조 류
    HALLUCINATED_EXTERNAL_REFERENCE = "hallucinated_external_reference"

    # 의미 류
    SEMANTIC_MISTRANSLATION = "semantic_mistranslation"
    SCHEMA_PASS_BUT_SEMANTIC_FAIL = "schema_pass_but_semantic_fail"

    # 계약 류
    TASK_CONTRACT_VIOLATION = "task_contract_violation"

    # 운영 류
    SCHEMA_FAILURE = "schema_failure"
    EMPTY_OUTPUT = "empty_output"
    REPAIR_PARSE_ERROR = "repair_parse_error"


# severity → numeric 우선순위 (높을수록 심각)
_SEVERITY_RANK: dict[str, int] = {
    Severity.INFO.value: 0,
    Severity.LOW.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.HIGH.value: 3,
    Severity.CRITICAL.value: 4,
}


def severity_max(items: Iterable[str]) -> str:
    """주어진 severity 들 중 가장 심각한 것을 반환. 비어 있으면 info."""
    best = -1
    best_name = Severity.INFO.value
    for s in items:
        rank = _SEVERITY_RANK.get(s, -1)
        if rank > best:
            best = rank
            best_name = s
    return best_name


@dataclass
class GateResult:
    """단일 게이트(layer) 결과"""
    name: str                       # schema/language/semantic/domain_guard/contract
    passed: bool
    severity: str = Severity.INFO.value
    failure_categories: list[str] = field(default_factory=list)
    rationale: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LayeredJudgment:
    """다층 판정 결과 — review_data.json 의 새 표준 표현.

    하위 호환을 위해 dict 출력은 기존 ``auto_validated`` 키를 유지하지만
    이제는 5개 게이트가 모두 통과해야만 True 가 된다.
    """
    artifact_id: str
    domain: str
    task_type: str

    # 5개 게이트
    schema_validated: bool = False
    language_validated: bool = False
    semantic_validated: bool = False
    domain_guard_validated: bool = False
    contract_validated: bool = False

    # 게이트 별 상세
    gates: list[GateResult] = field(default_factory=list)

    # 판정 결과
    auto_validated: bool = False             # 5개 게이트 모두 True 일 때만
    final_judgment: str = LayeredVerdict.NEEDS_REVIEW.value
    severity: str = Severity.INFO.value      # 게이트 중 최댓값
    failure_categories: list[str] = field(default_factory=list)
    rationale: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 1.0

    # 원본 디버깅
    raw_output: Optional[str] = None
    parsed_payload: Optional[Any] = None
    user_input: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "domain": self.domain,
            "task_type": self.task_type,

            "schema_validated": self.schema_validated,
            "language_validated": self.language_validated,
            "semantic_validated": self.semantic_validated,
            "domain_guard_validated": self.domain_guard_validated,
            "contract_validated": self.contract_validated,
            "auto_validated": self.auto_validated,

            "final_judgment": self.final_judgment,
            "severity": self.severity,
            "failure_categories": list(self.failure_categories),
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,

            "gates": [g.to_dict() for g in self.gates],
            "user_input": self.user_input,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Composition rules
# ---------------------------------------------------------------------------

# 어떤 게이트가 fail 일 때, 어떤 final_judgment 를 강제하는가
# - schema 실패는 항상 FAIL (운영 가능 안 됨)
# - language/semantic/domain_guard/contract 는 severity 에 따라 결정
_HARD_FAIL_GATES = {"schema"}


def compose_judgment(
    artifact_id: str,
    domain: str,
    task_type: str,
    gates: list[GateResult],
    *,
    raw_output: Optional[str] = None,
    parsed_payload: Optional[Any] = None,
    user_input: Optional[str] = None,
    recommended_action: str = "",
    confidence: float = 1.0,
) -> LayeredJudgment:
    """게이트 결과 리스트 → LayeredJudgment.

    조합 규칙:
      - schema_validated False → final = FAIL
      - 어느 게이트든 severity == critical → FAIL
      - 어느 게이트든 severity == high → FAIL
      - 어느 게이트든 severity == medium → NEEDS_REVIEW
      - 어느 게이트든 severity == low → NEEDS_REVIEW
      - 모든 게이트 통과 + severity in (info,) → PASS
      - auto_validated = (위 5개 게이트 모두 True)
    """
    by_name = {g.name: g for g in gates}

    schema_g       = by_name.get("schema",       GateResult("schema", False, Severity.CRITICAL.value, [FailureCategory.SCHEMA_FAILURE.value], "schema gate missing"))
    language_g     = by_name.get("language",     GateResult("language", True))
    semantic_g     = by_name.get("semantic",     GateResult("semantic", True))
    domain_g       = by_name.get("domain_guard", GateResult("domain_guard", True))
    contract_g     = by_name.get("contract",     GateResult("contract", True))

    schema_validated      = bool(schema_g.passed)
    language_validated    = bool(language_g.passed)
    semantic_validated    = bool(semantic_g.passed)
    domain_guard_validated = bool(domain_g.passed)
    contract_validated    = bool(contract_g.passed)

    auto_validated = all([
        schema_validated, language_validated, semantic_validated,
        domain_guard_validated, contract_validated,
    ])

    # severity max
    severity = severity_max(g.severity for g in gates)

    # failure categories merge
    fc: list[str] = []
    for g in gates:
        for c in g.failure_categories:
            if c and c != FailureCategory.NONE.value and c not in fc:
                fc.append(c)

    # evidence merge
    evidence: list[dict[str, Any]] = []
    for g in gates:
        for ev in g.evidence:
            ev2 = {"gate": g.name, **ev}
            evidence.append(ev2)

    # rationale
    failed_gates = [g.name for g in gates if not g.passed]
    if failed_gates:
        rationale = f"failed gates: {', '.join(failed_gates)}"
    else:
        rationale = "all gates passed"

    # final judgment
    if not schema_validated:
        verdict = LayeredVerdict.FAIL
    elif severity in (Severity.CRITICAL.value, Severity.HIGH.value):
        verdict = LayeredVerdict.FAIL
    elif severity in (Severity.MEDIUM.value, Severity.LOW.value):
        verdict = LayeredVerdict.NEEDS_REVIEW
    elif not auto_validated:
        # severity 가 info 인데도 실패한 게이트가 있다면 보수적으로 needs_review
        verdict = LayeredVerdict.NEEDS_REVIEW
    else:
        verdict = LayeredVerdict.PASS

    return LayeredJudgment(
        artifact_id=artifact_id,
        domain=domain,
        task_type=task_type,
        schema_validated=schema_validated,
        language_validated=language_validated,
        semantic_validated=semantic_validated,
        domain_guard_validated=domain_guard_validated,
        contract_validated=contract_validated,
        gates=gates,
        auto_validated=auto_validated,
        final_judgment=verdict.value,
        severity=severity,
        failure_categories=fc,
        rationale=rationale,
        evidence=evidence,
        recommended_action=recommended_action,
        confidence=confidence,
        raw_output=raw_output,
        parsed_payload=parsed_payload,
        user_input=user_input,
    )


def passing_judgment(
    artifact_id: str,
    domain: str,
    task_type: str,
    *,
    parsed_payload: Optional[Any] = None,
    user_input: Optional[str] = None,
) -> LayeredJudgment:
    """편의: 5개 게이트 모두 통과한 깨끗한 LayeredJudgment 반환 (테스트용)."""
    gates = [
        GateResult("schema", True),
        GateResult("language", True),
        GateResult("semantic", True),
        GateResult("domain_guard", True),
        GateResult("contract", True),
    ]
    return compose_judgment(
        artifact_id=artifact_id,
        domain=domain,
        task_type=task_type,
        gates=gates,
        parsed_payload=parsed_payload,
        user_input=user_input,
    )
