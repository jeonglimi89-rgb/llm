"""
orchestration/domain_classifier.py — Rule-based domain classification.

자연어 입력을 {cad, builder, minecraft, animation, product_design} 중 하나로 분류.
LLM 호출 0회 — 가중 키워드 매칭 기반.
"general" 로 빠지지 않음. 항상 5개 중 하나 선택.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..domain.profiles import DomainProfile


@dataclass
class ClassificationCandidate:
    domain: str
    confidence: float
    matched_signals: list[str]
    task_name: str


@dataclass
class ClassificationResult:
    top: ClassificationCandidate
    runner_up: Optional[ClassificationCandidate]
    ambiguous: bool
    classification_reason: str
    raw_scores: dict[str, float]


class DomainClassifier:
    """가중 키워드 기반 도메인 분류기.

    각 도메인 프로필의 vocabulary (term → weight) 를 사용해
    입력 텍스트에서 매칭되는 키워드의 가중 합을 계산하고,
    가장 높은 점수의 도메인을 선택한다.
    """

    def __init__(
        self,
        profiles: dict[str, DomainProfile],
        ambiguity_threshold: float = 0.15,
    ):
        self._profiles = profiles
        self._ambiguity_threshold = ambiguity_threshold

    def classify(
        self,
        user_input: str,
        context: Optional[dict] = None,
    ) -> ClassificationResult:
        text_lower = user_input.lower()
        scores: dict[str, float] = {}
        matches: dict[str, list[str]] = {}

        for domain, profile in self._profiles.items():
            score = 0.0
            matched = []
            for term, weight in profile.vocabulary.items():
                if term.lower() in text_lower:
                    score += weight
                    matched.append(term)
            # Context boost: 이전 도메인 정보가 있으면 가산점
            if context and context.get("prior_domain") == domain:
                score += 1.0
                matched.append("(context_boost)")
            scores[domain] = score
            matches[domain] = matched

        # 정규화 + 정렬
        total = sum(scores.values()) or 1.0
        ranked = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        top_domain, top_score = ranked[0]
        top_confidence = top_score / total
        runner_domain, runner_score = ranked[1] if len(ranked) > 1 else (None, 0.0)
        runner_confidence = runner_score / total if runner_domain else 0.0

        # Task name 추론
        top_task = self._infer_task_name(user_input, top_domain)
        runner_task = self._infer_task_name(user_input, runner_domain) if runner_domain else ""

        # Ambiguity 판단
        ambiguous = (top_confidence - runner_confidence) < self._ambiguity_threshold

        top_candidate = ClassificationCandidate(
            domain=top_domain,
            confidence=round(top_confidence, 3),
            matched_signals=matches.get(top_domain, []),
            task_name=top_task,
        )
        runner_candidate = ClassificationCandidate(
            domain=runner_domain,
            confidence=round(runner_confidence, 3),
            matched_signals=matches.get(runner_domain, []),
            task_name=runner_task,
        ) if runner_domain else None

        reason_parts = [f"top={top_domain}({top_confidence:.2f})"]
        if runner_domain:
            reason_parts.append(f"runner_up={runner_domain}({runner_confidence:.2f})")
        if ambiguous:
            reason_parts.append("AMBIGUOUS")
        reason_parts.append(f"signals={matches.get(top_domain, [])[:5]}")

        return ClassificationResult(
            top=top_candidate,
            runner_up=runner_candidate,
            ambiguous=ambiguous,
            classification_reason=" | ".join(reason_parts),
            raw_scores={d: round(s / total, 3) for d, s in scores.items()},
        )

    def _infer_task_name(self, user_input: str, domain: str) -> str:
        """도메인 내에서 가장 적합한 task_name 을 추론."""
        profile = self._profiles.get(domain)
        if not profile:
            return ""
        text_lower = user_input.lower()
        best_task = profile.fallback_task_name
        best_score = 0
        for task_name, signals in profile.task_signals.items():
            score = sum(1 for s in signals if s.lower() in text_lower)
            if score > best_score:
                best_score = score
                best_task = task_name
        return best_task
