"""
orchestration/orchestrated_pipeline.py — Domain-specialized runtime pipeline.

모든 요청이 반드시 거치는 강제 경로:
  DomainRouter → DomainProfile → RequirementExtractor → Chain/Dispatch
  → [VariantPlanning] → [CreativityVerification] → SchemaEnforcer
  → DomainEvaluator → [OutputPolicy] → Repair(1회) → fail-loud or pass

generic fallback 금지. 4개 도메인(cad/builder/minecraft/animation) 전용.

Creative Layer (additive, optional):
  creative_profile이 context에 없으면 domain default 사용.
  variant_count > 1이면 VariantPlanner로 복수안 생성.
  CreativityVerifier로 각 variant 검증.
  OutputPolicyEnforcer로 최종 출력 타입 분류.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..core.contracts import TaskRequest, TaskResult
from ..domain.profiles import DomainProfile
from ..domain.schema_enforcer import SchemaEnforcer, SchemaValidationResult
from ..domain.creative_profile import CreativeProfile, resolve_creative_profile
from ..domain.creative_boundaries import BoundaryEnforcer
from ..domain.output_policy import OutputPolicyEnforcer, OutputPolicyResult
from ..domain.intervention_policy import InterventionPolicy
from ..domain.allowed_range import AllowedRangeEnforcer, RangeEnforcementResult
from ..domain.command_graph import CommandGraphBuilder, CommandGraph, CommandGraphBundle
from ..observability.domain_telemetry import DomainTelemetryRecord
from ..observability.logger import get_logger, log_event
from .domain_classifier import DomainClassifier, ClassificationResult
from .domain_router import DomainRouter, RoutingResult
from .requirement_extractor import RequirementExtractor, RequirementEnvelope
from .post_classification_validator import PostClassificationValidator, PostClassificationResult
from ..review.domain_evaluator import DomainEvaluator, DomainEvaluation
from ..review.creativity_verifier import CreativityVerifier
from .router import Router
from .dispatcher import Dispatcher
from .task_chain import TaskChainEngine, ChainDefinition, ChainResult
from .variant_planner import VariantPlanner, VariantPlan
from ..domain.product_templates import ProductTemplate, match_template, match_domain_template
from ..training.data_collector import TrainingDataCollector

_log = get_logger("orchestration")
_training_collector: Optional[TrainingDataCollector] = None


def set_training_collector(collector: Optional[TrainingDataCollector]) -> None:
    """Enable training data collection from pipeline runs."""
    global _training_collector
    _training_collector = collector


@dataclass
class OrchestrationResult:
    """모든 요청의 강제 반환 구조."""
    task_result: TaskResult
    classification: ClassificationResult
    routing: Optional[RoutingResult]
    requirement_envelope: RequirementEnvelope
    domain_evaluation: Optional[DomainEvaluation]
    schema_validation: Optional[SchemaValidationResult]
    repaired: bool
    fail_loud: bool
    fail_loud_reason: str
    telemetry: DomainTelemetryRecord
    # Creative layer (additive)
    variant_plan: Optional[dict[str, Any]] = None
    output_policy: Optional[dict[str, Any]] = None
    # Intervention / capability layer (tranche 2)
    intervention_check: Optional[dict[str, Any]] = None
    range_enforcement: Optional[dict[str, Any]] = None
    command_graph: Optional[dict[str, Any]] = None
    post_classification: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        routing = self.routing
        return {
            "detected_domain": routing.primary_domain if routing else self.classification.top.domain,
            "domain_candidates": [
                {"domain": c.domain, "score": round(c.score, 3)}
                for c in (routing.candidates if routing else [])
            ],
            "selected_chain": self.telemetry.selected_chain,
            "selected_profile": self.telemetry.selected_profile,
            "output": self.task_result.slots,
            "evaluation": self.domain_evaluation.to_dict() if self.domain_evaluation else None,
            "schema_validation": self.schema_validation.to_dict() if self.schema_validation else None,
            "fail_loud": self.fail_loud,
            "fail_loud_reason": self.fail_loud_reason,
            "telemetry": self.telemetry.to_dict(),
            "task_result": self.task_result.to_dict() if hasattr(self.task_result, "to_dict") else {},
            "variant_plan": self.variant_plan,
            "output_policy": self.output_policy,
            "intervention_check": self.intervention_check,
            "range_enforcement": self.range_enforcement,
            "command_graph": self.command_graph,
            "post_classification": self.post_classification,
        }


class OrchestratedPipeline:
    """4-domain 전용 강제 런타임.

    모든 입력이 반드시:
    1. DomainRouter 로 분류
    2. DomainProfile 로드
    3. RequirementExtractor 로 제약 추출
    4. Chain 또는 enriched single-step dispatch
    5. SchemaEnforcer 로 hard validation
    6. DomainEvaluator 로 quality scoring
    7. 미달 시 same-domain repair (1회)
    8. repair 후에도 미달이면 fail-loud 구조 반환
    """

    def __init__(
        self,
        classifier: DomainClassifier,
        profiles: dict[str, DomainProfile],
        extractor: RequirementExtractor,
        evaluator: DomainEvaluator,
        schema_enforcer: SchemaEnforcer,
        router: Router,
        dispatcher: Dispatcher,
        *,
        domain_router: DomainRouter,
        chain_engine: TaskChainEngine,
        chain_definitions: dict[str, ChainDefinition],
        product_templates: Optional[dict[str, ProductTemplate]] = None,
        repair_threshold: float = 0.5,
        max_repair_passes: int = 1,
        # Creative layer (additive, backward-compatible)
        variant_planner: Optional[VariantPlanner] = None,
        creativity_verifier: Optional[CreativityVerifier] = None,
        output_policy: Optional[OutputPolicyEnforcer] = None,
        # Intervention / capability layer (tranche 2)
        post_classification_validator: Optional[PostClassificationValidator] = None,
        allowed_range_enforcer: Optional[AllowedRangeEnforcer] = None,
        command_graph_builder: Optional[CommandGraphBuilder] = None,
        # LLM variant generator (tranche 3)
        llm_variant_generator: Optional[Any] = None,
    ):
        self._classifier = classifier
        self._domain_router = domain_router
        self._profiles = profiles
        self._extractor = extractor
        self._evaluator = evaluator
        self._schema = schema_enforcer
        self._router = router
        self._dispatcher = dispatcher
        self._chain_engine = chain_engine
        self._chain_definitions = chain_definitions
        self._product_templates = product_templates or {}
        self._repair_threshold = repair_threshold
        self._max_repair_passes = max_repair_passes
        # Creative layer
        self._variant_planner = variant_planner
        self._creativity_verifier = creativity_verifier
        self._output_policy = output_policy
        # Intervention / capability layer
        self._post_classifier = post_classification_validator
        self._range_enforcer = allowed_range_enforcer
        self._graph_builder = command_graph_builder
        self._llm_variant_gen = llm_variant_generator

    def execute(
        self,
        user_input: str,
        context: Optional[dict] = None,
    ) -> OrchestrationResult:
        start = time.time()

        # ── 1. ROUTE (mandatory) ──
        routing = self._domain_router.route(user_input, context)
        domain = routing.primary_domain
        task_name = routing.inferred_task

        # Classifier still runs for back-compat telemetry
        classification = self._classifier.classify(user_input, context)

        # ── 1.5 POST-CLASSIFICATION VALIDATION (intervention policy gate) ──
        post_class_result: Optional[PostClassificationResult] = None
        if self._post_classifier:
            candidates = [
                {"domain": c.domain, "score": c.score}
                for c in (routing.candidates if routing else [])
            ]
            post_class_result = self._post_classifier.validate(
                primary_domain=domain,
                task_name=task_name,
                user_input=user_input,
                classification_candidates=candidates,
            )
            if not post_class_result.passed:
                reason = post_class_result.reason
                if post_class_result.fail_loud:
                    return self._fail_loud_result(
                        routing, classification, user_input, domain,
                        f"intervention_policy_violation: {reason}", start,
                    )
                if post_class_result.needs_clarification:
                    return self._fail_loud_result(
                        routing, classification, user_input, domain,
                        f"clarification_required: {reason}", start,
                    )

        # ── 2. PROFILE (mandatory) ──
        profile = self._profiles.get(domain)
        if not profile:
            return self._fail_loud_result(
                routing, classification, user_input, domain,
                f"No profile for domain '{domain}'", start,
            )

        # ── 3. EXTRACT REQUIREMENTS ──
        envelope = self._extractor.extract(user_input, domain, task_name)

        # ── 4. TEMPLATE MATCHING ──
        template_enrichment = None
        template_id = ""
        domain_tmpl = match_domain_template(user_input, domain)
        if domain_tmpl:
            template_enrichment = domain_tmpl.to_enrichment()
            template_id = domain_tmpl.template_id
        elif self._product_templates:
            cad_tmpl = match_template(user_input, self._product_templates)
            if cad_tmpl:
                template_enrichment = cad_tmpl.to_slots_enrichment()
                template_id = cad_tmpl.product_id

        # ── 5. SELECT CHAIN ──
        chain_def = self._chain_definitions.get(profile.chain_name)
        if not chain_def:
            for cdef in self._chain_definitions.values():
                if cdef.domain == domain:
                    chain_def = cdef
                    break
        chain_name = chain_def.name if chain_def else ""

        # ── 6. EXECUTE (chain preferred, single-step fallback) ──
        if chain_def:
            result, slots = self._execute_chain(
                chain_def, user_input, context, template_enrichment,
                domain, envelope,
            )
        else:
            result, slots = self._execute_single(
                domain, task_name, user_input, context, profile, envelope,
            )

        # ── 6.5 CREATIVE PROFILE (resolve domain default or user override) ──
        creative_profile = resolve_creative_profile(domain, context)

        # ── 6.5.1 ALLOWED RANGE ENFORCEMENT ──
        range_result: Optional[RangeEnforcementResult] = None
        if self._range_enforcer:
            creative_profile, range_result = self._range_enforcer.enforce(
                domain, creative_profile,
            )

        # ── 6.6 VARIANT PLANNING (if multi-variant requested) ──
        variant_plan: Optional[VariantPlan] = None
        if (
            self._variant_planner
            and creative_profile.is_multi_variant
            and slots is not None
        ):
            # Build LLM-backed generator function if available
            gen_fn = None
            if self._llm_variant_gen:
                gen_fn = self._llm_variant_gen.create_generator_fn(
                    domain=domain,
                    task_name=task_name,
                    user_input=user_input,
                    context=context,
                    profile_reasoning=profile.reasoning_template if profile else "",
                )
            variant_plan = self._variant_planner.plan_variants(
                domain=domain,
                creative_profile=creative_profile,
                base_slots=slots,
                envelope=envelope,
                classification=classification,
                profile=profile,
                variant_generator_fn=gen_fn,
            )
            # Use best variant's slots for downstream validation
            best = variant_plan.best_slots
            if best is not None:
                slots = best
                result = TaskResult(
                    request_id=result.request_id,
                    task_id=result.task_id,
                    task_type=result.task_type,
                    status=result.status,
                    slots=slots,
                    latency_ms=result.latency_ms,
                )

        # ── 6.7 CREATIVITY VERIFICATION (for non-baseline variants) ──
        if variant_plan and self._creativity_verifier:
            baseline_slots = (
                variant_plan.variants[0].slots
                if variant_plan.variants
                else slots
            )
            for v in variant_plan.variants:
                if v.family != variant_plan.variants[0].family and v.accepted:
                    check = self._creativity_verifier.verify(
                        domain=domain,
                        variant_slots=v.slots,
                        baseline_slots=baseline_slots,
                        creative_profile=creative_profile,
                        envelope=envelope,
                    )
                    v.creativity_check = check.to_dict()
                    if not check.passed:
                        if check.repair_path == "shrink_to_safe":
                            v.accepted = False

        # ── 7. SCHEMA ENFORCEMENT (hard validation) ──
        schema_result = self._schema.validate(domain, slots)

        # ── 8. DOMAIN EVALUATION (quality scoring) ──
        evaluation = self._evaluator.evaluate(
            classification, envelope, profile, slots,
        )
        pre_repair_scores = evaluation.to_dict().get("scores", {})

        # ── 9. REPAIR (if needed, max 1 pass) ──
        repaired = False
        repair_trigger = ""
        post_repair_scores: dict = {}
        if evaluation.needs_repair and self._max_repair_passes > 0 and slots is not None:
            repair_trigger = "; ".join(evaluation.issues[:3])
            if chain_def:
                repaired, result, slots, evaluation = self._repair_chain(
                    chain_def, user_input, context, template_enrichment,
                    domain, envelope, classification, profile, evaluation,
                )
            else:
                repaired, result, slots, evaluation = self._repair_single(
                    domain, task_name, user_input, context, profile, envelope,
                    classification, slots, evaluation,
                )
            if repaired:
                post_repair_scores = evaluation.to_dict().get("scores", {})
                schema_result = self._schema.validate(domain, slots)

        # ── 10. FAIL-LOUD (if still failing after repair) ──
        fail_loud = False
        fail_loud_reason = ""
        if not schema_result.passed:
            fail_loud = True
            fail_loud_reason = f"schema_fail: {schema_result.issues[:3]}"
        elif evaluation.needs_repair and not repaired:
            fail_loud = True
            fail_loud_reason = f"eval_below_threshold: {evaluation.overall_score:.2f}, issues: {evaluation.issues[:3]}"

        # ── 10.5 OUTPUT POLICY (classify and validate output type) ──
        output_policy_result: Optional[OutputPolicyResult] = None
        if self._output_policy:
            output_policy_result = self._output_policy.classify_and_validate(
                slots=slots,
                variant_plan=variant_plan,
                fail_loud=fail_loud,
                fail_loud_reason=fail_loud_reason,
            )

        # ── 10.7 COMMAND GRAPH BUILD ──
        graph_bundle: Optional[CommandGraphBundle] = None
        if self._graph_builder and slots is not None and not fail_loud:
            baseline_graph = self._graph_builder.build_from_slots(
                domain=domain,
                task_name=task_name,
                slots=slots,
                variant_family="safe_baseline",
                creative_profile=creative_profile.to_dict() if creative_profile else None,
                intervention_result=(
                    post_class_result.intervention_result if post_class_result else None
                ),
                range_result=range_result.to_dict() if range_result else None,
            )
            variant_graphs = []
            if variant_plan:
                for v in variant_plan.variants:
                    if v.accepted and v.family != variant_plan.variants[0].family and v.slots:
                        vg = self._graph_builder.build_from_slots(
                            domain=domain,
                            task_name=task_name,
                            slots=v.slots,
                            variant_family=v.family,
                            creative_profile=creative_profile.to_dict() if creative_profile else None,
                        )
                        variant_graphs.append(vg)
            graph_bundle = self._graph_builder.build_bundle(baseline_graph, variant_graphs)

        # ── 11. TELEMETRY ──
        elapsed = int((time.time() - start) * 1000)
        telemetry = DomainTelemetryRecord(
            detected_domain=domain,
            domain_confidence=routing.primary_score,
            runner_up_domain=routing.candidates[1].domain if len(routing.candidates) > 1 else None,
            runner_up_confidence=routing.candidates[1].score if len(routing.candidates) > 1 else None,
            classification_ambiguous=routing.ambiguous,
            classification_reason=routing.reason[0] if routing.reason else "",
            extracted_constraints=envelope.hard_constraints,
            extracted_preferences=envelope.soft_preferences,
            execution_risk=envelope.execution_risk,
            selected_profile=domain,
            selected_chain=chain_name,
            template_used=template_id,
            domain_candidates=[{"domain": c.domain, "score": c.score} for c in routing.candidates],
            evaluator_scores=evaluation.to_dict().get("scores", {}),
            overall_score=evaluation.overall_score,
            repaired=repaired,
            repair_trigger_reason=repair_trigger,
            pre_repair_scores=pre_repair_scores,
            post_repair_scores=post_repair_scores,
            schema_validation_pass=schema_result.passed,
            schema_issues=schema_result.issues[:5],
            fail_loud_reason=fail_loud_reason,
            router_reason=routing.reason[:5],
            final_execution_type=(
                f"chain:{chain_name}" + ("+repair" if repaired else "")
                if chain_def else
                "enriched" + ("+repair" if repaired else "")
            ),
            total_orchestration_ms=elapsed,
            # Creative layer telemetry
            creative_mode=creative_profile.mode if creative_profile else "",
            variant_count=len(variant_plan.variants) if variant_plan else 0,
            variant_families=[v.family for v in variant_plan.variants] if variant_plan else [],
            selected_variant_family=(
                next((v.family for v in variant_plan.variants if v.variant_id == variant_plan.selected_variant_id), "")
                if variant_plan else ""
            ),
            creativity_verification_passed=(
                all(
                    v.creativity_check.get("passed", True)
                    for v in variant_plan.variants
                    if v.creativity_check
                )
                if variant_plan else None
            ),
            output_type=output_policy_result.output_type if output_policy_result else "",
            # Intervention / capability telemetry
            requested_task_family=(
                post_class_result.intervention_result.get("requested_task_family", "")
                if post_class_result and post_class_result.intervention_result else ""
            ),
            resolved_app_domain=domain,
            intervention_policy_passed=(
                post_class_result.passed if post_class_result else None
            ),
            intervention_violation_type=(
                post_class_result.intervention_result.get("violation_type", "")
                if post_class_result and post_class_result.intervention_result else ""
            ),
            creative_range_adjusted=(
                range_result.adjusted if range_result else False
            ),
            creative_range_violation_fields=(
                range_result.violations if range_result else []
            ),
            graph_output_type=(
                graph_bundle.output_type if graph_bundle else ""
            ),
            post_classification_passed=(
                post_class_result.passed if post_class_result else None
            ),
        )

        log_event(
            _log, "domain_runtime_complete",
            domain=domain, chain=chain_name, score=evaluation.overall_score,
            repaired=repaired, fail_loud=fail_loud, elapsed_ms=elapsed,
        )

        # ── LoRA training data collection (opt-in) ──
        if _training_collector is not None and slots and not fail_loud:
            try:
                sample = _training_collector.collect(
                    domain=domain,
                    task_family=task_name,
                    system_prompt=(profile.reasoning_template if profile else ""),
                    user_input=user_input,
                    output_slots=slots,
                    evaluation_score=evaluation.overall_score if evaluation else 0.0,
                    creative_profile=creative_profile.to_dict() if creative_profile else None,
                    constraints=envelope.hard_constraints if envelope else [],
                    tags=[chain_name] if chain_name else [],
                )
                if sample is not None:
                    _training_collector.write_sample(sample)
            except Exception as e:
                log_event(_log, "training_collection_error", error=str(e))

        return OrchestrationResult(
            task_result=result,
            classification=classification,
            routing=routing,
            requirement_envelope=envelope,
            domain_evaluation=evaluation,
            schema_validation=schema_result,
            repaired=repaired,
            fail_loud=fail_loud,
            fail_loud_reason=fail_loud_reason,
            telemetry=telemetry,
            variant_plan=variant_plan.to_dict() if variant_plan else None,
            output_policy=output_policy_result.to_dict() if output_policy_result else None,
            intervention_check=(
                post_class_result.intervention_result if post_class_result else None
            ),
            range_enforcement=range_result.to_dict() if range_result else None,
            command_graph=graph_bundle.to_dict() if graph_bundle else None,
            post_classification=post_class_result.to_dict() if post_class_result else None,
        )

    # ── Internal helpers ──

    def _execute_chain(self, chain_def, user_input, context, enrichment, domain, envelope):
        chain_result = self._chain_engine.execute_chain(
            chain=chain_def, user_input=user_input,
            context=context, enrichment=enrichment,
        )
        result = TaskResult(
            request_id="chain", task_id=f"chain_{chain_def.name}",
            task_type=f"{domain}.chain",
            status="done" if chain_result.success else "error",
            slots=chain_result.final_output,
            errors=[s.error for s in chain_result.steps_completed if s.error],
            latency_ms=chain_result.total_latency_ms,
        )
        return result, chain_result.final_output

    def _execute_single(self, domain, task_name, user_input, context, profile, envelope):
        base_prompt = self._dispatcher._load_prompt(
            self._router.resolve(TaskRequest(domain=domain, task_name=task_name, user_input=user_input))
        )
        enriched = self._build_enriched_prompt(profile, envelope, base_prompt)
        request = TaskRequest(
            domain=domain, task_name=task_name, user_input=user_input,
            context=context or {},
            metadata={"orchestrated": True},
        )
        spec = self._router.resolve(request)
        result = self._dispatcher.dispatch(request, spec, system_prompt_override=enriched)
        return result, result.slots

    def _repair_chain(self, chain_def, user_input, context, enrichment,
                      domain, envelope, classification, profile, pre_eval):
        repair_result = self._chain_engine.execute_chain(
            chain=chain_def, user_input=user_input,
            context=context, enrichment=enrichment,
        )
        if not repair_result.success or not repair_result.final_output:
            return False, None, None, pre_eval
        repair_eval = self._evaluator.evaluate(
            classification, envelope, profile, repair_result.final_output,
        )
        if repair_eval.overall_score > pre_eval.overall_score:
            repair_eval.repair_applied = True
            result = TaskResult(
                request_id="chain", task_id=f"chain_{chain_def.name}_repair",
                task_type=f"{domain}.chain",
                status="done", slots=repair_result.final_output,
                latency_ms=repair_result.total_latency_ms,
            )
            return True, result, repair_result.final_output, repair_eval
        return False, None, None, pre_eval

    def _repair_single(self, domain, task_name, user_input, context, profile,
                       envelope, classification, original_slots, pre_eval):
        repair_prompt = self._build_repair_prompt(profile, envelope, original_slots, pre_eval)
        request = TaskRequest(
            domain=domain, task_name=task_name, user_input=user_input,
            context=context or {},
        )
        spec = self._router.resolve(request)
        repair_result = self._dispatcher.dispatch(request, spec, system_prompt_override=repair_prompt)
        if repair_result.slots is None:
            return False, None, None, pre_eval
        repair_eval = self._evaluator.evaluate(
            classification, envelope, profile, repair_result.slots,
        )
        if repair_eval.overall_score > pre_eval.overall_score:
            repair_eval.repair_applied = True
            return True, repair_result, repair_result.slots, repair_eval
        return False, None, None, pre_eval

    def _fail_loud_result(self, routing, classification, user_input, domain, reason, start):
        elapsed = int((time.time() - start) * 1000)
        envelope = self._extractor.extract(user_input, domain, "")
        telemetry = DomainTelemetryRecord(
            detected_domain=domain,
            domain_confidence=routing.primary_score if routing else 0,
            fail_loud_reason=reason,
            total_orchestration_ms=elapsed,
        )
        return OrchestrationResult(
            task_result=TaskResult(request_id="fail", task_id="fail",
                                  task_type=f"{domain}.fail", status="error",
                                  errors=[reason]),
            classification=classification,
            routing=routing,
            requirement_envelope=envelope,
            domain_evaluation=None,
            schema_validation=None,
            repaired=False,
            fail_loud=True,
            fail_loud_reason=reason,
            telemetry=telemetry,
        )

    def _build_enriched_prompt(self, profile, envelope, base_prompt):
        parts = []
        if profile and profile.reasoning_template:
            parts.append(profile.reasoning_template)
        if envelope.hard_constraints:
            parts.append(f"User constraints: {', '.join(envelope.hard_constraints[:10])}")
        if envelope.domain_specific:
            parts.append(f"Detected specs: {', '.join(f'{k}={v}' for k, v in envelope.domain_specific.items())}")
        parts.append("You MUST produce domain-specific structured JSON. No generic text.")
        parts.append(base_prompt)
        return "\n\n".join(parts)

    def _build_repair_prompt(self, profile, envelope, original_output, evaluation):
        parts = [profile.reasoning_template]
        parts.append("The previous output is INCOMPLETE. Fix ONLY these issues:")
        if evaluation.missing_constraints:
            parts.append(f"- Missing: {', '.join(evaluation.missing_constraints)}")
        if evaluation.terminology_issues:
            parts.append(f"- Terminology: {', '.join(evaluation.terminology_issues)}")
        if evaluation.output_schema_compliance < 1.0 and profile.required_output_keys:
            missing = profile.required_output_keys - set(original_output.keys())
            if missing:
                parts.append(f"- Missing keys: {', '.join(missing)}")
        parts.append(f"\nPrevious output:\n{json.dumps(original_output, ensure_ascii=False)}")
        parts.append("\nOutput corrected JSON only.")
        return "\n".join(parts)
