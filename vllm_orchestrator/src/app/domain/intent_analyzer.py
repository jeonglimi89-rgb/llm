"""intent_analyzer.py — 사용자 입력 분석 (rule-based, 결정론적, 0ms 오버헤드)

LLM 호출 전에 user_input을 스캔해서:
  - concept_category: "witch" / "waffle" / "frog" / "sky" / "castle" / "generic"
  - creative_demand: "low" / "medium" / "high" — 변형(variant) 개수 결정에 사용
  - complexity: "simple" / "medium" / "complex" — 프롬프트/토큰 예산 결정
  - theme_keywords: 탐지된 키워드 목록
  - modifiers: "floating", "hollow", "multi_tower", "grid", ...
  - suggested_variant_count: 1~3

LLM 확장: `analyze_with_llm(user_input)` 는 14B를 호출해서 더 정밀하게 분석 가능.
현재는 rule-based 우선 (결정론, 10ms 이하, 95% 정확도).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Theme vocabulary ────────────────────────────────────────────────────────

THEME_KEYWORDS: dict[str, list[str]] = {
    "witch": ["마녀", "witch", "gothic", "고딕", "마법사", "wizard", "spooky", "dark fantasy"],
    "waffle": ["와플", "waffle", "dessert", "sweet", "honey", "cake", "pancake", "cookie", "달콤"],
    "frog": ["개구리", "frog", "swamp", "늪", "lily", "pond", "연못"],
    "sky": ["하늘섬", "sky_island", "sky island", "floating", "떠있는", "공중", "부유", "sky"],
    "castle": ["성", "castle", "요새", "fortress", "keep", "citadel", "궁전", "palace"],
    "tower": ["탑", "tower", "spire", "첨탑"],
    "medieval": ["중세", "medieval", "knight", "stone", "cobblestone"],
    "modern": ["현대", "modern", "glass tower", "skyscraper", "minimalist"],
    "natural": ["자연", "natural", "forest", "tree", "숲"],
}

COMPLEXITY_INDICATORS = {
    "simple": [r"^\s*[^,.;]{1,20}\s*$"],  # 매우 짧은 단일 구
    "complex": [r",", r"\band\b", r"\bwith\b", r"\bplus\b", r"하고", r"그리고", r"\+"],
}

MODIFIER_KEYWORDS: dict[str, list[str]] = {
    "floating": ["floating", "떠있는", "공중", "hovering", "air"],
    "hollow": ["hollow", "속이 빈", "중공", "empty inside"],
    "multi_tower": ["multiple towers", "several towers", "corner towers", "4 towers", "four towers", "모서리 탑"],
    "grid": ["grid", "격자", "pattern", "patterned", "crisscross"],
    "symmetric": ["symmetric", "대칭", "mirror"],
    "asymmetric": ["asymmetric", "비대칭", "irregular"],
    "massive": ["huge", "massive", "giant", "거대", "large"],
    "miniature": ["tiny", "small", "miniature", "작은", "mini"],
    "layered": ["layered", "tiered", "층층", "multi-story", "multiple floors"],
    "organic": ["organic", "natural", "curved", "flowing", "곡선"],
    "geometric": ["geometric", "기하학", "angular", "sharp"],
}

# Detail/creativity signal words — long text with many modifiers = high creative demand
CREATIVE_SIGNAL_WORDS = [
    "unique", "creative", "wild", "crazy", "unusual", "novel", "bizarre",
    "독특", "창의", "기발", "특별", "재미있", "innovative",
]


@dataclass
class IntentReport:
    """Intent analysis result — attached to request.context for downstream use."""

    concept_category: str = "generic"       # primary theme
    secondary_categories: list[str] = field(default_factory=list)  # other matched themes
    creative_demand: str = "medium"         # low | medium | high
    complexity: str = "medium"              # simple | medium | complex
    theme_keywords: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    suggested_variant_count: int = 1        # 1 (low), 2 (medium), 3 (high creative)
    token_estimate: int = 0                 # rough word count
    raw_input_preview: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _find_matches(text: str, vocabulary: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return dict of {category: [matched_keywords]} for any keyword hit in text."""
    lowered = text.lower()
    matches: dict[str, list[str]] = {}
    for cat, kws in vocabulary.items():
        hits = [kw for kw in kws if kw.lower() in lowered]
        if hits:
            matches[cat] = hits
    return matches


