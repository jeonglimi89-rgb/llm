"""
review/semantic_validators.py — 결정론적 의미 검증 detector 모음

각 detector 는 *순수 함수* 다. LLM 호출 없음. 입력은 (parsed_payload[, context])
이고 출력은 ``DetectorResult`` (passed bool + evidence list + 관련 카테고리).

이 detector 들은 review/layered.py 의 5개 게이트(특히 language, semantic,
domain_guard) 안쪽에서 호출되며, review/task_contracts.py 가 이들을 태스크별로
조립한다.

다루는 false positive 패턴 (HR-001 ~ HR-012, 2026-03-30 verification report 기반)
=================================================================================
HR-001 builder.requirement_parse: 키가 중국어 (楼层, 户型)             → wrong_key_locale
HR-004 cad.constraint_parse:      validator-shape ({valid,message,error}) → validator_shaped_response
HR-008 minecraft.style_check:     CSS 속성 (font_family, padding, …)     → css_property_leak
HR-011 animation.camera_intent:   example.com URL 환각                    → hallucinated_external_reference
HR-012 animation.lighting_intent: 영어 reasoning + "outside night" 오역    → wrong_language + semantic_mistranslation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .judgment import Severity
from .layered import FailureCategory


# ---------------------------------------------------------------------------
# DetectorResult
# ---------------------------------------------------------------------------

@dataclass
class DetectorResult:
    """단일 detector 결과"""
    passed: bool
    severity: str = Severity.INFO.value
    failure_category: str = FailureCategory.NONE.value
    rationale: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. 언어 / 문자 종류
# ---------------------------------------------------------------------------

# CJK 통합 한자 (가장 흔한 영역). 한국 한자(漢字)도 일부 포함되나 우리 도메인
# 출력은 한글 키만 허용하므로 한자 키는 거부한다.
_CJK_HAN_RE   = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_HANGUL_RE    = re.compile(r"[\uac00-\ud7af]")
_LATIN_RE     = re.compile(r"[A-Za-z]")
_KATAKANA_RE  = re.compile(r"[\u30a0-\u30ff]")
_HIRAGANA_RE  = re.compile(r"[\u3040-\u309f]")


def _walk(node: Any, path: str = "$"):
    """parsed payload 를 (path, key, value) 로 깊이우선 순회.

    yields tuples of (parent_path, key_or_index, value)
    """
    if isinstance(node, dict):
        for k, v in node.items():
            child_path = f"{path}.{k}"
            yield (path, k, v)
            yield from _walk(v, child_path)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            child_path = f"{path}[{i}]"
            yield (path, i, v)
            yield from _walk(v, child_path)


def _all_keys(node: Any) -> list[tuple[str, str]]:
    """payload 안의 모든 (path, key) 페어 (string 키만)."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k in node.keys():
            if isinstance(k, str):
                out.append(("$", k))
        for k, v in node.items():
            child_path = f"$.{k}"
            for sub_path, sub_key in _all_keys(v):
                out.append((sub_path.replace("$", child_path, 1), sub_key))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            child_path = f"$[{i}]"
            for sub_path, sub_key in _all_keys(v):
                out.append((sub_path.replace("$", child_path, 1), sub_key))
    return out


def _all_string_values(node: Any) -> list[tuple[str, str]]:
    """payload 안의 모든 (path, string_value) 페어."""
    out: list[tuple[str, str]] = []
    def _rec(n: Any, path: str) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                _rec(v, f"{path}.{k}")
        elif isinstance(n, list):
            for i, v in enumerate(n):
                _rec(v, f"{path}[{i}]")
        elif isinstance(n, str):
            out.append((path, n))
    _rec(node, "$")
    return out


def detect_chinese_keys(payload: Any) -> DetectorResult:
    """payload 안 어딘가에 한자(CJK Han)로 된 키가 있으면 fail.

    HR-001 의 ``楼层``, ``户型`` 류를 잡는다. 한국 도메인 출력에서는 발생해서는
    안 된다. 발견 시 wrong_key_locale 카테고리로 표시.
    """
    bad: list[dict[str, Any]] = []
    for path, key in _all_keys(payload):
        if isinstance(key, str) and _CJK_HAN_RE.search(key):
            bad.append({"path": path, "key": key, "type": "chinese_key"})
    if not bad:
        return DetectorResult(passed=True)
    return DetectorResult(
        passed=False,
        severity=Severity.CRITICAL.value,
        failure_category=FailureCategory.WRONG_KEY_LOCALE.value,
        rationale=f"한자 키 {len(bad)}개 발견 (한국어 도메인 출력 금지)",
        evidence=bad,
    )


