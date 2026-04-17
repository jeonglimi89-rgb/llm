"""
core/intent_parser.py — 자연어 의도 해석기

v1: 규칙 기반 (키워드 매칭 + 정규식 + 기존 design_foundation.py 패턴 활용)
v2: 로컬 LLM + constrained decoding (이 파일의 _llm_parse 메서드로 교체)
v3: 프로젝트별 LoRA adapter 적용

핵심 원칙:
- 출력은 항상 ParsedIntent 구조체 (자유 텍스트 금지)
- confidence가 낮으면 ambiguities에 불확실 요소 기록
- LLM이 없어도 동작해야 함 (v1 규칙이 항상 fallback)
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .models import IntentType, ParsedIntent
from .schema_registry import SchemaRegistry


# ---------------------------------------------------------------------------
# 한국어 의도 분류 패턴
# ---------------------------------------------------------------------------

_CREATE_PATTERNS = [
    r"만들어", r"생성", r"새로", r"추가해", r"추가$", r"추가\b", r"넣어", r"그려",
    r"설계해", r"디자인해", r"제안해", r"올릴게", r"올려", r"업로드",
]
_MODIFY_PATTERNS = [
    r"바꿔", r"변경", r"수정", r"고쳐", r"줄여", r"늘려", r"키워",
    r"낮춰", r"높여", r"옮겨", r"이동", r"조정", r"정리해",
    r"넓혀", r"좁혀", r"분리해", r"사용해", r"숨겨", r"내보내",
    r"경사", r"짜줘", r"쉬워야", r"적게",
    r"\d{2,4}\s*mm\s*로",
    r"다시\s*(?:생성|만들|그려|짜|정리|배치)",  # "다시 X" = 재생성 = modify (2점)
    r"다시\s*(?:생성|만들|그려|짜|정리|배치)",  # 가중 복제 — "다시"가 있으면 modify 강화
]
_EXPLORE_PATTERNS = [
    r"다른\s*안", r"대안", r"후보", r"변형",
    r"다양하게", r"여러\s*가지",
]
_COMPARE_PATTERNS = [
    r"비교",
]
_SELECT_PATTERNS = [
    r"번째.*(?:로|으로|선택|갈게|할게)", r"이걸로", r"이\s*안",
    r"선택", r"골라", r"결정",
    r"(?:안|안으)로\s*(?:바꿔|갈게|할게|결정)",
    r"하이브리드\s*안", r"안으로\s*바꿔",
]
_DELETE_PATTERNS = [
    r"삭제", r"제거", r"없애", r"지워",
]
_UNDO_PATTERNS = [
    r"되돌려", r"취소", r"원래대로", r"롤백",
]
_QUERY_PATTERNS = [
    r"뭐야", r"알려줘$", r"어떻게\s*되", r"왜\s", r"무엇", r"설명해",
    r"현재\s*상태",
]

# ---------------------------------------------------------------------------
# 대상 객체 분류 패턴
# ---------------------------------------------------------------------------

_TARGET_PATTERNS: dict[str, list[str]] = {
    "concept": [r"컨셉", r"구조안", r"하이브리드안", r"(?:번째|번)\s*안"],
    "module": [r"(?<!충전\s)모듈"],  # "충전 모듈"은 wiring으로 가야 하므로 제외
    "dimension": [r"(?<!치수)치수(?!선)", r"크기", r"폭", r"높이", r"깊이", r"가로", r"세로", r"너비",
                  r"두께", r"팔\s*길이", r"공간", r"\d{2,4}\s*mm"],
    "bom_item": [r"부품", r"BOM", r"bom", r"재료", r"나사"],
    "risk": [r"리스크", r"위험"],
    "wiring_route": [r"배선(?!\s*레이어)", r"전선", r"와이어", r"케이블",
                     r"센서선", r"전원선", r"제어선", r"충전\s*모듈", r"충전부"],
    "connector": [r"커넥터", r"USB", r"usb"],
    "pcb": [r"PCB", r"pcb", r"기판"],
    "drainage_path": [r"배수", r"급수", r"유체", r"호스", r"역류", r"배수\s*라인"],
    "sealing_zone": [r"실링", r"밀봉", r"방수"],
    "maintenance_path": [r"유지보수", r"분해", r"교체", r"펌프\s*교체"],
    "fabrication_mode": [r"제작\s*방식", r"3[Dd]\s*프린팅", r"3d_printing"],
    "sketch": [r"스케치", r"그림", r"올릴게"],
    "requirement": [r"요구사항", r"요구", r"예산", r"환경", r"타깃\s*사용자", r"대상\s*사용자"],
    # Drawing AI 전용
    "view": [r"뷰", r"정면도", r"측면도", r"평면도", r"단면도", r"상세도", r"아이소메트릭", r"분해도"],
    "annotation": [r"주석", r"라벨", r"치수선", r"기호"],
    "system_layer": [r"레이어", r"배선\s*레이어"],
}

# ---------------------------------------------------------------------------
# 제약 조건 추출 패턴
# ---------------------------------------------------------------------------

_DIMENSION_EXTRACT = [
    (r"전체\s*폭[^\d]*(\d{2,4})\s*mm", "dimensions.overall_width_mm"),
    (r"가로[^\d]*(\d{2,4})\s*mm", "dimensions.overall_width_mm"),
    (r"폭[^\d]*(\d{2,4})\s*mm", "dimensions.overall_width_mm"),
    (r"너비[^\d]*(\d{2,4})\s*mm", "dimensions.overall_width_mm"),
    (r"전체\s*높이[^\d]*(\d{2,4})\s*mm", "dimensions.overall_height_mm"),
    (r"높이[^\d]*(\d{2,4})\s*mm", "dimensions.overall_height_mm"),
    (r"세로[^\d]*(\d{2,4})\s*mm", "dimensions.overall_height_mm"),
    (r"전체\s*(?:깊이|두께)[^\d]*(\d{2,4})\s*mm", "dimensions.overall_depth_mm"),
    (r"깊이[^\d]*(\d{2,4})\s*mm", "dimensions.overall_depth_mm"),
    (r"두께[^\d]*(\d{2,4})\s*mm", "dimensions.overall_depth_mm"),
]

_QUALITY_KEYWORDS = {
    "미니멀": ("style", "minimal"),
    "심플": ("style", "minimal"),
    "단순": ("style", "minimal"),
    "고급": ("budget_level", "high"),
    "저렴": ("budget_level", "low"),
    "저비용": ("budget_level", "low"),
    "경량": ("weight", "lightweight"),
    "가볍": ("weight", "lightweight"),
    "가벼": ("weight", "lightweight"),   # ㅂ불규칙: 가벼운/가벼워/가벼워서
    "방수": ("waterproof", True),
    "접이식": ("form", "foldable"),
    "소형": ("size_goal", "compact"),
    "대형": ("size_goal", "large"),
    "배터리": ("power_type", "battery"),
    "무선": ("power_type", "wireless"),
    "유선": ("power_type", "wired"),
}

_ORDINAL_MAP = {
    "첫": 0, "첫번째": 0, "첫 번째": 0, "1": 0, "1번": 0, "1번째": 0,
    "두": 1, "두번째": 1, "두 번째": 1, "2": 1, "2번": 1, "2번째": 1,
    "세": 2, "세번째": 2, "세 번째": 2, "3": 2, "3번": 2, "3번째": 2,
}


class IntentParserModule:

    def __init__(self, schema_registry: SchemaRegistry, project_type: str):
        self.schema_registry = schema_registry
        self.project_type = project_type
        self.llm_backend = None  # v2에서 설정

    def parse(self, user_input: str, context: Optional[dict] = None) -> ParsedIntent:
        """
        사용자 입력을 ParsedIntent로 변환.
        v1: 규칙 기반
        v2: LLM 사용 (llm_backend가 설정된 경우)
        """
        context = context or {}

        if self.llm_backend is not None:
            return self._llm_parse(user_input, context)
        return self._rule_based_parse(user_input, context)

    # ------------------------------------------------------------------
    # v1: 규칙 기반 파싱
    # ------------------------------------------------------------------

    def _rule_based_parse(self, user_input: str, context: dict) -> ParsedIntent:
        text = user_input.strip()

        intent_type = self._classify_intent(text)
        target = self._extract_target(text)
        constraints = self._extract_constraints(text)
        scope = self._extract_scope(text)
        reference_id = self._extract_reference(text, context)
        confidence = self._compute_confidence(text, intent_type, target)
        ambiguities = self._find_ambiguities(text, intent_type, target, constraints)

        return ParsedIntent(
            intent_type=intent_type,
            target_object=target,
            constraints=constraints,
            modification_scope=scope,
            reference_id=reference_id,
            confidence=confidence,
            ambiguities=ambiguities,
            raw_text=text,
        )

    def _classify_intent(self, text: str) -> IntentType:
        """동사 패턴 매칭으로 의도 유형 분류"""
        scores: dict[IntentType, int] = {}

        for pattern in _CREATE_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.CREATE_NEW] = scores.get(IntentType.CREATE_NEW, 0) + 1

        for pattern in _MODIFY_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.MODIFY_EXISTING] = scores.get(IntentType.MODIFY_EXISTING, 0) + 1

        for pattern in _EXPLORE_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.EXPLORE_VARIANTS] = scores.get(IntentType.EXPLORE_VARIANTS, 0) + 1

        for pattern in _COMPARE_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.COMPARE] = scores.get(IntentType.COMPARE, 0) + 1

        for pattern in _SELECT_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.SELECT] = scores.get(IntentType.SELECT, 0) + 1

        for pattern in _DELETE_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.DELETE] = scores.get(IntentType.DELETE, 0) + 1

        for pattern in _UNDO_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.UNDO] = scores.get(IntentType.UNDO, 0) + 1

        for pattern in _QUERY_PATTERNS:
            if re.search(pattern, text):
                scores[IntentType.QUERY] = scores.get(IntentType.QUERY, 0) + 1

        if not scores:
            # 수치가 포함되어 있으면 수정으로 추정
            if re.search(r"\d{2,4}\s*mm", text):
                return IntentType.MODIFY_EXISTING
            # "~해줘/~해/~줘" 종결이면 modify가 query보다 우선
            if re.search(r"(?:해줘|해\s*$|줘\s*$)", text):
                return IntentType.MODIFY_EXISTING
            return IntentType.QUERY

        # "보여줘"만 잡힌 경우: 다른 동사가 있으면 그쪽 우선
        if IntentType.QUERY in scores and len(scores) > 1:
            non_query = {k: v for k, v in scores.items() if k != IntentType.QUERY}
            if non_query:
                return max(non_query, key=lambda k: non_query[k])

        return max(scores, key=lambda k: scores[k])

    def _extract_target(self, text: str) -> str:
        """명사 패턴 매칭으로 대상 객체 추출"""
        # 복합 패턴 우선 처리 (특정 문맥에서 target 오버라이드)
        compound_rules = [
            # "충전 모듈" + "물/습기/방수" 컨텍스트 → wiring_route
            (r"충전.*(?:물|습기|방수|닿지)", "wiring_route"),
            # "배선 레이어" → system_layer
            (r"배선\s*레이어", "system_layer"),
            # "치수선" → annotation (not dimension)
            (r"치수선", "annotation"),
        ]
        for pattern, target in compound_rules:
            if re.search(pattern, text):
                return target

        scores: dict[str, int] = {}
        for target_name, patterns in _TARGET_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    scores[target_name] = scores.get(target_name, 0) + 1

        if not scores:
            return "general"

        return max(scores, key=lambda k: scores[k])

    def _extract_constraints(self, text: str) -> dict[str, Any]:
        """제약 조건 추출 (수치 + 키워드)"""
        constraints: dict[str, Any] = {}

        # 수치 치수 추출
        for pattern, field_path in _DIMENSION_EXTRACT:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                constraints[field_path] = int(match.group(1))

        # 품질/속성 키워드 추출
        for keyword, (key, value) in _QUALITY_KEYWORDS.items():
            if keyword in text:
                constraints[key] = value

        return constraints

    def _extract_scope(self, text: str) -> Optional[str]:
        """수정 범위 한정 추출 ('~만', '~부분만' 등)"""
        scope_pattern = r"([\w]+)(?:만|부분만|쪽만)\s"
        match = re.search(scope_pattern, text)
        if match:
            return match.group(1)

        # path_aliases에서 매칭 시도
        resolved = self.schema_registry.resolve_alias(self.project_type, text)
        if resolved and "/" in resolved:
            # 특정 경로가 식별되면 scope로 사용
            return resolved

        return None

    def _extract_reference(self, text: str, context: dict) -> Optional[str]:
        """참조 ID 추출 (서수 표현 또는 context의 현재 ID)"""
        for token, idx in _ORDINAL_MAP.items():
            if token in text:
                # context에서 해당 인덱스의 ID를 찾음
                concepts = context.get("concepts", [])
                if idx < len(concepts):
                    cid = concepts[idx]
                    if isinstance(cid, dict):
                        return cid.get("option_id", cid.get("id"))
                    return str(cid)

        return context.get("current_artifact_id")

    def _compute_confidence(
        self, text: str, intent_type: IntentType, target: str
    ) -> float:
        """해석 신뢰도 계산"""
        confidence = 0.5

        # 의도가 명확한 동사가 있으면 +0.2
        if intent_type != IntentType.QUERY:
            confidence += 0.2

        # 대상이 특정되면 +0.15
        if target != "general":
            confidence += 0.15

        # 수치가 포함되면 +0.1
        if re.search(r"\d{2,4}\s*mm", text):
            confidence += 0.1

        # 텍스트가 너무 짧으면 -0.1
        if len(text) < 5:
            confidence -= 0.1

        return min(1.0, max(0.0, confidence))

    def _find_ambiguities(
        self,
        text: str,
        intent_type: IntentType,
        target: str,
        constraints: dict,
    ) -> list[str]:
        """불확실한 해석 요소 목록"""
        ambiguities = []

        if target == "general":
            ambiguities.append("대상 객체를 특정하지 못함")

        if intent_type == IntentType.MODIFY_EXISTING and not constraints:
            ambiguities.append("수정 요청이지만 구체적 수치/속성이 없음")

        # "이런 느낌으로" 같은 추상 표현
        if re.search(r"느낌|분위기|스타일|감성", text):
            ambiguities.append("추상적 표현 — 구체적 파라미터 매핑 불확실")

        # "좀 더" 같은 상대적 표현 (수치 없이)
        if re.search(r"좀\s*더|조금|살짝|많이|훨씬", text) and not constraints:
            ambiguities.append("상대적 수정 표현이지만 기준값/변화량 불명확")

        return ambiguities

    # ------------------------------------------------------------------
    # v2: LLM 기반 파싱 (placeholder)
    # ------------------------------------------------------------------

    def _llm_parse(self, user_input: str, context: dict) -> ParsedIntent:
        """
        v2: 로컬 LLM + constrained decoding으로 ParsedIntent JSON 출력.
        규칙 기반 결과를 힌트로 제공하여 정확도 향상.
        LLM 실패 시 규칙 기반으로 자동 fallback.
        """
        # 규칙 기반 결과를 힌트로 사용
        rule_result = self._rule_based_parse(user_input, context)

        intent_schema = self.schema_registry.get_intent_schema(self.project_type)

        try:
            result = self.llm_backend.parse_intent(
                user_input=user_input,
                project_type=self.project_type,
                context={
                    **context,
                    "rule_hint": {
                        "intent_type": rule_result.intent_type.value,
                        "target_object": rule_result.target_object,
                        "constraints": rule_result.constraints,
                    },
                },
                intent_schema=intent_schema,
            )
            return ParsedIntent.from_dict(result)
        except Exception:
            # LLM 실패 시 규칙 기반 fallback
            return rule_result
