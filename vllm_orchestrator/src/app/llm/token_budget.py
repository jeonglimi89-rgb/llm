"""
token_budget.py - CPU 환경 토큰 예산 관리

슬롯 추출 결과는 보통 50~150 토큰. 512는 과잉.
CPU에서는 max_tokens를 줄이면 생성이 빨리 멈춰서 latency 감소.
"""
from __future__ import annotations


# 태스크별 최대 출력 토큰 (CPU 최적화)
OUTPUT_BUDGET = {
    "strict_json": 768,     # GPU 7B: JSON 완결성 우선 (복잡한 build_plan은 400+ tokens 소요)
    "creative_json": 800,   # 창작: 14B-AWQ ctx=2048 — scene_graph prompt(~1120) + input(~100) + output(800) ≈ 2020
    "fast_chat": 128,       # 짧은 응답
    "long_context": 768,    # 요약은 조금 짧게
    "embedding": 1,
}

# 프롬프트 최대 글자 수
PROMPT_CHAR_LIMIT = {
    "strict_json": 1500,    # was 2000 → 좀 더 타이트
    "creative_json": 4500,  # 창작: scene_graph prompt + 2-shot 예시 ~4000 chars
    "fast_chat": 800,
    "long_context": 3000,
}

# 풀별 temperature (기본값 override)
POOL_TEMPERATURE = {
    "strict_json": 0.01,
    "creative_json": 0.7,   # 창작에 다양성 부여
    "fast_chat": 0.3,
    "long_context": 0.1,
    "embedding": 0.0,
}


def get_temperature(pool_type: str) -> float:
    return POOL_TEMPERATURE.get(pool_type, 0.01)


def get_output_budget(pool_type: str) -> int:
    return OUTPUT_BUDGET.get(pool_type, 256)


def trim_prompt(text: str, pool_type: str) -> str:
    limit = PROMPT_CHAR_LIMIT.get(pool_type, 1500)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"