def detect_japanese_in_keys(payload: Any) -> DetectorResult:
    """가나(히라가나/카타카나) 키가 있으면 fail."""
    bad: list[dict[str, Any]] = []
    for path, key in _all_keys(payload):
        if isinstance(key, str) and (_KATAKANA_RE.search(key) or _HIRAGANA_RE.search(key)):
            bad.append({"path": path, "key": key, "type": "japanese_key"})
    if not bad:
        return DetectorResult(passed=True)
    return DetectorResult(
        passed=False,
        severity=Severity.CRITICAL.value,
        failure_category=FailureCategory.WRONG_KEY_LOCALE.value,
        rationale=f"가나 키 {len(bad)}개 발견",
        evidence=bad,
    )


def detect_non_korean_in_required_field(
    payload: Any,
    required_korean_fields: Iterable[str],
) -> DetectorResult:
    """지정된 필드 (예: ``intent``, ``reasoning``) 의 값이 한국어 dominant 여야 한다.

    단순히 한 글자라도 한글이 있으면 통과 — 같은 약한 검사는 HR-012 처럼
    영어 문장 안에 '비 오는 밤' 한 구절만 인용된 케이스를 잡지 못한다.
    그래서 ratio 기반으로 잡는다:

      - 한글 0자                                      → fail
      - 라틴 문자 ≥ 20자 그리고 라틴 ≥ 3 * 한글       → fail (영문 dominant)

    이러면 HR-012 처럼 영어 문장 안 짧은 한국어 인용만으로는 통과하지 못한다.
    """
    fields = set(required_korean_fields)
    bad: list[dict[str, Any]] = []

    def _rec(n: Any, path: str) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if k in fields and isinstance(v, str) and v.strip():
                    n_hangul = len(_HANGUL_RE.findall(v))
                    n_latin  = len(_LATIN_RE.findall(v))
                    fail = False
                    reason = ""
                    if n_hangul == 0 and n_latin >= 4:
                        fail = True
                        reason = "no_hangul_latin_present"
                    elif n_latin >= 20 and n_latin >= 3 * max(n_hangul, 1):
                        fail = True
                        reason = "latin_dominant_over_hangul"
                    if fail:
                        bad.append({
                            "path": f"{path}.{k}",
                            "field": k,
                            "value_preview": v[:80],
                            "n_hangul": n_hangul,
                            "n_latin": n_latin,
                            "reason": reason,
                        })
                _rec(v, f"{path}.{k}")
        elif isinstance(n, list):
            for i, v in enumerate(n):
                _rec(v, f"{path}[{i}]")

    _rec(payload, "$")
    if not bad:
        return DetectorResult(passed=True)
    return DetectorResult(
        passed=False,
        severity=Severity.HIGH.value,
        failure_category=FailureCategory.WRONG_LANGUAGE.value,
        rationale=f"한국어 필수 필드 {len(bad)}개가 영문 dominant",
        evidence=bad,
    )


# ---------------------------------------------------------------------------
# 2. Validator-shape leak (HR-004)
# ---------------------------------------------------------------------------

# 검증기/parser 가 자기 자신을 응답으로 출력하는 패턴.
# 예: {"valid": true, "message": "...", "error": null}
_VALIDATOR_SHAPE_KEY_SETS: list[set[str]] = [
    {"valid", "message", "error"},
    {"valid", "message"},
    {"valid", "error"},
    {"isValid", "message", "error"},
    {"is_valid", "message", "error"},
    {"ok", "message", "error"},
    {"status", "error", "message"},
]


def detect_validator_shape(payload: Any) -> DetectorResult:
    """top-level (또는 1단계 child) 객체가 validator 응답 형태면 fail.

    HR-004 케이스: cad.constraint_parse 가 ``{"valid": true, "message": ..., "error": null}``
    를 뱉었음. 이 모양은 어떤 도메인 슬롯도 표현하지 않는다.
    """
    if not isinstance(payload, dict):
        return DetectorResult(passed=True)

    keys = set(payload.keys())
    for shape in _VALIDATOR_SHAPE_KEY_SETS:
        if shape.issubset(keys) and len(keys - shape) <= 1:
            # validator shape 매치
            return DetectorResult(
                passed=False,
                severity=Severity.CRITICAL.value,
                failure_category=FailureCategory.VALIDATOR_SHAPED_RESPONSE.value,
                rationale=f"validator-shape 응답 감지: keys={sorted(keys)}",
                evidence=[{"keys": sorted(keys), "matched_shape": sorted(shape)}],
            )
    return DetectorResult(passed=True)


