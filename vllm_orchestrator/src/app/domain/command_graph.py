"""
domain/command_graph.py — Executable Command Graph.

The command graph is the primary output structure of the orchestrator.
It replaces free-form text output with a structured, executable format
that downstream program executors can consume directly.

Graph nodes represent individual capabilities with verified inputs/outputs.
Natural language is only allowed as optional metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from uuid import uuid4

from .capability_contract import CapabilityContract, get_capability
from .intervention_policy import InterventionPolicy, InterventionPolicyResult


@dataclass
class GraphNode:
    """Single executable node in the command graph."""
    node_id: str = field(default_factory=lambda: f"node_{uuid4().hex[:8]}")
    app_domain: str = ""
    capability_id: str = ""
    task_family: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)      # node_ids this depends on
    expected_outputs: list[str] = field(default_factory=list)
    verification_hooks: list[str] = field(default_factory=list)
    boundary_locks: list[str] = field(default_factory=list)
    creative_profile_applied: Optional[dict[str, Any]] = None
    intervention_policy_status: str = "passed"   # "passed" | "failed" | "skipped"
    metadata: dict[str, Any] = field(default_factory=dict)     # optional natural language notes

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> GraphNode:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CommandGraph:
    """Complete executable command graph for a request."""
    graph_id: str = field(default_factory=lambda: f"graph_{uuid4().hex[:8]}")
    app_domain: str = ""
    variant_family: str = "safe_baseline"
    nodes: list[GraphNode] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)   # ordered node_ids
    creative_profile: Optional[dict[str, Any]] = None
    intervention_check: Optional[dict[str, Any]] = None
    range_enforcement: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "graph_id": self.graph_id,
            "app_domain": self.app_domain,
            "variant_family": self.variant_family,
            "nodes": [n.to_dict() for n in self.nodes],
            "execution_order": self.execution_order,
            "creative_profile": self.creative_profile,
            "intervention_check": self.intervention_check,
            "range_enforcement": self.range_enforcement,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CommandGraph:
        nodes = [GraphNode.from_dict(n) for n in d.get("nodes", [])]
        return cls(
            graph_id=d.get("graph_id", ""),
            app_domain=d.get("app_domain", ""),
            variant_family=d.get("variant_family", "safe_baseline"),
            nodes=nodes,
            execution_order=d.get("execution_order", []),
            creative_profile=d.get("creative_profile"),
            intervention_check=d.get("intervention_check"),
            range_enforcement=d.get("range_enforcement"),
        )


@dataclass
class CommandGraphBundle:
    """Bundle of graphs: baseline + variants."""
    baseline: Optional[CommandGraph] = None
    variants: list[CommandGraph] = field(default_factory=list)
    output_type: str = "executable_command_graph"
    validation_passed: bool = True
    validation_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "output_type": self.output_type,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "variants": [v.to_dict() for v in self.variants],
            "validation_passed": self.validation_passed,
            "validation_issues": self.validation_issues,
        }

    @property
    def all_graphs(self) -> list[CommandGraph]:
        graphs = []
        if self.baseline:
            graphs.append(self.baseline)
        graphs.extend(self.variants)
        return graphs


class CommandGraphBuilder:
    """Builds executable command graphs from pipeline output."""

    def __init__(self, intervention_policy: InterventionPolicy):
        self._intervention = intervention_policy

    def build_from_slots(
        self,
        domain: str,
        task_name: str,
        slots: Optional[dict[str, Any]],
        *,
        variant_family: str = "safe_baseline",
        creative_profile: Optional[dict] = None,
        intervention_result: Optional[dict] = None,
        range_result: Optional[dict] = None,
    ) -> CommandGraph:
        """Build a command graph from pipeline output slots."""
        if not slots:
            return CommandGraph(
                app_domain=domain,
                variant_family=variant_family,
                creative_profile=creative_profile,
                intervention_check=intervention_result,
                range_enforcement=range_result,
            )

        # Determine the primary capability for this task
        primary_cap_id = self._resolve_capability(domain, task_name)

        # Build the primary node
        primary_node = GraphNode(
            app_domain=domain,
            capability_id=primary_cap_id,
            task_family=self._resolve_task_family(domain, task_name),
            inputs=slots,
            expected_outputs=self._infer_outputs(domain, slots),
            verification_hooks=self._get_verification_hooks(primary_cap_id),
            boundary_locks=self._get_boundary_locks(primary_cap_id),
            creative_profile_applied=creative_profile,
            intervention_policy_status="passed" if not intervention_result or intervention_result.get("passed") else "failed",
        )

        # Validate the node against intervention policy
        cap_check = self._intervention.check_graph_node(domain, primary_cap_id)
        if not cap_check.passed:
            primary_node.intervention_policy_status = "failed"

        graph = CommandGraph(
            app_domain=domain,
            variant_family=variant_family,
            nodes=[primary_node],
            execution_order=[primary_node.node_id],
            creative_profile=creative_profile,
            intervention_check=intervention_result,
            range_enforcement=range_result,
        )

        return graph

    def build_bundle(
        self,
        baseline_graph: CommandGraph,
        variant_graphs: Optional[list[CommandGraph]] = None,
    ) -> CommandGraphBundle:
        """Build a graph bundle from baseline and variants."""
        variants = variant_graphs or []
        has_variants = len(variants) > 0

        # Validate all graphs
        issues = []
        for g in [baseline_graph] + variants:
            for node in g.nodes:
                if node.intervention_policy_status == "failed":
                    issues.append(
                        f"Node {node.node_id} ({node.capability_id}) failed intervention policy"
                    )
                if not node.boundary_locks:
                    issues.append(
                        f"Node {node.node_id} ({node.capability_id}) has no boundary locks"
                    )

        return CommandGraphBundle(
            baseline=baseline_graph,
            variants=variants,
            output_type=(
                "executable_command_graph_with_variants" if has_variants
                else "executable_command_graph"
            ),
            validation_passed=len(issues) == 0,
            validation_issues=issues,
        )

    def _resolve_capability(self, domain: str, task_name: str) -> str:
        """Map domain+task to a capability_id."""
        # Direct lookup
        direct = f"{domain}.{task_name}"
        if get_capability(direct):
            return direct

        # Domain-specific generation capabilities
        _TASK_TO_CAP: dict[str, dict[str, str]] = {
            "minecraft": {
                "build_parse": "minecraft.build_plan_generate",
                "edit_parse": "minecraft.build_plan_generate",
                "style_check": "minecraft.build_style_validate",
                "anchor_resolution": "minecraft.build_plan_generate",
                "character_parse": "minecraft.npc_concept_generate",
                "dialogue_generate": "minecraft.npc_concept_generate",
                "style_parse": "minecraft.resourcepack_style_plan",
            },
            "builder": {
                "requirement_parse": "builder.exterior_drawing_generate",
                "patch_intent_parse": "builder.exterior_drawing_generate",
                "exterior_style_parse": "builder.exterior_drawing_generate",
                "zone_priority_parse": "builder.interior_drawing_generate",
            },
            "animation": {
                "shot_parse": "animation.camera_walk_plan_generate",
                "camera_intent_parse": "animation.camera_walk_plan_generate",
                "lighting_intent_parse": "animation.camera_walk_plan_generate",
                "edit_patch_parse": "animation.camera_walk_plan_generate",
                "creative_direction": "animation.camera_walk_plan_generate",
            },
            "cad": {
                "constraint_parse": "cad.design_drawing_generate",
                "patch_parse": "cad.design_drawing_generate",
                "system_split_parse": "cad.design_brief_parse",
                "priority_parse": "cad.design_brief_parse",
            },
            "product_design": {
                "requirement_parse": "cad.design_drawing_generate",
                "concept_parse": "cad.design_drawing_generate",
                "bom_parse": "cad.design_drawing_generate",
                "patch_parse": "cad.design_drawing_generate",
            },
        }
        domain_map = _TASK_TO_CAP.get(domain, {})
        return domain_map.get(task_name, f"{domain}.{task_name}")

    def _resolve_task_family(self, domain: str, task_name: str) -> str:
        from .intervention_policy import _TASK_TO_FAMILY
        family = _TASK_TO_FAMILY.get(task_name, "")
        return family if family and family != "_universal" else domain

    def _infer_outputs(self, domain: str, slots: dict) -> list[str]:
        return list(slots.keys())[:10]

    def _get_verification_hooks(self, capability_id: str) -> list[str]:
        cap = get_capability(capability_id)
        return list(cap.verifier_hooks) if cap else []

    def _get_boundary_locks(self, capability_id: str) -> list[str]:
        cap = get_capability(capability_id)
        return list(cap.hard_locks) if cap else []
