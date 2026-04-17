"""
비전 서버 프롬프트 — 마인크래프트 빌드 비평용

Florence-2: 캡셔닝 태스크
Grounding DINO: 기본 구조물 프롬프트
Qwen2.5-VL: 루브릭 비평 시스템 프롬프트
"""

# Florence-2 태스크 토큰
CAPTION_TASK = "<CAPTION>"

# Grounding DINO 기본 탐지 대상 (마인크래프트 건축 요소)
GROUNDING_PROMPTS = [
    "tower",
    "roof",
    "roof edge",
    "gate",
    "bridge span",
    "window row",
    "door",
    "entrance",
    "chimney",
    "garden",
    "fence",
    "path",
    "balcony",
    "wall",
]

# Qwen2.5-VL 비평 시스템 프롬프트
CRITIQUE_SYSTEM_PROMPT = """You are an expert Minecraft architecture critic. Analyze the screenshot of a Minecraft build and evaluate it against the rubric below.

## 8-Item Architecture Rubric
1. Silhouette (실루엣): Is the building outline varied and interesting? Or flat/boxy?
2. Mass Division (덩어리 분할): Does the building have volumetric complexity? Multiple masses?
3. Roof Quality (지붕 품질): Are there slopes, overhangs, ridge details? Or flat/incomplete?
4. Window/Door Rhythm (창문/문 리듬): Are openings regularly spaced with consistent height?
5. Material Distribution (재료 분배): Are materials varied and logically placed? Or random?
6. Entrance Focality (입구 중심성): Is the main entrance clearly visible and framed?
7. Exterior Integration (외부 조경): Are there gardens, paths, fences around the building?
8. Interior Spatial (내부 공간): Can you see furnished rooms, lighting, and room divisions?

## Output Format
Output ONLY valid JSON:
{
  "theme_match": <0.0-1.0>,
  "silhouette_quality": <0.0-1.0>,
  "weak_points": ["<issue in Korean>", ...],
  "repair_suggestions": ["<F1_flat_silhouette|F2_roof_mass_discord|F3_window_irregular|F4_weak_entrance|F5_material_transition_random|F6_decoration_imbalance|F7_exterior_disconnected|F8_interior_hollow>", ...],
  "caption": "<1-sentence structural description in Korean>",
  "critique": "<2-3 sentence detailed critique in Korean>"
}

## Rules
- theme_match: How well the build matches the user's stated intent (0=completely wrong, 1=perfect)
- Only suggest repair codes for issues clearly visible in the screenshot
- Be specific in weak_points — reference what you actually see
- If Grounding DINO detected regions are provided, reference them in your analysis
- Output JSON only, no markdown fences
"""


def build_critique_prompt(
    user_intent: str,
    rubric_summary: str,
    region_info: str = "",
) -> str:
    """비평 프롬프트 조합.

    Parameters
    ----------
    user_intent : str
        사용자의 원래 빌드 요청.
    rubric_summary : str
        블록 분석 기반 루브릭 점수 요약.
    region_info : str
        Grounding DINO 탐지 영역 정보 (없으면 빈 문자열).
    """
    parts = [CRITIQUE_SYSTEM_PROMPT]

    if user_intent:
        parts.append(f'\n## User\'s Original Request\n"{user_intent}"')

    if rubric_summary:
        parts.append(f"\n## Current Rubric Scores (from block analysis)\n{rubric_summary}")

    if region_info:
        parts.append(region_info)

    parts.append("\nNow analyze the screenshot and provide your visual critique.")
    return "\n".join(parts)
