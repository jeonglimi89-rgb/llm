"""
test_task_contracts.py — review/task_contracts.py 단위 테스트

5개 D등급 태스크 contract 가 알려진 false positive 를 잡고, 정상 출력은
계속 통과시키는지 검증.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.review.task_contracts import (
    evaluate_task_contract, get_task_contract, TASK_CONTRACTS,
)
from src.app.review.layered import FailureCategory


# ---------------------------------------------------------------------------
# Contract registration
# ---------------------------------------------------------------------------

def test_all_target_tasks_registered():
    print("  [1] all 5 D-grade target tasks registered")
    targets = [
        "builder.patch_intent_parse",
        "builder.requirement_parse",
        "cad.constraint_parse",
        "minecraft.style_check",
        "animation.camera_intent_parse",
        "animation.lighting_intent_parse",
    ]
    for t in targets:
        assert t in TASK_CONTRACTS, f"missing contract: {t}"
    print(f"    OK: {len(targets)} contracts registered")


def test_get_contract_falls_back_to_default():
    print("  [2] unknown task gets default domain contract")
    c = get_task_contract("builder.unknown_task")
    assert c.domain == "builder"
    print("    OK")


# ---------------------------------------------------------------------------
# builder.patch_intent_parse
# ---------------------------------------------------------------------------

def test_builder_patch_chinese_keys_blocked():
    print("  [3] builder.patch_intent_parse blocks Chinese keys")
    j = evaluate_task_contract(
        "builder.patch_intent_parse",
        "거실만 1.5배 크게",
        {"操作": "resize", "目标": "거실"},
    )
    assert not j.auto_validated
    assert "wrong_key_locale" in j.failure_categories
    print("    OK")


def test_builder_patch_korean_intent_passes():
    print("  [4] builder.patch_intent_parse pure Korean intent passes")
    j = evaluate_task_contract(
        "builder.patch_intent_parse",
        "창문 유지하고 출입문만 키워줘",
        {"intent": "출입문 확장 + 창문 유지"},
    )
    assert j.auto_validated, f"failed gates: {j.failure_categories}"
    print("    OK")


# ---------------------------------------------------------------------------
# cad.constraint_parse
# ---------------------------------------------------------------------------

def test_cad_constraint_validator_shape_blocked():
    print("  [5] cad.constraint_parse blocks {valid,message,error}")
    j = evaluate_task_contract(
        "cad.constraint_parse",
        "방수 샤워필터, 배수 연결 포함",
        {"valid": True, "message": "ok", "error": None},
    )
    assert not j.auto_validated
    assert "validator_shaped_response" in j.failure_categories
    print("    OK")


def test_cad_constraint_real_constraint_passes():
    print("  [6] cad.constraint_parse real constraint object passes")
    j = evaluate_task_contract(
        "cad.constraint_parse",
        "모터+PCB 내장 접이식 기구부",
        {
            "constraint_type": "접이식기구",
            "description": "모터와 PCB 내장",
            "category": "기계",
            "input_requirements": [
                {"component_name": "모터", "quantity": 1, "unit": "개"},
                {"component_name": "PCB", "quantity": 1, "unit": "개"},
            ],
            "output_requirements": [{"component_name": "접이식기구", "quantity": 1, "unit": "개"}],
        },
    )
    assert j.auto_validated, f"failed gates: {j.failure_categories} | {j.rationale}"
    print("    OK")


# ---------------------------------------------------------------------------
# minecraft.style_check
# ---------------------------------------------------------------------------

def test_minecraft_style_css_blocked():
    print("  [7] minecraft.style_check blocks CSS vocab")
    j = evaluate_task_contract(
        "minecraft.style_check",
        "중세풍 스타일 체크",
        {"style": "중세풍", "check": {"font_family": True, "padding": True}},
    )
    assert not j.auto_validated
    assert "css_property_leak" in j.failure_categories
    print("    OK")


def test_minecraft_style_clean_passes():
    print("  [8] minecraft.style_check clean payload passes")
    j = evaluate_task_contract(
        "minecraft.style_check",
        "현대풍 카페 룩",
        {"verdict": "pass", "style_score": 0.8, "issues": []},
    )
    assert j.auto_validated, f"failed: {j.failure_categories}"
    print("    OK")


# ---------------------------------------------------------------------------
# animation.camera_intent_parse
# ---------------------------------------------------------------------------

def test_camera_intent_url_blocked():
    print("  [9] animation.camera_intent_parse blocks URL hallucination")
    j = evaluate_task_contract(
        "animation.camera_intent_parse",
        "공포 장면 어둠 연출",
        {
            "intent": "camera",
            "framing": "wide",
            "data": {"image_url": "https://example.com/img.jpg"},
        },
    )
    assert not j.auto_validated
    assert "hallucinated_external_reference" in j.failure_categories
    print("    OK")


def test_camera_intent_clean_passes():
    print("  [10] animation.camera_intent_parse clean payload passes")
    j = evaluate_task_contract(
        "animation.camera_intent_parse",
        "긴장감 있는 클로즈업",
        {"framing": "close_up", "movement": "tracking", "mood": "dark"},
    )
    assert j.auto_validated, f"failed: {j.failure_categories}"
    print("    OK")


# ---------------------------------------------------------------------------
# animation.lighting_intent_parse
# ---------------------------------------------------------------------------

def test_lighting_intent_english_reasoning_blocked():
    print("  [11] animation.lighting_intent_parse blocks English-dominant reasoning (HR-012)")
    j = evaluate_task_contract(
        "animation.lighting_intent_parse",
        "비 오는 밤 외로운 분위기",
        {
            "intent": "외로운 분위기",
            "reasoning": "The user is expressing a desire for an environment that is not too crowded or noisy, which aligns with the concept of '비 오는 밤' (outside night) in Korean language.",
        },
    )
    assert not j.auto_validated
    cats = j.failure_categories
    # 적어도 wrong_language 또는 semantic_mistranslation 둘 중 하나는 떠야 함
    assert ("wrong_language" in cats) or ("semantic_mistranslation" in cats), f"got: {cats}"
    print("    OK")


def test_lighting_intent_korean_passes():
    print("  [12] animation.lighting_intent_parse Korean reasoning passes")
    j = evaluate_task_contract(
        "animation.lighting_intent_parse",
        "비 오는 밤 외로운 분위기",
        {
            # intent 가 입력과 똑같으면 echo detector 가 잡으므로 슬롯 추출 형태로 변환
            "intent": "고독한 야경",
            "reasoning": "비 내리는 밤거리에서 외로움을 강조하는 어두운 푸른 톤",
            "atmosphere": "비 내리는 밤",
            "mood_tag": "외로움",
        },
    )
    assert j.auto_validated, f"failed: {j.failure_categories} | {j.rationale}"
    print("    OK")


# ---------------------------------------------------------------------------
# Schema-fail propagation
# ---------------------------------------------------------------------------

def test_schema_fail_propagates():
    print("  [13] schema_validated=False forces fail")
    j = evaluate_task_contract(
        "builder.requirement_parse",
        "2층 주택",
        None,
        schema_validated=False,
    )
    assert j.final_judgment == "fail"
    assert not j.auto_validated
    print("    OK")


# ---------------------------------------------------------------------------
# Recommended action surfaces meaningful guidance
# ---------------------------------------------------------------------------

def test_recommended_action_for_each_failure_class():
    print("  [14] recommended_action contains per-class guidance")
    cases = [
        ("builder.requirement_parse", "x", {"楼层": "2"}, "한자"),
        ("cad.constraint_parse", "x", {"valid": True, "message": "x", "error": None}, "validator"),
        ("minecraft.style_check", "x", {"style": "x", "css": {"padding": 1}}, "Minecraft"),
        ("animation.camera_intent_parse", "x", {"u": "https://example.com"}, "URL"),
    ]
    for task_type, inp, payload, must_contain in cases:
        j = evaluate_task_contract(task_type, inp, payload)
        assert j.recommended_action, f"{task_type}: empty action"
        assert must_contain in j.recommended_action, (
            f"{task_type}: '{must_contain}' not in action='{j.recommended_action}'"
        )
    print("    OK")


TESTS = [
    test_all_target_tasks_registered,
    test_get_contract_falls_back_to_default,
    test_builder_patch_chinese_keys_blocked,
    test_builder_patch_korean_intent_passes,
    test_cad_constraint_validator_shape_blocked,
    test_cad_constraint_real_constraint_passes,
    test_minecraft_style_css_blocked,
    test_minecraft_style_clean_passes,
    test_camera_intent_url_blocked,
    test_camera_intent_clean_passes,
    test_lighting_intent_english_reasoning_blocked,
    test_lighting_intent_korean_passes,
    test_schema_fail_propagates,
    test_recommended_action_for_each_failure_class,
]


if __name__ == "__main__":
    print("=" * 60)
    print("task contracts unit tests")
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
