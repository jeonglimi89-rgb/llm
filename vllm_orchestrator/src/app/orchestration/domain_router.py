"""
orchestration/domain_router.py — Domain-specialized request router.

5개 고정 도메인(cad/builder/minecraft/animation/product_design)만 지원.
general/generic 으로 빠지지 않음.

3가지 신호를 조합해 분류:
1. Vocabulary match (profile.vocabulary 가중 키워드)
2. Intent match (동사/행위 패턴 → 도메인 귀속)
3. Constraint/output expectation match (기대 산출물 유형)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..domain.profiles import DomainProfile


@dataclass
class DomainCandidate:
    domain: str
    score: float
    signals: list[str] = field(default_factory=list)


@dataclass
class RoutingResult:
    primary_domain: str
    primary_score: float
    candidates: list[DomainCandidate]
    reason: list[str]
    ambiguous: bool
    inferred_task: str

    def to_dict(self) -> dict:
        return {
            "primary_domain": self.primary_domain,
            "candidates": [
                {"domain": c.domain, "score": round(c.score, 3)}
                for c in self.candidates
            ],
            "reason": self.reason,
            "ambiguous": self.ambiguous,
            "inferred_task": self.inferred_task,
        }


# Intent patterns: (regex, domain, weight, signal_label)
_INTENT_PATTERNS: list[tuple[re.Pattern, str, float, str]] = [
    # CAD
    (re.compile(r"(설계|제작|부품|조립|배선|배수|치수|공차)", re.I), "cad", 2.0, "intent:design/part"),
    (re.compile(r"(\d+\s*[x×]\s*\d+\s*(mm|cm))", re.I), "cad", 2.5, "intent:dimension_pattern"),
    (re.compile(r"(IP\d{2}|방수|방진|밀봉)", re.I), "cad", 2.0, "intent:sealing_spec"),
    (re.compile(r"(PCB|모터|센서|배터리|충전|USB)", re.I), "cad", 1.8, "intent:electronic_component"),
    # Builder
    (re.compile(r"(건축|건물|주택|아파트|층\s*짓|평면|설계도|카페|사무실|상가)", re.I), "builder", 2.5, "intent:architecture"),
    (re.compile(r"(\d+\s*층|\d+\s*평|건폐율|용적률)", re.I), "builder", 2.5, "intent:building_spec"),
    (re.compile(r"(거실|주방|침실|화장실|현관|카페|사무실)", re.I), "builder", 2.0, "intent:room_type"),
    (re.compile(r"(설비|배관|전기|구조|기초)", re.I), "builder", 1.5, "intent:MEP/structural"),
    # Minecraft
    (re.compile(r"(마인크래프트|minecraft|블록|block)", re.I), "minecraft", 3.0, "intent:minecraft_explicit"),
    (re.compile(r"(빌드|build|팔레트|palette|바이옴|biome)", re.I), "minecraft", 2.0, "intent:mc_build"),
    (re.compile(r"(중세|판타지|모던.*빌드|성|탑|다리|항구)", re.I), "minecraft", 1.8, "intent:mc_style"),
    (re.compile(r"(돌|참나무|스프루스|벽돌|유리|울타리)", re.I), "minecraft", 2.0, "intent:mc_material"),
    # Animation
    (re.compile(r"(애니메이션|animation|스토리보드|storyboard)", re.I), "animation", 3.0, "intent:anim_explicit"),
    (re.compile(r"(클로즈업|와이드|미디엄|샷|shot|컷|씬|scene)", re.I), "animation", 2.5, "intent:shot_type"),
    (re.compile(r"(카메라|camera|렌즈|앵글|팬|틸트|달리)", re.I), "animation", 2.0, "intent:camera"),
    (re.compile(r"(연기|표정|감정|무드|조명|lighting)", re.I), "animation", 2.0, "intent:acting/mood"),
    (re.compile(r"(연속성|continuity|리드로|redraw)", re.I), "animation", 2.5, "intent:continuity"),
    # Product Design
    (re.compile(r"(제품.*설계|product.*design|소형가전|가전.*제품)", re.I), "product_design", 3.0, "intent:product_design_explicit"),
    (re.compile(r"(양산|금형|사출|mass.?production|injection)", re.I), "product_design", 2.5, "intent:manufacturing"),
    (re.compile(r"(컨셉|concept|BOM|원가|cost)", re.I), "product_design", 2.0, "intent:concept/cost"),
    (re.compile(r"(KC|CE|FCC|인증|certification)", re.I), "product_design", 2.0, "intent:certification"),
    (re.compile(r"(사용자.*경험|UX|사용성|usability|패키징|packaging)", re.I), "product_design", 2.0, "intent:ux/packaging"),
]

# Output expectation patterns: what the user expects back
_OUTPUT_PATTERNS: list[tuple[re.Pattern, str, float, str]] = [
    (re.compile(r"(부품.*목록|BOM|인터페이스|배선도)", re.I), "cad", 2.0, "output:parts_list"),
    (re.compile(r"(평면도|배치도|공간.*계획|층.*설계)", re.I), "builder", 2.0, "output:floor_plan"),
    (re.compile(r"(블록.*배치|건축.*명령|팔레트.*추천)", re.I), "minecraft", 2.0, "output:block_placement"),
    (re.compile(r"(샷.*리스트|컷.*시트|카메라.*계획|연출.*지시)", re.I), "animation", 2.0, "output:shot_list"),
    (re.compile(r"(BOM.*표|부품.*목록|원가.*산출|제품.*사양)", re.I), "product_design", 2.0, "output:bom/spec"),
]

VALID_DOMAINS = frozenset({"cad", "builder", "minecraft", "animation", "product_design"})
AMBIGUITY_THRESHOLD = 0.12  # top-runner_up 차이 이하면 ambiguous


class DomainRouter:
    """5-domain specialized router. Never routes to 'general'."""

    def __init__(self, profiles: dict[str, DomainProfile]):
        self._profiles = profiles

    def route(
        self,
        user_input: str,
        context: Optional[dict] = None,
    ) -> RoutingResult:
        text = user_input.strip()
        scores: dict[str, float] = {d: 0.0 for d in VALID_DOMAINS}
        signals: dict[str, list[str]] = {d: [] for d in VALID_DOMAINS}

        # 1. Vocabulary match (from profiles)
        text_lower = text.lower()
        for domain, profile in self._profiles.items():
            if domain not in VALID_DOMAINS:
                continue
            for term, weight in profile.vocabulary.items():
                if term.lower() in text_lower:
                    scores[domain] += weight
                    signals[domain].append(f"vocab:{term}")

        # 2. Intent match
        for pattern, domain, weight, label in _INTENT_PATTERNS:
            if pattern.search(text):
                scores[domain] += weight
                signals[domain].append(label)

        # 3. Output expectation match
        for pattern, domain, weight, label in _OUTPUT_PATTERNS:
            if pattern.search(text):
                scores[domain] += weight
                signals[domain].append(label)

        # 4. Context boost
        if context and context.get("prior_domain") in VALID_DOMAINS:
            prior = context["prior_domain"]
            scores[prior] += 1.0
            signals[prior].append("context:prior_domain")

        # Normalize + rank
        total = sum(scores.values()) or 1.0
        candidates = sorted(
            [DomainCandidate(d, scores[d] / total, signals[d]) for d in VALID_DOMAINS],
            key=lambda c: c.score,
            reverse=True,
        )

        top = candidates[0]
        runner = candidates[1] if len(candidates) > 1 else None
        ambiguous = (runner is not None and (top.score - runner.score) < AMBIGUITY_THRESHOLD)

        # Infer task_name within primary domain
        inferred_task = self._infer_task(text, top.domain)

        reason = [f"primary={top.domain}({top.score:.2f})"]
        if runner:
            reason.append(f"runner_up={runner.domain}({runner.score:.2f})")
        if ambiguous:
            reason.append("AMBIGUOUS")
        reason.extend(top.signals[:5])

        return RoutingResult(
            primary_domain=top.domain,
            primary_score=round(top.score, 3),
            candidates=candidates,
            reason=reason,
            ambiguous=ambiguous,
            inferred_task=inferred_task,
        )

    def _infer_task(self, text: str, domain: str) -> str:
        profile = self._profiles.get(domain)
        if not profile or not profile.task_signals:
            return profile.fallback_task_name if profile else ""
        text_lower = text.lower()
        best_task = profile.fallback_task_name
        best_score = 0
        for task_name, keywords in profile.task_signals.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            if score > best_score:
                best_score = score
                best_task = task_name
        return best_task
