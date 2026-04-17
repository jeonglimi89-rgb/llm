"""
test_semantic_validators.py — review/semantic_validators.py 단위 테스트
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.review.semantic_validators import (
    detect_chinese_keys,
    detect_japanese_in_keys,
    detect_non_korean_in_required_field,
    detect_validator_shape,
    detect_css_property_leak,
    detect_url_hallucination,
    detect_semantic_anchor_loss,
    detect_known_lossy_english,
    detect_empty_or_trivial_payload,
    detect_input_echo,
)
from src.app.review.layered import FailureCategory


# ---------------------------------------------------------------------------
# detect_chinese_keys
# ---------------------------------------------------------------------------

def test_chinese_keys_top_level():
    print("  [chinese_keys] top-level CJK key fails")
    res = detect_chinese_keys({"楼层": "2층", "户型": "모던"})
    assert not res.passed
    assert res.failure_category == FailureCategory.WRONG_KEY_LOCALE.value
    assert len(res.evidence) == 2
    print("    OK")


def test_chinese_keys_nested():
    print("  [chinese_keys] nested CJK key fails")
    res = detect_chinese_keys({"meta": {"楼层": "2"}})
    assert not res.passed
    assert res.evidence[0]["key"] == "楼层"
    print("    OK")


def test_chinese_keys_clean_passes():
    print("  [chinese_keys] pure ASCII keys pass")
    res = detect_chinese_keys({"floor": 2, "rooms": [{"name": "거실"}]})
    assert res.passed
    print("    OK")


def test_chinese_keys_korean_value_ok():
    print("  [chinese_keys] Korean *value* (not key) is fine")
    res = detect_chinese_keys({"name": "거실"})
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_japanese_in_keys
# ---------------------------------------------------------------------------

def test_japanese_keys_kana_fails():
    print("  [japanese_keys] kana key fails")
    res = detect_japanese_in_keys({"アンカー": "test"})
    assert not res.passed
    print("    OK")


def test_japanese_keys_hangul_passes():
    print("  [japanese_keys] hangul key is not kana → pass at this gate")
    res = detect_japanese_in_keys({"앵커": "test"})
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_non_korean_in_required_field
# ---------------------------------------------------------------------------

def test_korean_required_pure_english_fails():
    print("  [korean_required] pure English fails")
    res = detect_non_korean_in_required_field(
        {"reasoning": "The user wants something"},
        ["reasoning"],
    )
    assert not res.passed
    assert res.failure_category == FailureCategory.WRONG_LANGUAGE.value
    print("    OK")


def test_korean_required_korean_passes():
    print("  [korean_required] full Korean passes")
    res = detect_non_korean_in_required_field(
        {"reasoning": "사용자가 따뜻한 분위기를 원함"},
        ["reasoning"],
    )
    assert res.passed
    print("    OK")


def test_korean_required_english_with_quoted_korean_fails():
    """HR-12 회귀: 영어 문장 안 짧은 한국어 인용은 통과시키면 안 됨."""
    print("  [korean_required] English-dominant + quoted Korean fails (HR-012)")
    res = detect_non_korean_in_required_field(
        {"reasoning": "The user is expressing a desire for an environment that is not too crowded or noisy, which aligns with the concept of '비 오는 밤' (outside night) in Korean language."},
        ["reasoning"],
    )
    assert not res.passed, "should not pass — Latin >> Hangul"
    print("    OK")


def test_korean_required_short_value_passes():
    print("  [korean_required] very short Latin value (e.g. '2층') passes if non-text")
    res = detect_non_korean_in_required_field({"intent": "외로운 분위기"}, ["intent"])
    assert res.passed
    print("    OK")


def test_korean_required_field_absent_passes():
    print("  [korean_required] required field absent → pass (not enforced presence)")
    res = detect_non_korean_in_required_field({"other": "x"}, ["reasoning"])
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_validator_shape
# ---------------------------------------------------------------------------

def test_validator_shape_basic_fails():
    print("  [validator_shape] {valid, message, error} fails (HR-004)")
    res = detect_validator_shape({"valid": True, "message": "ok", "error": None})
    assert not res.passed
    assert res.failure_category == FailureCategory.VALIDATOR_SHAPED_RESPONSE.value
    print("    OK")


def test_validator_shape_isvalid_variant_fails():
    print("  [validator_shape] {isValid, message, error} variant fails")
    res = detect_validator_shape({"isValid": True, "message": "x", "error": None})
    assert not res.passed
    print("    OK")


def test_validator_shape_constraints_passes():
    print("  [validator_shape] real constraints object passes")
    res = detect_validator_shape({
        "constraints": [{"constraint_type": "방수"}],
        "category": "기계",
    })
    assert res.passed
    print("    OK")


def test_validator_shape_non_dict_passes():
    print("  [validator_shape] list / scalar payload passes")
    assert detect_validator_shape([1, 2, 3]).passed
    assert detect_validator_shape("foo").passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_css_property_leak
# ---------------------------------------------------------------------------

def test_css_leak_underscore_fails():
    print("  [css_leak] font_family / padding fails")
    res = detect_css_property_leak({
        "style": "x",
        "check": {"font_family": True, "padding": True, "color_scheme": True},
    })
    assert not res.passed
    tokens = {ev["token"] for ev in res.evidence}
    assert "font_family" in tokens
    assert "padding" in tokens
    print("    OK")


def test_css_leak_dash_form_fails():
    print("  [css_leak] hyphenated form (background-color) also fails")
    res = detect_css_property_leak({"css": {"background-color": "#fff", "border-radius": 4}})
    assert not res.passed
    print("    OK")


def test_css_leak_clean_minecraft_passes():
    print("  [css_leak] clean minecraft style payload passes")
    res = detect_css_property_leak({
        "verdict": "pass",
        "style_score": 0.8,
        "blocks": [{"material": "oak_planks"}],
    })
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_url_hallucination
# ---------------------------------------------------------------------------

def test_url_example_com_fails():
    print("  [url_hallucination] https://example.com fails (placeholder critical)")
    res = detect_url_hallucination({"data": {"image_url": "https://example.com/image.jpg"}})
    assert not res.passed
    assert res.severity == "critical"
    print("    OK")


def test_url_bare_domain_fails():
    print("  [url_hallucination] bare domain (foo.com) fails")
    res = detect_url_hallucination({"ref": "see foo.com for details"})
    assert not res.passed
    print("    OK")


def test_url_korean_text_passes():
    print("  [url_hallucination] pure Korean text passes")
    res = detect_url_hallucination({"intent": "공포 장면 어둠 연출", "framing": "wide"})
    assert res.passed
    print("    OK")


def test_url_allow_urls_skip():
    print("  [url_hallucination] allow_urls=True bypasses detector")
    res = detect_url_hallucination({"url": "https://example.com"}, allow_urls=True)
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_semantic_anchor_loss
# ---------------------------------------------------------------------------

def test_anchor_loss_rainy_night_fails():
    print("  [anchor_loss] '비 오는 밤' anchors lost → fail")
    res = detect_semantic_anchor_loss(
        "비 오는 밤 외로운 분위기",
        {"intent": "외로운 분위기"},
    )
    assert not res.passed
    print("    OK")


def test_anchor_loss_anchor_preserved_passes():
    print("  [anchor_loss] anchor preserved → pass")
    res = detect_semantic_anchor_loss(
        "비 오는 밤 외로운 분위기",
        {"intent": "비 내리는 외로운 밤 거리"},
    )
    assert res.passed
    print("    OK")


def test_anchor_loss_pcb_preserved_passes():
    print("  [anchor_loss] PCB anchor preserved")
    res = detect_semantic_anchor_loss(
        "PCB 고정 케이스",
        {"constraints": [{"component_name": "PCB"}]},
    )
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_known_lossy_english
# ---------------------------------------------------------------------------

def test_known_lossy_outside_night_fails():
    print("  [known_lossy] '비 오는 밤' → 'outside night' fails (HR-012)")
    res = detect_known_lossy_english(
        "비 오는 밤 외로운 분위기",
        {"reasoning": "outside night vibe"},
    )
    assert not res.passed
    print("    OK")


def test_known_lossy_unrelated_passes():
    print("  [known_lossy] unrelated input passes")
    res = detect_known_lossy_english("거실 평면", {"intent": "거실 확장"})
    assert res.passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_empty_or_trivial_payload
# ---------------------------------------------------------------------------

def test_empty_dict_fails():
    print("  [empty] empty dict fails")
    assert not detect_empty_or_trivial_payload({}).passed
    print("    OK")


def test_none_fails():
    print("  [empty] None fails")
    assert not detect_empty_or_trivial_payload(None).passed
    print("    OK")


def test_wrapper_text_fails():
    print("  [empty] {'text': '...'} wrapper fails (HR-009/HR-010)")
    assert not detect_empty_or_trivial_payload({"text": "정면 창문 넓게"}).passed
    print("    OK")


def test_real_payload_passes():
    print("  [empty] real structured payload passes")
    assert detect_empty_or_trivial_payload({"floors": 2, "spaces": []}).passed
    print("    OK")


# ---------------------------------------------------------------------------
# detect_input_echo
# ---------------------------------------------------------------------------

def test_input_echo_fails():
    print("  [input_echo] exact echo fails")
    res = detect_input_echo("정면 창문 넓게, 지붕 유지", {"result": "정면 창문 넓게, 지붕 유지"})
    assert not res.passed
    print("    OK")


def test_input_echo_no_match_passes():
    print("  [input_echo] different content passes")
    res = detect_input_echo("정면 창문 넓게", {"intent": "창문 확장"})
    assert res.passed
    print("    OK")


def test_input_echo_too_short_skipped():
    print("  [input_echo] very short input skipped")
    res = detect_input_echo("짧음", {"text": "짧음"})
    assert res.passed  # below min_len threshold
    print("    OK")


TESTS = [
    test_chinese_keys_top_level,
    test_chinese_keys_nested,
    test_chinese_keys_clean_passes,
    test_chinese_keys_korean_value_ok,
    test_japanese_keys_kana_fails,
    test_japanese_keys_hangul_passes,
    test_korean_required_pure_english_fails,
    test_korean_required_korean_passes,
    test_korean_required_english_with_quoted_korean_fails,
    test_korean_required_short_value_passes,
    test_korean_required_field_absent_passes,
    test_validator_shape_basic_fails,
    test_validator_shape_isvalid_variant_fails,
    test_validator_shape_constraints_passes,
    test_validator_shape_non_dict_passes,
    test_css_leak_underscore_fails,
    test_css_leak_dash_form_fails,
    test_css_leak_clean_minecraft_passes,
    test_url_example_com_fails,
    test_url_bare_domain_fails,
    test_url_korean_text_passes,
    test_url_allow_urls_skip,
    test_anchor_loss_rainy_night_fails,
    test_anchor_loss_anchor_preserved_passes,
    test_anchor_loss_pcb_preserved_passes,
    test_known_lossy_outside_night_fails,
    test_known_lossy_unrelated_passes,
    test_empty_dict_fails,
    test_none_fails,
    test_wrapper_text_fails,
    test_real_payload_passes,
    test_input_echo_fails,
    test_input_echo_no_match_passes,
    test_input_echo_too_short_skipped,
]


if __name__ == "__main__":
    print("=" * 60)
    print("semantic validator unit tests")
    print("=" * 60)
    passed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\nResults: {passed}/{len(TESTS)} passed")
