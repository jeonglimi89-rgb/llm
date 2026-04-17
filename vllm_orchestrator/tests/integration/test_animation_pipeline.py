"""
test_animation_pipeline.py — Animation 풀 파이프라인:
solve_shot → render_preview → check_continuity + review judgment
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.tools.adapters.animation_shot_solver import solve_shot
from src.app.tools.adapters.animation_preview import render_preview
from src.app.tools.adapters.animation_continuity import check_continuity
from src.app.tools.registry import create_default_registry
from src.app.review.reviewer import review_animation_shot


def test_basic_shot_solve():
    print("  [1] Basic shot solve")
    shot = solve_shot({"framing": "close_up", "mood": "warm", "speed": "slow", "emotion_hint": "슬픔"})
    assert shot["camera"]["framing"] == "close_up"
    assert shot["camera"]["lens_mm"] == 85
    assert shot["lighting"]["color_temperature_k"] == 3200
    assert shot["timing"]["ease_in"]
    print(f"    framing={shot['camera']['framing']}, lens={shot['camera']['lens_mm']}mm, duration={shot['duration_frames']}f")


def test_emotion_affects_duration():
    print("  [2] Emotion adjusts duration")
    sad = solve_shot({"framing": "medium", "speed": "moderate", "emotion_hint": "슬픔"})
    tense = solve_shot({"framing": "medium", "speed": "moderate", "emotion_hint": "긴장"})
    assert sad["duration_frames"] > tense["duration_frames"], "Sad should be longer than tense"
    print(f"    sad={sad['duration_frames']}f vs tense={tense['duration_frames']}f")


def test_render_preview():
    print("  [3] Render preview generation")
    shot = solve_shot({"framing": "wide", "mood": "cold", "speed": "slow"})
    preview = render_preview(shot)
    assert preview["keyframe_count"] >= 3
    assert preview["duration_frames"] >= shot["duration_frames"]
    print(f"    keyframes={preview['keyframe_count']}, duration={preview['duration_frames']}f")


def test_continuity_single_shot():
    print("  [4] Continuity check single shot")
    shot = solve_shot({"framing": "medium"})
    result = check_continuity([shot])
    assert result["verdict"] in ("pass", "warn", "fail")
    print(f"    verdict={result['verdict']}, issues={len(result['issues'])}")


def test_continuity_lens_jump():
    print("  [5] Continuity detects lens jump")
    s1 = solve_shot({"framing": "wide"})  # 24mm
    s2 = solve_shot({"framing": "extreme_close_up"})  # 135mm
    result = check_continuity([s1, s2])
    lens_issues = [i for i in result["issues"] if "lens" in i.get("rule", "")]
    assert len(lens_issues) > 0, "Should detect 24→135mm jump"
    print(f"    detected lens_jump: {lens_issues[0]['detail']}")


def test_continuity_color_jump():
    print("  [6] Continuity detects color temperature jump")
    s1 = solve_shot({"framing": "medium", "mood": "warm"})   # 3200K
    s2 = solve_shot({"framing": "medium", "mood": "cold"})   # 6500K
    result = check_continuity([s1, s2])
    color_issues = [i for i in result["issues"] if "color" in i.get("rule", "")]
    assert len(color_issues) > 0, "Should detect color jump"
    print(f"    detected color_jump: {color_issues[0]['detail']}")


def test_review_judgment_animation():
    print("  [7] Animation review judgment")
    shot = solve_shot({"framing": "close_up", "mood": "dramatic"})
    cont = check_continuity([shot])
    judgment = review_animation_shot(shot, cont, artifact_id="test_anim_001")
    assert judgment.domain == "animation"
    assert judgment.verdict in ("pass", "fail", "needs_review")
    print(f"    verdict={judgment.verdict}, items={len(judgment.items)}")


def test_registry_animation_tools():
    print("  [8] Registry: 3 animation tools real")
    reg = create_default_registry()
    anim_real = [t for t in reg.list_real_tools() if t.startswith("animation.")]
    assert len(anim_real) == 3, f"Expected 3, got {len(anim_real)}"
    print(f"    {anim_real}")


def test_registry_animation_pipeline():
    print("  [9] Animation pipeline via registry")
    reg = create_default_registry()

    shot = reg.call("animation.solve_shot", {
        "framing": "medium", "mood": "neutral", "speed": "moderate",
    })
    assert shot["status"] == "executed"

    preview = reg.call("animation.render_preview", shot)
    assert preview["status"] == "executed"

    continuity = reg.call("animation.check_continuity", [shot["result"]])
    assert continuity["status"] == "executed"

    print(f"    solve→preview→continuity: verdict={continuity['verdict']}")


TESTS = [
    test_basic_shot_solve,
    test_emotion_affects_duration,
    test_render_preview,
    test_continuity_single_shot,
    test_continuity_lens_jump,
    test_continuity_color_jump,
    test_review_judgment_animation,
    test_registry_animation_tools,
    test_registry_animation_pipeline,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Animation Pipeline Tests (3 engines + review)")
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
        print("ALL ANIMATION PIPELINE TESTS PASSED!")
