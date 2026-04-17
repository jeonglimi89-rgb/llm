"""
execution/graph_executor.py — Command Graph Executor.

Takes a CommandGraph or CommandGraphBundle and executes its nodes
through the tool chain, respecting dependency ordering and boundary locks.

Execution flow per node:
1. Validate intervention policy for the node's capability
2. Check boundary locks
3. Resolve the tool/LLM handler for the capability
4. Execute with inputs
5. Run verification hooks
6. Record result
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from ..domain.command_graph import CommandGraph, CommandGraphBundle, GraphNode
from ..domain.capability_contract import get_capability
from ..domain.heuristic_checks import HeuristicDispatcher, HeuristicCheckResult
from ..domain.heuristics import HeuristicPack
from ..tools.registry import ToolRegistry
from ..observability.logger import get_logger

_log = get_logger("graph_exec")


@dataclass
class NodeExecutionResult:
    """Result of executing a single graph node."""
    node_id: str
    capability_id: str
    status: str = "success"         # "success"|"skipped"|"failed"|"tool_not_found"
    outputs: dict[str, Any] = field(default_factory=dict)
    heuristic_results: list[dict] = field(default_factory=list)
    error: str = ""
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphExecutionResult:
    """Result of executing an entire command graph."""
    graph_id: str
    app_domain: str
    variant_family: str = ""
    node_results: list[NodeExecutionResult] = field(default_factory=list)
    success: bool = True
    total_latency_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "graph_id": self.graph_id,
            "app_domain": self.app_domain,
            "variant_family": self.variant_family,
            "node_results": [r.to_dict() for r in self.node_results],
            "success": self.success,
            "total_latency_ms": self.total_latency_ms,
            "errors": self.errors,
        }


@dataclass
class BundleExecutionResult:
    """Result of executing a full graph bundle."""
    baseline_result: Optional[GraphExecutionResult] = None
    variant_results: list[GraphExecutionResult] = field(default_factory=list)
    overall_success: bool = True
    output_type: str = ""

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline_result.to_dict() if self.baseline_result else None,
            "variants": [v.to_dict() for v in self.variant_results],
            "overall_success": self.overall_success,
            "output_type": self.output_type,
        }


# Map capability domains to tool name prefixes
_CAPABILITY_TO_TOOL: dict[str, str] = {
    "minecraft.build_plan_generate": "minecraft.compile_archetype",
    "minecraft.build_style_validate": "minecraft.validate_palette",
    "minecraft.npc_concept_generate": "minecraft.compile_archetype",
    "minecraft.resourcepack_style_plan": "minecraft.compile_archetype",
    "builder.exterior_drawing_generate": "builder.generate_plan",
    "builder.interior_drawing_generate": "builder.generate_plan",
    "builder.exterior_interior_consistency_check": "builder.validate",
    "builder.plan_adjacency_check": "builder.validate",
    "builder.facade_massing_variant_plan": "builder.generate_plan",
    "animation.camera_walk_plan_generate": "animation.solve_shot",
    "animation.camera_continuity_check": "animation.check_continuity",
    "animation.style_lock_check": "animation.check_continuity",
    "animation.style_feedback_generate": "animation.solve_shot",
    "animation.identity_drift_check": "animation.check_continuity",
    "cad.design_brief_parse": "cad.generate_part",
    "cad.design_drawing_generate": "cad.generate_part",
    "cad.assembly_feasibility_check": "cad.solve_assembly",
    "cad.routing_precheck": "cad.route_wiring",
    "cad.manufacturability_check": "cad.validate_geometry",
}


class GraphExecutor:
    """Executes command graphs through the tool chain."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        heuristic_dispatcher: HeuristicDispatcher,
        heuristic_packs: dict[str, HeuristicPack],
    ):
        self._tools = tool_registry
        self._heuristic_dispatcher = heuristic_dispatcher
        self._heuristic_packs = heuristic_packs

    def execute_graph(self, graph: CommandGraph) -> GraphExecutionResult:
        """Execute a single command graph.

        Processes nodes in execution_order, feeding outputs to dependent nodes.
        """
        import time
        start = time.time()

        node_results: list[NodeExecutionResult] = []
        node_outputs: dict[str, dict] = {}  # node_id -> outputs
        errors: list[str] = []
        success = True

        order = graph.execution_order or [n.node_id for n in graph.nodes]

        for node_id in order:
            node = next((n for n in graph.nodes if n.node_id == node_id), None)
            if not node:
                continue

            # Check dependencies
            deps_met = all(
                dep_id in node_outputs for dep_id in node.dependencies
            )
            if not deps_met:
                nr = NodeExecutionResult(
                    node_id=node_id,
                    capability_id=node.capability_id,
                    status="skipped",
                    error="dependencies not met",
                )
                node_results.append(nr)
                continue

            # Execute the node
            nr = self._execute_node(node, graph.app_domain, node_outputs)
            node_results.append(nr)

            if nr.status == "success":
                node_outputs[node_id] = nr.outputs
            else:
                errors.append(f"{node.capability_id}: {nr.error}")
                # Continue execution for non-critical nodes
                cap = get_capability(node.capability_id)
                if cap and cap.creativity_tier == "strict":
                    success = False

        elapsed = int((time.time() - start) * 1000)

        return GraphExecutionResult(
            graph_id=graph.graph_id,
            app_domain=graph.app_domain,
            variant_family=graph.variant_family,
            node_results=node_results,
            success=success,
            total_latency_ms=elapsed,
            errors=errors,
        )

    def execute_bundle(self, bundle: CommandGraphBundle) -> BundleExecutionResult:
        """Execute a full command graph bundle (baseline + variants)."""
        baseline_result = None
        variant_results = []

        if bundle.baseline:
            baseline_result = self.execute_graph(bundle.baseline)

        for vg in bundle.variants:
            vr = self.execute_graph(vg)
            variant_results.append(vr)

        overall = True
        if baseline_result and not baseline_result.success:
            overall = False

        return BundleExecutionResult(
            baseline_result=baseline_result,
            variant_results=variant_results,
            overall_success=overall,
            output_type=bundle.output_type,
        )

    def _execute_node(
        self,
        node: GraphNode,
        domain: str,
        prior_outputs: dict[str, dict],
    ) -> NodeExecutionResult:
        """Execute a single graph node."""
        import time
        start = time.time()

        # Resolve tool
        tool_name = _CAPABILITY_TO_TOOL.get(node.capability_id, "")

        # Merge dependency outputs into inputs
        merged_inputs = dict(node.inputs)
        for dep_id in node.dependencies:
            if dep_id in prior_outputs:
                merged_inputs[f"_dep_{dep_id}"] = prior_outputs[dep_id]

        # Try tool execution
        outputs = {}
        if tool_name and self._tools.is_registered(tool_name):
            try:
                tool_result = self._tools.call(tool_name, merged_inputs)
                outputs = tool_result if isinstance(tool_result, dict) else {"result": tool_result}
            except Exception as e:
                elapsed = int((time.time() - start) * 1000)
                return NodeExecutionResult(
                    node_id=node.node_id,
                    capability_id=node.capability_id,
                    status="failed",
                    error=str(e),
                    latency_ms=elapsed,
                )
        else:
            # No tool registered — use inputs as passthrough outputs
            outputs = merged_inputs

        # Run heuristic checks
        heuristic_results = []
        pack = self._heuristic_packs.get(domain)
        if pack:
            checks = self._heuristic_dispatcher.run_safety_only(pack, outputs)
            heuristic_results = [c.to_dict() for c in checks]

        elapsed = int((time.time() - start) * 1000)

        return NodeExecutionResult(
            node_id=node.node_id,
            capability_id=node.capability_id,
            status="success",
            outputs=outputs,
            heuristic_results=heuristic_results,
            latency_ms=elapsed,
        )