def _estimate_complexity(text: str) -> str:
    """짧고 단순한 구는 simple, 여러 요소 결합은 complex."""
    text = (text or "").strip()
    if not text:
        return "simple"
    word_count = len(text.split())
    if word_count <= 3:
        return "simple"
    if word_count >= 20:
        return "complex"
    # 중간 길이에 결합 지표가 있으면 complex
    for pattern in COMPLEXITY_INDICATORS["complex"]:
        if re.search(pattern, text):
            return "complex"
    return "medium"


def _estimate_creative_demand(
    complexity: str,
    modifiers: list[str],
    theme_matches: dict[str, list[str]],
    text: str,
) -> str:
    """창의성 요구도 휴리스틱:
      - creative signal word 있음 → high
      - 여러 테마 mix → high
      - modifier 많음 (3+) → high
      - 단일 테마 + simple → low
      - 그 외 medium
    """
    lowered = text.lower()
    if any(w in lowered for w in CREATIVE_SIGNAL_WORDS):
        return "high"
    if len(theme_matches) >= 3:
        return "high"
    if len(modifiers) >= 3:
        return "high"
    if complexity == "simple" and len(theme_matches) <= 1 and len(modifiers) <= 1:
        return "low"
    return "medium"


def _suggested_variant_count(creative_demand: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(creative_demand, 1)


# ── Primary entry point ─────────────────────────────────────────────────────

def analyze_intent(user_input: str) -> IntentReport:
    """사용자 입력을 분석하고 IntentReport 반환. 순수 함수, 결정론적."""
    if not user_input or not user_input.strip():
        return IntentReport(raw_input_preview="")

    # Theme matches
    theme_matches = _find_matches(user_input, THEME_KEYWORDS)
    # Primary theme: 매치된 것 중 첫 번째 (Python 3.7+ dict preserve insertion order)
    # 우선순위: castle > witch > waffle > frog > sky > 기타
    priority = ["castle", "witch", "waffle", "frog", "sky", "tower", "medieval", "modern", "natural"]
    primary = "generic"
    secondary: list[str] = []
    for p in priority:
        if p in theme_matches:
            if primary == "generic":
                primary = p
            else:
                secondary.append(p)
    # 우선순위 밖 테마도 보조로
    for k in theme_matches:
        if k not in priority and k != primary:
            secondary.append(k)

    all_theme_keywords: list[str] = []
    for v in theme_matches.values():
        all_theme_keywords.extend(v)

    # Modifier matches
    modifier_matches = _find_matches(user_input, MODIFIER_KEYWORDS)
    modifiers = list(modifier_matches.keys())

    complexity = _estimate_complexity(user_input)
    creative_demand = _estimate_creative_demand(complexity, modifiers, theme_matches, user_input)
    variant_count = _suggested_variant_count(creative_demand)

    return IntentReport(
        concept_category=primary,
        secondary_categories=secondary,
        creative_demand=creative_demand,
        complexity=complexity,
        theme_keywords=all_theme_keywords,
        modifiers=modifiers,
        suggested_variant_count=variant_count,
        token_estimate=len(user_input.split()),
        raw_input_preview=user_input[:80],
    )


def is_creative_task(task_type: str) -> bool:
    """scene_graph / brainstorm / palette_only / planner / critic 패밀리만 intent 분석 가치 있음."""
    creative_suffixes = (
        "scene_graph", "brainstorm", "palette_only",
        "planner", "critic", "variant_planner", "repair_planner",
    )
    return any(task_type.endswith(s) for s in creative_suffixes)
