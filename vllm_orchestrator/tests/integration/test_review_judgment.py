"""
test_review_judgment.py — 인간 검수 판정 레이어 통합 테스트
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.review.judgment import (
    ReviewJudgment, JudgmentItem, Verdict, Severity, validate_judgment_schema
)
from src.app.review.reviewer import (
    review_builder_plan, review_minecraft_build,
    review_cad_design, review_animation_shot
)
from src.app.tools.adapters.builder_planner import generate_plan
from src.app.tools.adapters.builder_validator import validate_plan
from src.app.tools.adapters.minecraft_compiler import compile_edit
from src.app.tools.adapters.minecraft_palette_validator import validate_palette
from src.app.tools.adapters.cad_part_generator import generate_part
from src.app.tools.adapters.cad_geometry_validator import validate_geometry
from src.app.tools.adapters.animation_shot_solver import solve_shot
from src.app.tools.adapters.animation_continuity import check_continuity


def test_judgment_schema_basic():
    print("  [1] Judgment schema basic")
    j = ReviewJudgment(
        artifact_id="test_001",
        domain="builder",
        task_type="builder.generate_plan",
        verdict=Verdict.PASS.value,
        items=[JudgmentItem(category="test", severity=Severity.LOW.value, rationale="test")],
    )
    d = j.to_dict()
    assert d["artifact_id"] == "test_001"
    assert d["verdict"] == "pass"
    assert "stats" in d
    ok, errors = validate_judgment_schema(d)
    assert ok, f"Schema invalid: {errors}"
    print(f"    OK: schema valid, items={len(j.items)}")


def test_judgment_serialization():
    print("  [2] Judgment JSON round-trip")
    j = ReviewJudgment(
        artifact_id="ser_001",
        domain="cad",
        task_type="cad.generate_part",
        verdict=Verdict.NEEDS_REVIEW.value,
        items=[
            JudgmentItem(category="dimension", severity=Severity.HIGH.value, rationale="too small"),
            JudgmentItem(category="safety", severity=Severity.MEDIUM.value, rationale="missing seal"),
        ],
        summary="2 issues",
        human_required=True,
    )
    serialized = j.to_json()
    parsed = json.loads(serialized)
    restored = ReviewJudgment.from_dict(parsed)
    assert restored.artifact_id == j.artifact_id
    assert len(restored.items) == 2
    print(f"    OK: round-trip preserved")


def test_judgment_invalid_schema():
    print("  [3] Schema validation rejects bad input")
    bad = {"domain": "builder"}  # missing required fields
    ok, errors = validate_judgment_schema(bad)
    assert not ok
    assert len(errors) > 0
    print(f"    OK: caught {len(errors)} errors")

    bad2 = {
        "artifact_id": "x", "domain": "x", "task_type": "x", "verdict": "INVALID",
        "items": [{"severity": "WRONG", "category": "x", "rationale": "x"}],
    }
    ok2, errors2 = validate_judgment_schema(bad2)
    assert not ok2
    print(f"    OK: caught invalid verdict + severity")


def test_review_builder_pass():
    print("  [4] Builder review: pass case")
    plan = generate_plan({
        "floors": 2,
        "spaces": [
            {"type": "living_room", "count": 1, "priority": "high"},
            {"type": "bedroom", "count": 2},
            {"type": "bathroom", "count": 1},
        ],
    })
    val = validate_plan(plan)
    j = review_builder_plan(plan, val, artifact_id="b_001")
    assert j.domain == "builder"
    assert j.verdict in ("pass", "needs_review", "fail")
    print(f"    verdict={j.verdict}, items={len(j.items)}, auto_pass={j.auto_pass}")


def test_review_minecraft():
    print("  [5] Minecraft review")
    blocks = compile_edit({
        "target_anchor": {"anchor_type": "facade"},
        "operations": [{"type": "add", "delta": {"material": "stone", "count": 10}}],
        "preserve": [],
    })
    palette = validate_palette(blocks, theme="medieval")
    j = review_minecraft_build(blocks, palette, artifact_id="m_001")
    assert j.domain == "minecraft"
    print(f"    verdict={j.verdict}, items={len(j.items)}")


def test_review_cad_with_critical():
    print("  [6] CAD review: critical case (empty parts)")
    empty = {"parts": [], "interfaces": {}, "metadata": {"part_count": 0}}
    val = validate_geometry(empty)
    j = review_cad_design(empty, val)
    assert j.verdict == "fail"
    assert any(i.severity == "critical" for i in j.items)
    print(f"    verdict={j.verdict}, critical={sum(1 for i in j.items if i.severity == 'critical')}")


def test_review_animation():
    print("  [7] Animation review")
    shot = solve_shot({"framing": "close_up", "mood": "warm", "emotion_hint": "슬픔"})
    cont = check_continuity([shot])
    j = review_animation_shot(shot, cont, artifact_id="a_001")
    assert j.domain == "animation"
    print(f"    verdict={j.verdict}, items={len(j.items)}")


def test_severity_levels_exist():
    print("  [8] All severity levels accessible")
    for s in [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]:
        item = JudgmentItem(category="x", severity=s.value, rationale="x")
        assert item.severity == s.value
    print(f"    OK: 5 severity levels")


def test_judgment_evidence_pointers():
    print("  [9] Judgment items support evidence")
    item = JudgmentItem(
        category="geometry",
        severity=Severity.HIGH.value,
        rationale="overlap detected",
        evidence={"part_a": "P001", "part_b": "P002", "overlap_mm": 5.2},
        recommended_action="reposition P002",
        confidence=0.95,
    )
    d = item.to_dict()
    assert d["evidence"]["overlap_mm"] == 5.2
    assert d["confidence"] == 0.95
    print(f"    OK: evidence preserved")


TESTS = [
    test_judgment_schema_basic,
    test_judgment_serialization,
    test_judgment_invalid_schema,
    test_review_builder_pass,
    test_review_minecraft,
    test_review_cad_with_critical,
    test_review_animation,
    test_severity_levels_exist,
    test_judgment_evidence_pointers,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Review Judgment Layer Tests")
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
    if passed == len(TESTS):
        print("ALL REVIEW JUDGMENT TESTS PASSED!")
