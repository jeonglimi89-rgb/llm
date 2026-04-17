"""
orchestration/multi_graph_splitter.py — Multi-domain request splitting.

When a user request contains intents for multiple domains (e.g.,
"Minecraft 타워 만들고 카메라 워킹도 짜줘"), the splitter:

1. Detects which domains are involved
2. Splits the request into per-domain sub-requests
3. Produces separate command graphs for each domain
4. Ensures no cross-domain contamination in any single graph

If splitting is not possible or domains are ambiguous,
returns clarification_required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..domain.command_graph import CommandGraph, CommandGraphBundle


@dataclass
class DomainSubRequest:
    """A sub-request extracted for a single domain."""
    domain: str
    user_input_segment: str
    confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MultiGraphSplitResult:
    """Result of multi-domain request splitting."""
    is_multi_domain: bool = False
    can_split: bool = False
    needs_clarification: bool = False
    sub_requests: list[DomainSubRequest] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "is_multi_domain": self.is_multi_domain,
            "can_split": self.can_split,
            "needs_clarification": self.needs_clarification,
            "sub_requests": [s.to_dict() for s in self.sub_requests],
            "reason": self.reason,
        }


# Domain keyword patterns for segmentation
_DOMAIN_PATTERNS: dict[str, list[tuple[str, float]]] = {
    "minecraft": [
        (r"마인크래프트|minecraft", 3.0),
        (r"빌드|build|블록|block", 2.0),
        (r"NPC|npc|엔피씨", 2.5),
        (r"리소스팩|resource\s*pack|텍스처", 2.5),
        (r"타워|성|마을|다리|항구", 1.5),
    ],
    "builder": [
        (r"건물|주택|아파트|상가", 2.5),
        (r"도면|평면도|입면|단면", 3.0),
        (r"외관|외부|외피|파사드", 2.0),
        (r"내부|인테리어|실내|거실|주방|침실", 2.0),
        (r"건축|건폐율|용적률|층", 2.0),
    ],
    "animation": [
        (r"카메라|camera", 3.0),
        (r"워킹|walking|무빙", 2.5),
        (r"그림체|스타일|style", 2.0),
        (r"연출|장면|씬|scene", 2.0),
        (r"프레이밍|클로즈업|와이드", 2.5),
        (r"피드백|feedback", 1.5),
    ],
    "cad": [
        (r"설계도|CAD|cad", 3.0),
        (r"치수|dimension|방수|IP\d{2}", 2.5),
        (r"부품|조립|assembly|배선", 2.0),
        (r"PCB|하우징|housing|금형", 2.5),
    ],
}

# Conjunctions that suggest separate intents
_SPLIT_MARKERS = re.compile(
    r"(?:그리고|하고|또|또한|이랑|랑|추가로|동시에|함께|,\s*(?:그리고|또))",
    re.IGNORECASE,
)


class MultiGraphSplitter:
    """Splits multi-domain requests into per-domain sub-requests."""

    def analyze(self, user_input: str) -> MultiGraphSplitResult:
        """Analyze if the input contains multiple domain intents.

        Returns a MultiGraphSplitResult indicating whether splitting is needed
        and providing the split sub-requests if possible.
        """
        # Score each domain
        domain_scores: dict[str, float] = {}
        domain_keywords: dict[str, list[str]] = {}
        for domain, patterns in _DOMAIN_PATTERNS.items():
            score = 0.0
            keywords = []
            for pattern, weight in patterns:
                matches = re.findall(pattern, user_input, re.IGNORECASE)
                if matches:
                    score += weight * len(matches)
                    keywords.extend(matches)
            if score > 0:
                domain_scores[domain] = score
                domain_keywords[domain] = keywords

        # Single domain or none
        if len(domain_scores) <= 1:
            return MultiGraphSplitResult(
                is_multi_domain=False,
                reason="single domain detected" if domain_scores else "no domain signals",
            )

        # Multiple domains detected
        sorted_domains = sorted(domain_scores.items(), key=lambda x: -x[1])

        # Check if there's a clear split marker
        segments = _SPLIT_MARKERS.split(user_input)
        if len(segments) >= 2:
            # Try to assign each segment to a domain
            sub_requests = self._assign_segments(segments, domain_scores, domain_keywords)
            if sub_requests and len(sub_requests) >= 2:
                return MultiGraphSplitResult(
                    is_multi_domain=True,
                    can_split=True,
                    sub_requests=sub_requests,
                    reason=f"Split into {len(sub_requests)} domain sub-requests",
                )

        # Can't cleanly split — need clarification
        return MultiGraphSplitResult(
            is_multi_domain=True,
            can_split=False,
            needs_clarification=True,
            sub_requests=[
                DomainSubRequest(
                    domain=d,
                    user_input_segment=user_input,
                    confidence=s / max(domain_scores.values()),
                    matched_keywords=domain_keywords.get(d, []),
                )
                for d, s in sorted_domains
            ],
            reason=(
                f"Multiple domains detected ({', '.join(d for d, _ in sorted_domains)}) "
                f"but cannot cleanly split the request. Please separate into individual requests."
            ),
        )

    def _assign_segments(
        self,
        segments: list[str],
        domain_scores: dict[str, float],
        domain_keywords: dict[str, list[str]],
    ) -> list[DomainSubRequest]:
        """Try to assign text segments to specific domains."""
        sub_requests = []
        assigned_domains = set()

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            best_domain = ""
            best_score = 0.0
            best_keywords = []

            for domain, patterns in _DOMAIN_PATTERNS.items():
                score = 0.0
                keywords = []
                for pattern, weight in patterns:
                    matches = re.findall(pattern, segment, re.IGNORECASE)
                    if matches:
                        score += weight * len(matches)
                        keywords.extend(matches)
                if score > best_score:
                    best_score = score
                    best_domain = domain
                    best_keywords = keywords

            if best_domain and best_score > 0:
                sub_requests.append(DomainSubRequest(
                    domain=best_domain,
                    user_input_segment=segment,
                    confidence=min(best_score / 5.0, 1.0),
                    matched_keywords=best_keywords,
                ))
                assigned_domains.add(best_domain)

        # Only return if we have at least 2 distinct domains
        if len(assigned_domains) >= 2:
            return sub_requests
        return []
