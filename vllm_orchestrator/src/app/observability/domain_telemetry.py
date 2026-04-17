"""
observability/domain_telemetry.py — Domain orchestration telemetry record.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DomainTelemetryRecord:
    """OrchestratedPipeline 1회 실행의 전체 관측 기록."""
    # Classification
    detected_domain: str = ""
    domain_confidence: float = 0.0
    runner_up_domain: Optional[str] = None
    runner_up_confidence: Optional[float] = None
    classification_ambiguous: bool = False
    classification_reason: str = ""

    # Requirements
    extracted_constraints: list[str] = field(default_factory=list)
    extracted_preferences: list[str] = field(default_factory=list)
    execution_risk: str = "low"

    # Profile
    selected_profile: str = ""

    # Evaluation
    evaluator_scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 0.0

    # Repair
    repaired: bool = False
    repair_delta: Optional[float] = None

    # Chain / template
    selected_chain: str = ""
    template_used: str = ""
    domain_candidates: list[dict] = field(default_factory=list)

    # Evaluation detail
    final_output_schema: str = ""
    schema_validation_pass: bool = True
    schema_issues: list[str] = field(default_factory=list)

    # Repair detail
    repair_trigger_reason: str = ""
    pre_repair_scores: dict[str, float] = field(default_factory=dict)
    post_repair_scores: dict[str, float] = field(default_factory=dict)
    fail_loud_reason: str = ""

    # Router detail
    router_reason: list[str] = field(default_factory=list)

    # Execution
    final_execution_type: str = "direct"  # "direct" | "enriched" | "enriched+repair" | "chain:..."
    total_orchestration_ms: int = 0

    # Creative layer (additive fields)
    creative_mode: str = ""
    variant_count: int = 0
    variant_families: list[str] = field(default_factory=list)
    selected_variant_family: str = ""
    creativity_verification_passed: Optional[bool] = None
    output_type: str = ""

    # Intervention / capability layer (tranche 2)
    requested_task_family: str = ""
    resolved_app_domain: str = ""
    intervention_policy_passed: Optional[bool] = None
    intervention_violation_type: str = ""
    creative_range_adjusted: bool = False
    creative_range_violation_fields: list[str] = field(default_factory=list)
    graph_output_type: str = ""
    capability_contract_mismatch: bool = False
    hard_lock_failure_type: str = ""
    post_classification_passed: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)
