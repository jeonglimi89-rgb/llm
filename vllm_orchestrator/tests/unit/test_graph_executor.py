"""Unit tests for graph_executor module."""
import pytest
from unittest.mock import MagicMock

from vllm_orchestrator.src.app.execution.graph_executor import (
    GraphExecutor, GraphExecutionResult, NodeExecutionResult, BundleExecutionResult,
)
from vllm_orchestrator.src.app.domain.command_graph import (
    GraphNode, CommandGraph, CommandGraphBundle, CommandGraphBuilder,
)
from vllm_orchestrator.src.app.domain.intervention_policy import InterventionPolicy
from vllm_orchestrator.src.app.domain.heuristic_checks import HeuristicDispatcher
from vllm_orchestrator.src.app.domain.heuristics import load_heuristic_packs
from vllm_orchestrator.src.app.tools.registry import ToolRegistry


@pytest.fixture
def mock_tool_registry():
    reg = ToolRegistry()
    reg.register("minecraft.compile_archetype", lambda p: {"status": "executed", "blocks": 42}, real=True)
    reg.register("builder.generate_plan", lambda p: {"status": "executed", "rooms": 5}, real=True)
    reg.register("animation.solve_shot", lambda p: {"status": "executed", "shots": 3}, real=True)
    reg.register("cad.generate_part", lambda p: {"status": "executed", "parts": 2}, real=True)
    return reg


@pytest.fixture
def executor(mock_tool_registry):
    return GraphExecutor(
        tool_registry=mock_tool_registry,
        heuristic_dispatcher=HeuristicDispatcher(),
        heuristic_packs=load_heuristic_packs(),
    )


@pytest.fixture
def graph_builder():
    return CommandGraphBuilder(InterventionPolicy())


class TestGraphExecutor:
    def test_execute_single_node_graph(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "minecraft", "build_parse",
            {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]},
        )
        result = executor.execute_graph(graph)
        assert result.success
        assert len(result.node_results) == 1
        assert result.node_results[0].status == "success"

    def test_execute_builder_graph(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "builder", "requirement_parse",
            {"spaces": [{"name": "living"}], "floors": 2},
        )
        result = executor.execute_graph(graph)
        assert result.success

    def test_execute_animation_graph(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "animation", "shot_parse",
            {"framing": "close_up", "mood": "tense"},
        )
        result = executor.execute_graph(graph)
        assert result.success

    def test_execute_cad_graph(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "cad", "constraint_parse",
            {"constraints": [{"type": "dimension"}]},
        )
        result = executor.execute_graph(graph)
        assert result.success

    def test_execute_empty_graph(self, executor):
        graph = CommandGraph(app_domain="minecraft")
        result = executor.execute_graph(graph)
        assert result.success
        assert len(result.node_results) == 0

    def test_execute_bundle(self, executor, graph_builder):
        baseline = graph_builder.build_from_slots(
            "minecraft", "build_parse",
            {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]},
        )
        variant = graph_builder.build_from_slots(
            "minecraft", "build_parse",
            {"target_anchor": {"type": "relative"}, "operations": [{"op": "fill"}]},
            variant_family="creative_variant",
        )
        bundle = graph_builder.build_bundle(baseline, [variant])
        result = executor.execute_bundle(bundle)
        assert result.overall_success
        assert result.baseline_result is not None
        assert len(result.variant_results) == 1

    def test_node_with_dependencies(self, executor):
        node1 = GraphNode(
            node_id="n1", app_domain="cad",
            capability_id="cad.design_brief_parse",
            inputs={"raw": "data"},
        )
        node2 = GraphNode(
            node_id="n2", app_domain="cad",
            capability_id="cad.design_drawing_generate",
            inputs={"refined": "data"},
            dependencies=["n1"],
        )
        graph = CommandGraph(
            app_domain="cad",
            nodes=[node1, node2],
            execution_order=["n1", "n2"],
        )
        result = executor.execute_graph(graph)
        assert result.success
        assert len(result.node_results) == 2

    def test_skipped_node_missing_dependency(self, executor):
        node = GraphNode(
            node_id="n1", app_domain="cad",
            capability_id="cad.design_drawing_generate",
            dependencies=["nonexistent"],
            inputs={"a": 1},
        )
        graph = CommandGraph(
            app_domain="cad",
            nodes=[node],
            execution_order=["n1"],
        )
        result = executor.execute_graph(graph)
        assert result.node_results[0].status == "skipped"

    def test_heuristic_results_populated(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "minecraft", "build_parse",
            {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]},
        )
        result = executor.execute_graph(graph)
        for nr in result.node_results:
            assert isinstance(nr.heuristic_results, list)

    def test_result_to_dict(self, executor, graph_builder):
        graph = graph_builder.build_from_slots(
            "minecraft", "build_parse", {"ops": [1]},
        )
        result = executor.execute_graph(graph)
        d = result.to_dict()
        assert "graph_id" in d
        assert "node_results" in d
        assert "success" in d

    def test_bundle_result_to_dict(self, executor, graph_builder):
        baseline = graph_builder.build_from_slots("cad", "constraint_parse", {"c": [1]})
        bundle = graph_builder.build_bundle(baseline)
        result = executor.execute_bundle(bundle)
        d = result.to_dict()
        assert "baseline" in d
        assert "overall_success" in d