# ---------------------------------------------------------------------------
# 3. CSS property leak (HR-008)
# ---------------------------------------------------------------------------

# 웹 CSS / typography 어휘. 마인크래프트 style_check 출력에 등장하면 fail.
_CSS_LEAK_TOKENS = frozenset({
    "font_family", "font-family",
    "font_size", "font-size",
    "font_weight", "font-weight",
    "font_style", "font-style",
    "letter_spacing", "letter-spacing",
    "line_height", "line-height",
    "padding", "margin",
    "border_color", "border-color",
    "border_width", "border-width",
    "border_radius", "border-radius",
    "text_color", "text-color",
    "background_color", "background-color",
    "alignment", "indentation",
    "leading", "kerning",
    "space_after", "space-after",
    "space_before", "space-before",
    "shadow", "box_shadow", "box-shadow",
})


def detect_css_property_leak(payload: Any) -> DetectorResult:
    """payload 어디든 CSS 속성 키가 등장하면 fail.

    HR-008: minecraft.style_check 가 ``font_family``, ``padding``, ``letter_spacing``
    등을 마구 뱉음. 마인크래프트 도메인에는 존재하지 않는 어휘.
    """
    bad: list[dict[str, Any]] = []
    for path, key in _all_keys(payload):
        if isinstance(key, str):
            normalized = key.lower().replace("-", "_")
            if key in _CSS_LEAK_TOKENS or normalized in _CSS_LEAK_TOKENS:
                bad.append({"path": path, "key": key, "token": normalized})
    if not bad:
        return DetectorResult(passed=True)
    return DetectorResult(
        passed=False,
        severity=Severity.HIGH.value,
        failure_category=FailureCategory.CSS_PROPERTY_LEAK.value,
        rationale=f"CSS 속성 키 {len(bad)}개 발견 (마인크래프트 도메인 어휘 아님)",
        evidence=bad[:20],  # cap
    )


# ---------------------------------------------------------------------------
# 4. URL / external reference hallucination (HR-011)
# ---------------------------------------------------------------------------

# http(s)://, www., 도메인 패턴
_URL_RE = re.compile(
    r"(?ix)"
    r"(?:https?://|www\.)"               # http(s):// or www.
    r"[\w.\-]+"
    r"\.[a-z]{2,}"                       # TLD
    r"(?:/[\w./?=&%#:\-]*)?"             # path
)
_BARE_DOMAIN_RE = re.compile(
    r"(?ix)\b"
    r"(?:[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|ai|co|xyz|dev|app|me|info)\b"
)
_KNOWN_PLACEHOLDERS = frozenset({
    "example.com", "example.org", "example.net",
    "test.com", "foo.com", "bar.com",
})


def detect_url_hallucination(payload: Any, *, allow_urls: bool = False) -> DetectorResult:
    """payload string 값 어디든 URL/도메인이 있으면 fail (allow_urls=True 가 아니면).

    HR-011: animation.camera_intent 가 ``"image_url": "https://example.com/image.jpg"``
    같은 값을 환각함. animation 도메인 contract 는 URL 출력을 요구하지 않는다.
    """
    if allow_urls:
        return DetectorResult(passed=True)

    bad: list[dict[str, Any]] = []
    for path, val in _all_string_values(payload):
        url_m = _URL_RE.search(val)
        if url_m:
            bad.append({
                "path": path, "value_preview": val[:80],
                "matched": url_m.group(0), "type": "url",
            })
            continue
        dom_m = _BARE_DOMAIN_RE.search(val)
        if dom_m:
            bad.append({
                "path": path, "value_preview": val[:80],
                "matched": dom_m.group(0), "type": "bare_domain",
            })

    if not bad:
        return DetectorResult(passed=True)

    # placeholder 도메인이 끼어 있으면 더 강한 신호 (확실한 환각)
    has_placeholder = any(
        any(ph in (b.get("matched") or "").lower() for ph in _KNOWN_PLACEHOLDERS)
        for b in bad
    )
    severity = Severity.CRITICAL.value if has_placeholder else Severity.HIGH.value

    return DetectorResult(
        passed=False,
        severity=severity,
        failure_category=FailureCategory.HALLUCINATED_EXTERNAL_REFERENCE.value,
        rationale=f"URL/도메인 환각 {len(bad)}건 (placeholder={has_placeholder})",
        evidence=bad[:20],
    )


