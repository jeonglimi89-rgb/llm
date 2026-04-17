"""
orchestration/requirement_extractor.py — Rule-based requirement extraction.

자연어에서 하드 제약 / 소프트 선호 / 도메인별 슬롯을 추출.
LLM 호출 0회 — regex + 키워드 사전 기반.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..domain.profiles import DomainProfile


@dataclass
class RequirementEnvelope:
    target_domain: str
    task_type: str
    user_intent: str
    hard_constraints: list[str] = field(default_factory=list)
    soft_preferences: list[str] = field(default_factory=list)
    expected_artifact: str = ""
    validation_targets: list[str] = field(default_factory=list)
    execution_risk: str = "low"
    domain_specific: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "target_domain": self.target_domain,
            "task_type": self.task_type,
            "user_intent": self.user_intent,
            "hard_constraints": self.hard_constraints,
            "soft_preferences": self.soft_preferences,
            "expected_artifact": self.expected_artifact,
            "validation_targets": self.validation_targets,
            "execution_risk": self.execution_risk,
            "domain_specific": self.domain_specific,
        }


# 하드 제약 마커
_HARD_MARKERS = re.compile(r"(반드시|필수|최소|최대|이상|이하|꼭|무조건|필요)")
# 소프트 선호 마커
_SOFT_MARKERS = re.compile(r"(가능하면|선호|좋겠|원하|했으면|바람직)")
# 숫자+단위 패턴
_NUMERIC_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(mm|cm|m|인치|inch|평|m2|㎡|제곱미터|kg|g|도|°|프레임|frames?|초|s|층)"
)
# 기술 사양 패턴 (하드 제약으로 자동 포함)
_SPEC_PATTERN = re.compile(r"(IP\d{2}|방수|방진|USB[- ]?C|USB[- ]?A|리튬|배터리)")

# 도메인별 추가 추출 패턴
_DOMAIN_PATTERNS: dict[str, dict[str, re.Pattern]] = {
    "cad": {
        "dimensions": re.compile(r"(\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?)\s*(mm|cm|m)?"),
        "material": re.compile(r"(알루미늄|스테인리스|ABS|PP|PE|PVC|나일론|철|구리|실리콘|티타늄|aluminum|steel|stainless|plastic)"),
        "sealing": re.compile(r"(IP\d{2}|방수|방진|밀봉|sealed)"),
    },
    "builder": {
        "floor_count": re.compile(r"(\d+)\s*층"),
        "area": re.compile(r"(\d+(?:\.\d+)?)\s*(평|m2|㎡|제곱미터)"),
        "room_type": re.compile(r"(거실|주방|침실|안방|화장실|현관|서재|발코니|카페|사무실|주차장)"),
    },
    "minecraft": {
        "block_material": re.compile(r"(돌|참나무|스프루스|자작나무|벽돌|유리|울타리|횃불|랜턴|꽃|stone|oak|spruce|birch|brick|glass)"),
        "anchor": re.compile(r"(정면|지붕|내부|입구|창문|벽|탑|정원|발코니|facade|roof|interior|entrance|window|wall|tower|garden)"),
    },
    "animation": {
        "framing": re.compile(r"(클로즈업|와이드|미디엄|풀샷|오버숄더|close.?up|wide|medium|full|over.?shoulder)"),
        "mood": re.compile(r"(슬픈|공포|외로운|따뜻|차가운|극적|어두|밝|평화|긴장|sad|fear|warm|cold|dark|bright|dramatic)"),
        "camera_motion": re.compile(r"(팬|틸트|달리|크레인|트래킹|핸드헬드|pan|tilt|dolly|crane|tracking|handheld)"),
    },
}

# Artifact 예측 테이블
_ARTIFACT_MAP = {
    "cad": "부품 설계 사양 (치수/재질/인터페이스)",
    "builder": "건축 평면도 (층별 공간 배치)",
    "minecraft": "블록 배치 명령 (좌표/재료/보존 목록)",
    "animation": "샷 구성 명세 (프레이밍/무드/카메라/타이밍)",
}


class RequirementExtractor:
    def __init__(self, profiles: dict[str, DomainProfile]):
        self._profiles = profiles

    def extract(
        self,
        user_input: str,
        domain: str,
        task_name: str,
    ) -> RequirementEnvelope:
        text = user_input.strip()

        # 1. Intent: 첫 동사/행위 구절 (간단히 첫 40자)
        intent = text[:60].rstrip(".,!?") if text else ""

        # 2. Hard constraints
        hard = []
        for m in _HARD_MARKERS.finditer(text):
            # 마커 주변 컨텍스트 추출
            start = max(0, m.start() - 15)
            end = min(len(text), m.end() + 30)
            hard.append(text[start:end].strip())
        # 숫자+단위도 하드 제약으로 포함
        for m in _NUMERIC_PATTERN.finditer(text):
            hard.append(m.group(0).strip())
        # 기술 사양 패턴도 하드 제약
        for m in _SPEC_PATTERN.finditer(text):
            hard.append(m.group(0).strip())

        # 3. Soft preferences
        soft = []
        for m in _SOFT_MARKERS.finditer(text):
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 30)
            soft.append(text[start:end].strip())

        # 4. Domain-specific slots
        domain_specific: dict[str, Any] = {}
        patterns = _DOMAIN_PATTERNS.get(domain, {})
        for slot_name, pattern in patterns.items():
            found = pattern.findall(text)
            if found:
                # 패턴이 tuple 을 반환하면 첫 그룹만
                values = [f[0] if isinstance(f, tuple) else f for f in found]
                domain_specific[slot_name] = values if len(values) > 1 else values[0]

        # 5. Validation targets (profile 기반)
        profile = self._profiles.get(domain)
        validation_targets = list(profile.validation_checklist) if profile else []

        # 6. Execution risk
        risk = "low"
        if len(hard) >= 3:
            risk = "medium"
        if len(hard) >= 5 or len(text) > 200:
            risk = "high"

        return RequirementEnvelope(
            target_domain=domain,
            task_type=f"{domain}.{task_name}",
            user_intent=intent,
            hard_constraints=hard,
            soft_preferences=soft,
            expected_artifact=_ARTIFACT_MAP.get(domain, ""),
            validation_targets=validation_targets,
            execution_risk=risk,
            domain_specific=domain_specific,
        )
