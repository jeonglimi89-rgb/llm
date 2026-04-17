"""
test_domain_orchestration.py — Domain-specialized orchestration layer tests.

Default gate, deterministic, no LLM calls. Uses FakeLLM fixtures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.app.domain.profiles import load_domain_profiles, DomainProfile
from src.app.orchestration.domain_classifier import DomainClassifier, ClassificationResult
from src.app.orchestration.requirement_extractor import RequirementExtractor
from src.app.review.domain_evaluator import DomainEvaluator
from src.app.observability.domain_telemetry import DomainTelemetryRecord
from src.app.orchestration.orchestrated_pipeline import OrchestratedPipeline, OrchestrationResult
from src.app.orchestration.router import Router
from src.app.orchestration.dispatcher import Dispatcher
from src.app.execution.timeouts import TimeoutPolicy
from src.app.execution.queue_manager import QueueManager
from src.app.execution.scheduler import Scheduler
from src.app.llm.client import LLMClient
from src.app.observability.health_registry import HealthRegistry
from src.app.execution.circuit_breaker import CircuitBreaker
from src.app.core.contracts import TaskRequest


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


def _load_profiles():
    return load_domain_profiles(CONFIGS_DIR)


class _FakeLLM:
    provider_name = "fake"
    def __init__(self, response='{"intent": "test"}'):
        self._response = response
    def is_available(self): return True
    def generate(self, *, messages, max_tokens, temperature, timeout_s):
        return {"text": self._response, "prompt_tokens": 0, "completion_tokens": 0}


def _build_pipeline(llm_response='{"intent": "test"}'):
    profiles = _load_profiles()
    adapter = _FakeLLM(llm_response)
    llm = LLMClient(adapter, HealthRegistry(), CircuitBreaker(), max_retries=0)
    router = Router()
    dispatcher = Dispatcher(
        llm_client=llm,
        queue=QueueManager(max_concurrency=1, max_depth=10),
        scheduler=Scheduler(),
        timeouts=TimeoutPolicy(),
        prompts_dir=Path(__file__).resolve().parent.parent.parent / "prompts",
    )
    from src.app.orchestration.domain_router import DomainRouter
    from src.app.orchestration.task_chain import TaskChainEngine, load_chain_definitions
    from src.app.domain.schema_enforcer import SchemaEnforcer
    from src.app.tools.registry import create_default_registry

    chains = load_chain_definitions(CONFIGS_DIR)
    tools = create_default_registry()
    return OrchestratedPipeline(
        classifier=DomainClassifier(profiles),
        profiles=profiles,
        extractor=RequirementExtractor(profiles),
        evaluator=DomainEvaluator(profiles),
        schema_enforcer=SchemaEnforcer(),
        router=router,
        dispatcher=dispatcher,
        domain_router=DomainRouter(profiles),
        chain_engine=TaskChainEngine(dispatcher, router, tools),
        chain_definitions=chains,
    )


# ===========================================================================
# Test 1: Classifier → CAD input produces top=cad with confidence
# ===========================================================================

def test_classifier_cad_input_produces_top_cad():
    print("  [1] Classifier: CAD input → top=cad")
    profiles = _load_profiles()
    c = DomainClassifier(profiles)
    result = c.classify("PCB 방수 처리된 제어모듈 설계해줘")
    assert result.top.domain == "cad", f"got {result.top.domain}"
    assert result.top.confidence > 0.3, f"confidence too low: {result.top.confidence}"
    assert len(result.top.matched_signals) >= 1
    assert result.runner_up is not None
    print(f"    OK: top={result.top.domain}({result.top.confidence}), "
          f"runner_up={result.runner_up.domain}({result.runner_up.confidence})")


# ===========================================================================
# Test 2: Classifier → ambiguous input never defaults to "general"
# ===========================================================================

def test_classifier_ambiguous_no_general():
    print("  [2] Classifier: ambiguous input → always a known domain, never 'general'")
    profiles = _load_profiles()
    c = DomainClassifier(profiles)
    result = c.classify("이 디자인 수정해줘")
    _KNOWN_DOMAINS = {"cad", "builder", "minecraft", "animation", "product_design", "resourcepack", "npc"}
    assert result.top.domain in _KNOWN_DOMAINS, (
        f"got domain '{result.top.domain}' — must be a known domain, never 'general'"
    )
    # raw_scores should contain all loaded domains (no 'general')
    assert "general" not in result.raw_scores
    assert len(result.raw_scores) == len(profiles)
    print(f"    OK: top={result.top.domain}, scores={result.raw_scores}")


# ===========================================================================
# Test 3: RequirementExtractor → CAD dimensions extracted
# ===========================================================================

def test_requirement_extractor_cad_dimensions():
    print("  [3] Extractor: CAD dimensions from '120x80mm 알루미늄 케이스'")
    profiles = _load_profiles()
    ext = RequirementExtractor(profiles)
    envelope = ext.extract("120x80mm 알루미늄 케이스, 방수 IP67", "cad", "constraint_parse")
    assert "dimensions" in envelope.domain_specific, f"missing dimensions: {envelope.domain_specific}"
    assert "120x80" in str(envelope.domain_specific["dimensions"])
    assert any("IP67" in c or "방수" in c for c in envelope.hard_constraints), (
        f"missing waterproof constraint: {envelope.hard_constraints}"
    )
    print(f"    OK: dimensions={envelope.domain_specific.get('dimensions')}, "
          f"constraints={envelope.hard_constraints}")


# ===========================================================================
# Test 4: DomainEvaluator → missing constraints detected
# ===========================================================================

def test_domain_evaluator_missing_constraints():
    print("  [4] Evaluator: missing constraints → low coverage")
    profiles = _load_profiles()
    ev = DomainEvaluator(profiles)
    c = DomainClassifier(profiles)
    ext = RequirementExtractor(profiles)

    classification = c.classify("방수 120x80mm 알루미늄 케이스 설계")
    envelope = ext.extract("방수 120x80mm 알루미늄 케이스 설계", "cad", "constraint_parse")

    # Output missing dimensions and material
    slots = {"constraints": [{"constraint_type": "기타", "description": "없음"}]}
    evaluation = ev.evaluate(classification, envelope, profiles["cad"], slots)

    assert evaluation.constraint_coverage < 1.0, f"coverage should be < 1.0: {evaluation.constraint_coverage}"
    assert len(evaluation.missing_constraints) >= 1
    print(f"    OK: coverage={evaluation.constraint_coverage}, missing={evaluation.missing_constraints}")


# ===========================================================================
# Test 5: OrchestratedPipeline E2E with FakeLLM
# ===========================================================================

def test_orchestrated_pipeline_e2e():
    print("  [5] Pipeline E2E: classify → extract → dispatch → evaluate")
    cad_response = json.dumps({
        "constraints": [
            {"constraint_type": "방수", "description": "방수 처리", "category": "기계"}
        ]
    }, ensure_ascii=False)
    pipeline = _build_pipeline(cad_response)
    result = pipeline.execute("방수 샤워필터 설계, 배수 연결 포함")

    assert isinstance(result, OrchestrationResult)
    assert result.classification.top.domain == "cad"
    assert result.task_result.status in ("done", "TaskStatus.DONE")
    assert result.telemetry.detected_domain == "cad"
    assert result.telemetry.total_orchestration_ms >= 0
    print(f"    OK: domain={result.classification.top.domain}, "
          f"score={result.telemetry.overall_score}")


# ===========================================================================
# Test 6: Telemetry record has all required fields
# ===========================================================================

def test_telemetry_record_all_fields():
    print("  [6] Telemetry: all required fields present")
    record = DomainTelemetryRecord(
        detected_domain="cad",
        domain_confidence=0.75,
        classification_reason="top=cad(0.75)",
        selected_profile="cad",
        final_execution_type="enriched",
    )
    d = record.to_dict()
    required = {
        "detected_domain", "domain_confidence", "runner_up_domain",
        "runner_up_confidence", "classification_ambiguous", "classification_reason",
        "extracted_constraints", "extracted_preferences", "execution_risk",
        "selected_profile", "evaluator_scores", "overall_score",
        "repaired", "repair_delta", "final_execution_type", "total_orchestration_ms",
    }
    missing = required - set(d.keys())
    assert not missing, f"missing fields: {missing}"
    print(f"    OK: {len(d)} fields present")


# ===========================================================================
# Test 7: Generic prohibition in enriched prompt
# ===========================================================================

def test_enriched_prompt_contains_generic_prohibition():
    print("  [7] Enriched prompt contains generic prohibition")
    pipeline = _build_pipeline()
    profiles = _load_profiles()
    ext = RequirementExtractor(profiles)
    envelope = ext.extract("방수 샤워필터", "cad", "constraint_parse")
    base_prompt = "Output ONLY valid JSON."
    enriched = pipeline._build_enriched_prompt(profiles["cad"], envelope, base_prompt)
    assert "MUST produce domain-specific" in enriched, (
        f"generic prohibition missing from enriched prompt"
    )
    assert "generic" in enriched.lower(), "generic prohibition keyword missing"
    print(f"    OK: prohibition found in enriched prompt ({len(enriched)} chars)")


# ===========================================================================
# Test 8: Existing 5-gate unchanged (no regression)
# ===========================================================================

def test_existing_5gate_unchanged():
    print("  [8] Existing 5-gate pipeline unaffected by new code")
    from src.app.review.task_contracts import evaluate_task_contract
    # Known-good payload for builder.requirement_parse
    result = evaluate_task_contract(
        task_type="builder.requirement_parse",
        user_input="2층 주택 거실 크게",
        payload={"floors": 2, "spaces": [{"type": "living_room", "count": 1}]},
        schema_validated=True,
    )
    assert result.auto_validated is True, (
        f"5-gate regression: auto_validated={result.auto_validated}, "
        f"judgment={result.final_judgment}, cats={result.failure_categories}"
    )
    print(f"    OK: 5-gate still passes for known-good payload")


TESTS = [
    test_classifier_cad_input_produces_top_cad,
    test_classifier_ambiguous_no_general,
    test_requirement_extractor_cad_dimensions,
    test_domain_evaluator_missing_constraints,
    test_orchestrated_pipeline_e2e,
    test_telemetry_record_all_fields,
    test_enriched_prompt_contains_generic_prohibition,
    test_existing_5gate_unchanged,
]

if __name__ == "__main__":
    print("=" * 60)
    print("Domain Orchestration Tests")
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
