"""
review package — 인간 검수 / 자동 게이트.

기존 모듈
---------
- judgment.py            : ReviewJudgment / JudgmentItem (엔진 결과 검수 표현)
- reviewer.py            : 도메인별 review_* 함수 (engine result → ReviewJudgment)

2026-04-06 추가
---------------
- layered.py             : 5게이트 LayeredJudgment + compose_judgment
- semantic_validators.py : 결정론적 의미 detector 모음
- task_contracts.py      : 태스크별 contract + evaluate_task_contract
- false_positive_analyzer.py : runtime artifact false-positive 분석기
"""
from .judgment import (
    ReviewJudgment, JudgmentItem, Verdict, Severity, validate_judgment_schema,
)
from .layered import (
    LayeredJudgment, LayeredVerdict, FailureCategory, GateResult,
    compose_judgment, passing_judgment, severity_max,
)
from .task_contracts import (
    TaskContract, get_task_contract, evaluate_task_contract, TASK_CONTRACTS,
)
from .semantic_validators import (
    DetectorResult,
    detect_chinese_keys, detect_japanese_in_keys,
    detect_non_korean_in_required_field,
    detect_validator_shape, detect_css_property_leak,
    detect_url_hallucination, detect_semantic_anchor_loss,
    detect_known_lossy_english, detect_empty_or_trivial_payload,
    detect_input_echo,
)
from .false_positive_analyzer import (
    HRCaseAnalysis, ManifestSummary, FalsePositiveReport,
    analyze_manifests, analyze_human_review, build_report, write_report,
)

__all__ = [
    # judgment (legacy)
    "ReviewJudgment", "JudgmentItem", "Verdict", "Severity", "validate_judgment_schema",
    # layered
    "LayeredJudgment", "LayeredVerdict", "FailureCategory", "GateResult",
    "compose_judgment", "passing_judgment", "severity_max",
    # task contracts
    "TaskContract", "get_task_contract", "evaluate_task_contract", "TASK_CONTRACTS",
    # detectors
    "DetectorResult",
    "detect_chinese_keys", "detect_japanese_in_keys",
    "detect_non_korean_in_required_field",
    "detect_validator_shape", "detect_css_property_leak",
    "detect_url_hallucination", "detect_semantic_anchor_loss",
    "detect_known_lossy_english", "detect_empty_or_trivial_payload",
    "detect_input_echo",
    # analyzer
    "HRCaseAnalysis", "ManifestSummary", "FalsePositiveReport",
    "analyze_manifests", "analyze_human_review", "build_report", "write_report",
]
