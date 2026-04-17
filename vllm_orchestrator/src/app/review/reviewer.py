"""
review/reviewer.py - 도메인별 자동 판정 → ReviewJudgment 변환

엔진/검증기 결과를 표준 ReviewJudgment 스키마로 변환.
LLM critique 아님. 결정론적 규칙 기반.
"""
from __future__ import annotations

from typing import Any
from .judgment import ReviewJudgment, JudgmentItem, Verdict, Severity


def review_builder_plan(plan_result: dict, validate_result: dict | None = None, artifact_id: str = "") -> ReviewJudgment:
    """builder.generate_plan + builder.validate 결과 → ReviewJudgment"""
    items = []
    metadata = plan_result.get("metadata", {})

    # 검증 결과가 있으면 그것을 기반으로 항목 생성
    if validate_result:
        for issue in validate_result.get("issues", []):
            sev_map = {
                "critical": Severity.CRITICAL,
                "warning": Severity.MEDIUM,
                "info": Severity.INFO,
            }
            severity = sev_map.get(issue.get("severity", "info"), Severity.LOW)
            items.append(JudgmentItem(
                category=issue.get("rule", "unknown"),
                severity=severity.value,
                rationale=issue.get("detail", ""),
                evidence={"rule": issue.get("rule"), "source": "builder.validate"},
                recommended_action="평면 재배치 또는 공간 추가" if severity in (Severity.CRITICAL, Severity.HIGH) else "검토 권장",
                confidence=0.95,
            ))

    # 메타 검증
    if metadata.get("total_rooms", 0) < 3:
        items.append(JudgmentItem(
            category="room_count",
            severity=Severity.MEDIUM.value,
            rationale=f"방 수 {metadata.get('total_rooms', 0)}개로 적음",
            evidence={"total_rooms": metadata.get("total_rooms", 0)},
            recommended_action="필수 공간 추가 검토",
            confidence=0.9,
        ))

    # 판정
    has_critical = any(i.severity == "critical" for i in items)
    has_high = any(i.severity == "high" for i in items)
    if has_critical:
        verdict = Verdict.FAIL
    elif has_high or len(items) > 2:
        verdict = Verdict.NEEDS_REVIEW
    else:
        verdict = Verdict.PASS

    val_verdict = validate_result.get("verdict", "pass") if validate_result else "pass"
    auto_pass = val_verdict in ("pass", "warn") and verdict != Verdict.FAIL

    return ReviewJudgment(
        artifact_id=artifact_id or f"builder_{metadata.get('floors', 1)}f_{metadata.get('total_rooms', 0)}r",
        domain="builder",
        task_type="builder.generate_plan",
        verdict=verdict.value,
        items=items,
        summary=f"{metadata.get('floors', 1)}층, {metadata.get('total_rooms', 0)}개 방, {metadata.get('total_area_m2', 0)}m²",
        auto_pass=auto_pass,
        human_required=verdict == Verdict.NEEDS_REVIEW,
    )


def review_minecraft_build(compile_result: dict, palette_result: dict | None = None, artifact_id: str = "") -> ReviewJudgment:
    """minecraft compile + palette validation → ReviewJudgment"""
    items = []
    metadata = compile_result.get("metadata", {})

    if palette_result:
        for issue in palette_result.get("issues", []):
            sev_map = {"critical": Severity.CRITICAL, "warning": Severity.MEDIUM, "info": Severity.INFO}
            sev = sev_map.get(issue.get("severity", "info"), Severity.LOW)
            items.append(JudgmentItem(
                category="palette",
                severity=sev.value,
                rationale=issue.get("detail", ""),
                evidence={"source": "minecraft.validate_palette"},
                recommended_action="블록 팔레트 재구성",
                confidence=0.9,
            ))

        score = palette_result.get("style_score", 0)
        if score < 0.5:
            items.append(JudgmentItem(
                category="style_score",
                severity=Severity.HIGH.value,
                rationale=f"스타일 점수 {score} (낮음)",
                evidence={"style_score": score},
                recommended_action="스타일 일관성 개선",
                confidence=0.85,
            ))

    block_count = metadata.get("block_count", 0)
    if block_count == 0:
        items.append(JudgmentItem(
            category="empty_build",
            severity=Severity.CRITICAL.value,
            rationale="생성된 블록 없음",
            evidence={"block_count": 0},
            recommended_action="입력 슬롯 재확인",
            confidence=1.0,
        ))

    has_critical = any(i.severity == "critical" for i in items)
    has_high = any(i.severity == "high" for i in items)
    verdict = Verdict.FAIL if has_critical else (Verdict.NEEDS_REVIEW if has_high else Verdict.PASS)

    return ReviewJudgment(
        artifact_id=artifact_id or f"mc_{metadata.get('anchor', 'unknown')}_{block_count}b",
        domain="minecraft",
        task_type="minecraft.compile_archetype",
        verdict=verdict.value,
        items=items,
        summary=f"{block_count} blocks, anchor={metadata.get('anchor', '?')}",
        auto_pass=verdict == Verdict.PASS,
        human_required=verdict == Verdict.NEEDS_REVIEW,
    )