# ---------------------------------------------------------------------------
# 5. Semantic anchor / mistranslation (HR-012)
# ---------------------------------------------------------------------------

# 입력 한국어 → 출력 어딘가에 반드시 보존되어야 할 의미 anchor 토큰.
#
# 설계 원칙 (semantic anchor redesign):
# slot extraction 은 "한국어 입력 → 영어 enum/구조화된 JSON" 이 정상 경로이므로,
# 각 anchor 의 required_any 목록에는 3종류의 토큰이 모두 포함되어야 한다:
#   1) 한국어 원문 (직접 보존된 경우)
#   2) 영어 번역어 (LLM이 번역한 경우)
#   3) 영어 enum 값 (도메인 필드의 allowed values)
#   4) 숫자 문자열 (정수 변환된 경우)
# 매칭은 OR — 하나만 있으면 의미 보존으로 인정.
DEFAULT_SEMANTIC_ANCHORS: dict[str, list[str]] = {
    # animation / atmosphere
    "비 오는 밤":   ["비", "밤", "rain", "night", "dark", "wet"],
    "노을빛":       ["노을", "sunset", "warm", "golden", "orange"],
    "공포 장면":    ["공포", "horror", "fear", "dark", "tension"],
    "외로운":       ["외로운", "외로움", "고독", "lonely", "solitude", "isolation"],
    "어둠":         ["어둠", "어두", "dark", "shadow", "low"],
    # builder
    "지하 카페":    ["지하", "카페", "underground", "cafe", "basement"],
    "벽돌 외관":    ["벽돌", "brick"],
    "2층 주택":     ["2층", "2", "주택", "주거", "floor"],
    "마당 넓게":    ["마당", "garden", "yard"],
    "건폐율":       ["건폐율", "coverage", "ratio"],
    # cad / technical
    "PCB":          ["PCB", "pcb", "circuit"],
    "방수":         ["방수", "waterproof", "IP67", "sealed"],
}

# 명백히 lossy 한 영어 표현 (입력 한국어 의미를 잃은 신호)
_LOSSY_ENGLISH_REPLACEMENTS = [
    ("비 오는 밤", "outside night"),     # HR-12: rain night → outside night (틀림)
    ("노을빛",     "sunset only"),
    ("외로운",     "not too crowded"),    # HR-12: 외로운 → not too crowded (의미 손실)
]


def detect_semantic_anchor_loss(
    user_input: str,
    payload: Any,
    *,
    anchors: Optional[dict[str, list[str]]] = None,
) -> DetectorResult:
    """입력에 등장한 한국어 anchor 가 출력 어디에서도 보존되지 않으면 fail.

    부분 문자열 매칭. 출력에 한 번도 등장하지 않으면 lossy 로 본다.
    """
    if not user_input:
        return DetectorResult(passed=True)
    anchors = anchors or DEFAULT_SEMANTIC_ANCHORS

    # payload 전체를 string 으로 평탄화 (숫자 값도 포함해 anchor 매칭 가능하게)
    flat_strings = [v for _, v in _all_string_values(payload)]
    flat_keys    = [k for _, k in _all_keys(payload)]
    # 숫자 값도 문자열로 변환해서 haystack 에 추가 (floors:2 → "2" 매칭 가능)
    flat_nums = []
    if isinstance(payload, dict):
        import json as _json
        for v in _json.dumps(payload, ensure_ascii=False).split(","):
            for tok in v.strip().split(":"):
                tok = tok.strip().strip('"').strip('{').strip('}').strip('[').strip(']')
                if tok:
                    flat_nums.append(tok)
    haystack = " || ".join(flat_strings + [k for k in flat_keys if isinstance(k, str)] + flat_nums)

    bad: list[dict[str, Any]] = []
    for trigger, required_any in anchors.items():
        if trigger in user_input:
            if not any(tok in haystack for tok in required_any):
                bad.append({
                    "trigger_in_input": trigger,
                    "expected_any_of": required_any,
                    "where_searched": "all_strings_and_keys",
                })

    if bad:
        return DetectorResult(
            passed=False,
            severity=Severity.HIGH.value,
            failure_category=FailureCategory.SEMANTIC_MISTRANSLATION.value,
            rationale=f"의미 anchor {len(bad)}개 손실",
            evidence=bad,
        )
    return DetectorResult(passed=True)


