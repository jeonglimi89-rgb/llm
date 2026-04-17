"""
test_domain_runtime_e2e.py — Domain runtime benchmark suite.

12 deterministic E2E cases (3 per domain) with FakeLLM.
Verifies: correct domain routing, chain selection, schema pass,
evaluator scoring, fail-loud on bad output, to_dict structure.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.orchestration.orchestrated_pipeline import OrchestratedPipeline, OrchestrationResult
from src.app.orchestration.domain_classifier import DomainClassifier
from src.app.orchestration.domain_router import DomainRouter
from src.app.orchestration.requirement_extractor import RequirementExtractor
from src.app.review.domain_evaluator import DomainEvaluator
from src.app.domain.schema_enforcer import SchemaEnforcer
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.orchestration.task_chain import TaskChainEngine, load_chain_definitions
from src.app.domain.profiles import load_domain_profiles, init_profiles
from src.app.domain.product_templates import init_templates, init_domain_templates
from src.app.tools.registry import create_default_registry
from src.app.execution.timeouts import TimeoutPolicy
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.llm.client import LLMClient
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker

CONFIGS = Path(__file__).resolve().parent.parent.parent / "configs"
PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"


class _MultiResponseLLM:
    """FakeLLM that returns a sequence of responses."""
    provider_name = "multi-fake"
    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0
    def is_available(self): return True
    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        text = self._responses[self._idx] if self._idx < len(self._responses) else '{"fallback": true}'
        self._idx += 1
        return {"text": text, "prompt_tokens": 0, "completion_tokens": 0}


def _build_pipeline(responses: list[str]) -> OrchestratedPipeline:
    profiles = init_profiles(CONFIGS)
    init_domain_templates(CONFIGS)
    chains = load_chain_definitions(CONFIGS)
    tools = create_default_registry()
    adapter = _MultiResponseLLM(responses)
    llm = LLMClient(adapter, HealthRegistry(), CircuitBreaker(), max_retries=0)
    router = Router()
    dispatcher = Dispatcher(llm_client=llm, queue=QueueManager(1, 10),
                            scheduler=Scheduler(), timeouts=TimeoutPolicy(),
                            prompts_dir=PROMPTS)
    return OrchestratedPipeline(
        classifier=DomainClassifier(profiles), profiles=profiles,
        extractor=RequirementExtractor(profiles), evaluator=DomainEvaluator(profiles),
        schema_enforcer=SchemaEnforcer(), router=router, dispatcher=dispatcher,
        domain_router=DomainRouter(profiles),
        chain_engine=TaskChainEngine(dispatcher, router, tools),
        chain_definitions=chains,
        product_templates=init_templates(CONFIGS),
    )


def _check_common(r: OrchestrationResult, expected_domain: str, label: str):
    """공통 검증: routing, profile, chain, to_dict structure."""
    d = r.to_dict()
    assert d["detected_domain"] == expected_domain, f"[{label}] domain={d['detected_domain']}"
    assert d["selected_profile"] == expected_domain, f"[{label}] profile={d['selected_profile']}"
    assert d["selected_chain"], f"[{label}] no chain selected"
    assert d["evaluation"] is not None, f"[{label}] no evaluation"
    assert "scores" in d["evaluation"], f"[{label}] no scores"
    assert "pass" in d["evaluation"], f"[{label}] no pass field"
    return d


# ═══════════════════════════════════════════════════════════════════
# CAD (3 cases)
# ═══════════════════════════════════════════════════════════════════

def test_cad_straightforward():
    p = _build_pipeline([
        '{"systems": [{"name": "mechanical"}, {"name": "electrical"}]}',
        '{"constraints": [{"constraint_type": "방수", "description": "IP67", "category": "기계"}]}',
    ])
    r = p.execute("방수 센서 모듈 설계, 배선 포함")
    d = _check_common(r, "cad", "cad_straight")
    assert not d["fail_loud"], f"unexpected fail_loud: {d['fail_loud_reason']}"

def test_cad_ambiguous_resolvable():
    p = _build_pipeline([
        '{"systems": [{"name": "mechanical"}]}',
        '{"constraints": [{"constraint_type": "구조", "description": "부품 설계", "category": "기계"}]}',
    ])
    r = p.execute("이 제품 부품 설계해줘, 치수 120x80mm")
    d = _check_common(r, "cad", "cad_ambig")
    assert d["detected_domain"] == "cad"

def test_cad_constraint_heavy():
    p = _build_pipeline([
        '{"systems": [{"name": "mechanical"}, {"name": "electrical"}, {"name": "plumbing"}]}',
        '{"constraints": [{"constraint_type": "방수", "description": "IP67", "category": "기계"}, {"constraint_type": "충전", "description": "USB-C", "category": "전기"}]}',
    ])
    r = p.execute("충전식 방수 샤워필터, USB-C, IP67, 필터 교체 가능, 배수 연결")
    d = _check_common(r, "cad", "cad_heavy")


# ═══════════════════════════════════════════════════════════════════
# Builder (3 cases)
# ═══════════════════════════════════════════════════════════════════

def test_builder_straightforward():
    p = _build_pipeline([
        '{"floors": 2, "spaces": [{"type": "living_room", "count": 1}, {"type": "bedroom", "count": 2}], "preferences": {"style_family": "modern"}}',
    ])
    r = p.execute("2층 주택 거실 크게, 모던 스타일")
    d = _check_common(r, "builder", "builder_straight")
    assert not d["fail_loud"]

def test_builder_ambiguous():
    p = _build_pipeline([
        '{"floors": 1, "spaces": [{"type": "cafe", "count": 1}]}',
    ])
    r = p.execute("카페 공간 설계해줘")
    d = _check_common(r, "builder", "builder_ambig")

def test_builder_constraint_heavy():
    p = _build_pipeline([
        '{"floors": 3, "spaces": [{"type": "store", "count": 1}, {"type": "office", "count": 2}, {"type": "bathroom", "count": 3}]}',
    ])
    r = p.execute("건폐율 60%, 용적률 200%, 3층 상가건물, 1층 매장, 2-3층 사무실")
    d = _check_common(r, "builder", "builder_heavy")


# ═══════════════════════════════════════════════════════════════════
# Minecraft (3 cases)
# ═══════════════════════════════════════════════════════════════════

def test_minecraft_straightforward():
    p = _build_pipeline([
        '{"target_anchor": {"anchor_type": "facade"}, "operations": [{"type": "add", "delta": {"material": "stone", "count": 100}}], "preserve": []}',
        '{"verdict": "pass", "style_score": 0.9, "issues": []}',
    ])
    r = p.execute("마인크래프트 중세 성벽 돌 블록으로 쌓기")
    d = _check_common(r, "minecraft", "mc_straight")
    assert not d["fail_loud"]

def test_minecraft_ambiguous():
    p = _build_pipeline([
        '{"target_anchor": {"anchor_type": "tower"}, "operations": [{"type": "add", "delta": {"material": "spruce", "count": 50}}], "preserve": ["door"]}',
        '{"verdict": "warn", "style_score": 0.6, "issues": []}',
    ])
    r = p.execute("탑 빌드 스프루스로, 문 유지")
    d = _check_common(r, "minecraft", "mc_ambig")

def test_minecraft_constraint_heavy():
    p = _build_pipeline([
        '{"target_anchor": {"anchor_type": "facade"}, "operations": [{"type": "replace_material", "delta": {"material": "brick", "count": 200}}, {"type": "add", "delta": {"material": "glass", "count": 30}}], "preserve": ["door", "roof"]}',
        '{"verdict": "pass", "style_score": 0.85, "issues": []}',
    ])
    r = p.execute("마인크래프트 정면 벽돌로 교체, 유리창 넓게, 문과 지붕은 유지")
    d = _check_common(r, "minecraft", "mc_heavy")


# ═══════════════════════════════════════════════════════════════════
# Animation (3 cases)
# ═══════════════════════════════════════════════════════════════════

def test_animation_straightforward():
    p = _build_pipeline([
        '{"framing": "close_up", "mood": "warm", "speed": "slow", "emotion_hint": "nostalgia"}',
        '{"movement": "dolly", "angle": "eye_level", "subject": "인물 얼굴", "focus": "shallow"}',
        '{"atmosphere": "노을빛 따뜻한 분위기", "mood_tag": "따뜻함", "intensity": "medium", "color_temperature": "warm"}',
    ])
    r = p.execute("노을빛에 따뜻한 클로즈업 연출")
    d = _check_common(r, "animation", "anim_straight")
    assert not d["fail_loud"]

def test_animation_ambiguous():
    p = _build_pipeline([
        '{"framing": "medium", "mood": "neutral", "speed": "moderate"}',
        '{"movement": "static", "angle": "eye_level", "subject": "대화", "focus": "sharp"}',
        '{"atmosphere": "일상적 실내", "mood_tag": "평범", "intensity": "low", "color_temperature": "neutral"}',
    ])
    r = p.execute("대화 장면 연출해줘")
    d = _check_common(r, "animation", "anim_ambig")

def test_animation_constraint_heavy():
    p = _build_pipeline([
        '{"framing": "wide", "mood": "dramatic", "speed": "fast", "emotion_hint": "tension"}',
        '{"movement": "handheld", "angle": "low", "subject": "추격전", "focus": "sharp"}',
        '{"atmosphere": "어두운 밤거리", "mood_tag": "긴장감", "intensity": "high", "color_temperature": "cold"}',
    ])
    r = p.execute("추격 씬, 와이드 핸드헬드, 어두운 밤거리, 긴장감 있는 조명, 연속성 유지")
    d = _check_common(r, "animation", "anim_heavy")


# ═══════════════════════════════════════════════════════════════════
# Cross-cutting: to_dict structure + fail-loud
# ═══════════════════════════════════════════════════════════════════

def test_to_dict_has_required_structure():
    """to_dict 가 요구된 반환 구조를 가지는지 검증."""
    p = _build_pipeline([
        '{"systems": [{"name": "mechanical"}]}',
        '{"constraints": [{"constraint_type": "test"}]}',
    ])
    r = p.execute("테스트 제품 설계")
    d = r.to_dict()
    required_keys = {"detected_domain", "domain_candidates", "selected_chain",
                     "selected_profile", "output", "evaluation", "fail_loud"}
    missing = required_keys - set(d.keys())
    assert not missing, f"missing keys in to_dict: {missing}"

def test_fail_loud_on_null_output():
    """LLM 이 아무것도 반환하지 않으면 fail-loud."""
    p = _build_pipeline(["not valid json at all"])
    r = p.execute("방수 설계해줘")
    d = r.to_dict()
    # schema should fail (output is None or unparseable)
    assert d.get("fail_loud") or d.get("evaluation", {}).get("pass") is False


TESTS = [
    test_cad_straightforward, test_cad_ambiguous_resolvable, test_cad_constraint_heavy,
    test_builder_straightforward, test_builder_ambiguous, test_builder_constraint_heavy,
    test_minecraft_straightforward, test_minecraft_ambiguous, test_minecraft_constraint_heavy,
    test_animation_straightforward, test_animation_ambiguous, test_animation_constraint_heavy,
    test_to_dict_has_required_structure, test_fail_loud_on_null_output,
]

if __name__ == "__main__":
    passed = 0
    for fn in TESTS:
        try: fn(); passed += 1; print(f"  OK {fn.__name__}")
        except Exception as e: print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