def review_cad_design(generate_result: dict, validate_result: dict | None = None, artifact_id: str = "") -> ReviewJudgment:
    """CAD generate_part + validate_geometry → ReviewJudgment"""
    items = []

    if validate_result:
        for issue in validate_result.get("issues", []):
            sev_map = {"critical": Severity.CRITICAL, "high": Severity.HIGH, "warning": Severity.MEDIUM, "info": Severity.INFO}
            sev = sev_map.get(issue.get("severity", "info"), Severity.LOW)
            items.append(JudgmentItem(
                category=issue.get("rule", "geometry"),
                severity=sev.value,
                rationale=issue.get("detail", ""),
                evidence={"source": "cad.validate_geometry", "rule": issue.get("rule")},
                recommended_action=issue.get("fix_hint", "기하 재검토"),
                confidence=0.95,
            ))

    parts = generate_result.get("parts", [])
    if not parts:
        items.append(JudgmentItem(
            category="empty_parts",
            severity=Severity.CRITICAL.value,
            rationale="부품 0개",
            evidence={},
            recommended_action="입력 사양 재확인",
            confidence=1.0,
        ))

    has_critical = any(i.severity == "critical" for i in items)
    has_high = any(i.severity == "high" for i in items)
    verdict = Verdict.FAIL if has_critical else (Verdict.NEEDS_REVIEW if has_high else Verdict.PASS)

    return ReviewJudgment(
        artifact_id=artifact_id or f"cad_{len(parts)}parts",
        domain="cad",
        task_type="cad.generate_part",
        verdict=verdict.value,
        items=items,
        summary=f"{len(parts)} parts, {len(generate_result.get('interfaces', {}).get('electrical', []))} wires, {len(generate_result.get('interfaces', {}).get('drainage', []))} drains",
        auto_pass=verdict == Verdict.PASS,
        human_required=verdict == Verdict.NEEDS_REVIEW,
    )


def review_animation_shot(shot_result: dict, continuity_result: dict | None = None, artifact_id: str = "") -> ReviewJudgment:
    """animation solve_shot + check_continuity → ReviewJudgment"""
    items = []

    if continuity_result:
        for issue in continuity_result.get("issues", []):
            sev_map = {"critical": Severity.CRITICAL, "warning": Severity.MEDIUM, "info": Severity.INFO}
            sev = sev_map.get(issue.get("severity", "info"), Severity.LOW)
            items.append(JudgmentItem(
                category=issue.get("rule", "continuity"),
                severity=sev.value,
                rationale=issue.get("detail", ""),
                evidence={"source": "animation.check_continuity"},
                recommended_action=issue.get("fix_hint", "샷 재구성"),
                confidence=0.9,
            ))

    duration = shot_result.get("duration_frames", 0)
    if duration <= 0:
        items.append(JudgmentItem(
            category="invalid_duration",
            severity=Severity.CRITICAL.value,
            rationale=f"duration_frames={duration}",
            evidence={"duration": duration},
            recommended_action="샷 길이 지정 필요",
            confidence=1.0,
        ))

    has_critical = any(i.severity == "critical" for i in items)
    verdict = Verdict.FAIL if has_critical else (Verdict.NEEDS_REVIEW if items else Verdict.PASS)

    return ReviewJudgment(
        artifact_id=artifact_id or f"anim_shot_{duration}f",
        domain="animation",
        task_type="animation.solve_shot",
        verdict=verdict.value,
        items=items,
        summary=f"shot {duration}f, framing={shot_result.get('camera', {}).get('framing', '?')}",
        auto_pass=verdict == Verdict.PASS,
        human_required=verdict == Verdict.NEEDS_REVIEW,
    )