def detect_known_lossy_english(
    user_input: str,
    payload: Any,
) -> DetectorResult:
    """알려진 lossy 영어 변환을 잡는다 (HR-012 회귀 방어)."""
    if not user_input:
        return DetectorResult(passed=True)
    flat = [v for _, v in _all_string_values(payload)]
    bad: list[dict[str, Any]] = []
    for ko, en_lossy in _LOSSY_ENGLISH_REPLACEMENTS:
        if ko in user_input:
            for v in flat:
                if en_lossy.lower() in v.lower():
                    bad.append({
                        "ko_input": ko,
                        "lossy_english": en_lossy,
                        "found_value_preview": v[:80],
                    })
    if not bad:
        return DetectorResult(passed=True)
    return DetectorResult(
        passed=False,
        severity=Severity.CRITICAL.value,
        failure_category=FailureCategory.SEMANTIC_MISTRANSLATION.value,
        rationale=f"알려진 lossy 영어 변환 {len(bad)}건",
        evidence=bad,
    )


# ---------------------------------------------------------------------------
# 6. Empty / trivial payload
# ---------------------------------------------------------------------------

def detect_empty_or_trivial_payload(payload: Any) -> DetectorResult:
    """payload 가 None / {} / {"text": ...} 같은 trivial wrapper 면 fail.

    HR-007/HR-009/HR-010: ``{"text": "정면 창문 넓게, 지붕 유지"}`` 같이 입력을
    그대로 다시 감싸는 패턴. 슬롯 추출이 일어나지 않은 신호.
    """
    if payload is None:
        return DetectorResult(
            passed=False,
            severity=Severity.CRITICAL.value,
            failure_category=FailureCategory.EMPTY_OUTPUT.value,
            rationale="payload is None",
            evidence=[{"reason": "none"}],
        )
    if isinstance(payload, dict):
        if not payload:
            return DetectorResult(
                passed=False,
                severity=Severity.CRITICAL.value,
                failure_category=FailureCategory.EMPTY_OUTPUT.value,
                rationale="payload is empty dict",
                evidence=[{"reason": "empty_dict"}],
            )
        # 단일 wrapper 키 (echo)
        if len(payload) == 1:
            only_k = next(iter(payload.keys()))
            if only_k in ("text", "result", "value", "data"):
                v = payload[only_k]
                if isinstance(v, str) and len(v) <= 200:
                    return DetectorResult(
                        passed=False,
                        severity=Severity.HIGH.value,
                        failure_category=FailureCategory.SCHEMA_PASS_BUT_SEMANTIC_FAIL.value,
                        rationale=f"단일 wrapper 키('{only_k}')로 입력을 그대로 echo",
                        evidence=[{"key": only_k, "value_preview": v[:80]}],
                    )
    return DetectorResult(passed=True)


# ---------------------------------------------------------------------------
# 7. Echo detection — 입력 텍스트가 출력 string 어디에 그대로 있는가
# ---------------------------------------------------------------------------

def detect_input_echo(user_input: str, payload: Any, *, min_len: int = 8) -> DetectorResult:
    """입력 한국어 문장 자체가 출력 string 값 중 하나로 그대로 들어 있으면 fail.

    이는 LLM 이 슬롯을 추출하지 않고 입력을 echo 한 신호다.
    """
    if not user_input or len(user_input) < min_len:
        return DetectorResult(passed=True)
    target = user_input.strip()
    for path, val in _all_string_values(payload):
        if target == val.strip():
            return DetectorResult(
                passed=False,
                severity=Severity.HIGH.value,
                failure_category=FailureCategory.SCHEMA_PASS_BUT_SEMANTIC_FAIL.value,
                rationale="출력 string 값이 입력과 동일 (slot extraction 없음)",
                evidence=[{"path": path, "echo_of": target[:80]}],
            )
    return DetectorResult(passed=True)
