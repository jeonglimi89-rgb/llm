"""
orchestration/post_classification_validator.py — Post-classification validation.

After route/classify, validates that:
1. Inferred domain matches actual app role
2. Task family is valid for the app
3. Multi-intent requests are flagged (not silently merged into one app)
4. Wrong single-domain attribution is caught

This gate runs BEFORE execution and can return fail_loud or clarification_required.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..domain.intervention_policy import InterventionPolicy, InterventionPolicyResult


@dataclass
class PostClassificationResult:
    """Result of post-classification validation."""
    passed: bool = True
    needs_clarification: bool = False
    fail_loud: bool = False
    reason: str = ""
    intervention_result: Optional[dict[str, Any]] = None
    multi_domain_detected: bool = False
    detected_domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# Keywords that suggest cross-domain intent
_CROSS_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "minecraft": ["블록", "마인크래프트", "minecraft", "빌드", "NPC", "리소스팩", "텍스처"],
    "builder": ["건물", "평면도", "도면", "외관", "내부", "층", "주택", "상가", "건축"],
    "animation": ["카메라", "그림체", "스타일", "연출", "장면", "애니메이션", "프레이밍"],
    "cad": ["설계도", "치수", "방수", "IP67", "부품", "조립", "PCB", "배선"],
}


class PostClassificationValidator:
    """Validates classification results before execution."""

    def __init__(self, intervention_policy: InterventionPolicy):
        self._intervention = intervention_policy

    def validate(
        self,
        primary_domain: str,
        task_name: str,
        user_input: str,
        *,
        classification_candidates: Optional[list[dict]] = None,
    ) -> PostClassificationResult:
        """Validate the classification result.

        Args:
            primary_domain: The domain the router selected
            task_name: The task name the router selected
            user_input: Original user input for multi-domain detection
            classification_candidates: List of {domain, score} from router
        """
        # 1. Intervention policy check
        intervention = self._intervention.check(primary_domain, task_name)
        if not intervention.passed:
            return PostClassificationResult(
                passed=False,
                fail_loud=True,
                reason=intervention.violation_detail,
                intervention_result=intervention.to_dict(),
            )

        # 2. Multi-domain detection
        detected_domains = self._detect_multi_domain(user_input)
        if len(detected_domains) > 1 and primary_domain in detected_domains:
            other_domains = [d for d in detected_domains if d != primary_domain]
            # Check if the signal is strong enough to warrant clarification
            if self._is_strong_multi_domain(user_input, primary_domain, other_domains):
                return PostClassificationResult(
                    passed=False,
                    needs_clarification=True,
                    reason=(
                        f"Request appears to involve multiple domains: {detected_domains}. "
                        f"Routed to '{primary_domain}' but also detected: {other_domains}. "
                        f"Please clarify which app should handle this, or split into separate requests."
                    ),
                    multi_domain_detected=True,
                    detected_domains=detected_domains,
                    intervention_result=intervention.to_dict(),
                )

        # 3. Check classification confidence (if candidates available)
        if classification_candidates and len(classification_candidates) >= 2:
            top = classification_candidates[0]
            runner = classification_candidates[1]
            gap = top.get("score", 0) - runner.get("score", 0)
            if gap < 0.05 and runner.get("domain") != primary_domain:
                # Very close scores — might be wrong attribution
                return PostClassificationResult(
                    passed=False,
                    needs_clarification=True,
                    reason=(
                        f"Classification is ambiguous: "
                        f"'{top.get('domain')}'={top.get('score', 0):.2f} vs "
                        f"'{runner.get('domain')}'={runner.get('score', 0):.2f}. "
                        f"Please clarify which app should handle this."
                    ),
                    detected_domains=[top.get("domain", ""), runner.get("domain", "")],
                    intervention_result=intervention.to_dict(),
                )

        return PostClassificationResult(
            passed=True,
            intervention_result=intervention.to_dict(),
            detected_domains=[primary_domain],
        )

    def _detect_multi_domain(self, text: str) -> list[str]:
        """Detect which domains have signals in the input text."""
        detected = []
        text_lower = text.lower()
        for domain, keywords in _CROSS_DOMAIN_SIGNALS.items():
            if any(kw.lower() in text_lower for kw in keywords):
                detected.append(domain)
        return detected

    def _is_strong_multi_domain(
        self,
        text: str,
        primary: str,
        others: list[str],
    ) -> bool:
        """Check if non-primary domain signals are strong enough to flag."""
        text_lower = text.lower()
        for other in others:
            keywords = _CROSS_DOMAIN_SIGNALS.get(other, [])
            matches = sum(1 for kw in keywords if kw.lower() in text_lower)
            if matches >= 2:
                return True
        return False
