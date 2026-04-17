"""Unit tests for command_graph module."""
import pytest
from vllm_orchestrator.src.app.domain.command_graph import (
    GraphNode, CommandGraph, CommandGraphBundle, CommandGraphBuilder,
)
from vllm_orchestrator.src.app.domain.intervention_policy import InterventionPolicy


@pytest.fixture
def builder():
    return CommandGraphBuilder(InterventionPolicy())


class TestGraphNode:
    def test_to_dict(self):
        node = GraphNode(
            app_domain="minecraft",
            capability_id="minecraft.build_plan_generate",
            task_family="build",
            inputs={"style": "medieval"},
        )
        d = node.to_dict()
        assert d["app_domain"] == "minecraft"
        assert d["capability_id"] == "minecraft.build_plan_generate"
        assert "node_id" in d

    def test_from_dict_roundtrip(self):
        node = GraphNode(app_domain="cad", capability_id="cad.design_drawing_generate")
        d = node.to_dict()
        node2 = GraphNode.from_dict(d)
        assert node2.app_domain == "cad"


class TestCommandGraph:
    def test_to_dict(self):
        node = GraphNode(app_domain="builder", capability_id="builder.exterior_drawing_generate")
        graph = CommandGraph(
            app_domain="builder",
            variant_family="safe_baseline",
            nodes=[node],
            execution_order=[node.node_id],
        )
        d = graph.to_dict()
        assert d["app_domain"] == "builder"
        assert len(d["nodes"]) == 1


class TestCommandGraphBuilder:
    def test_build_minecraft_graph(self, builder):
        slots = {
            "target_anchor": {"type": "relative"},
            "operations": [{"op": "place", "block": "stone"}],
        }
        graph = builder.build_from_slots("minecraft", "build_parse", slots)
        assert graph.app_domain == "minecraft"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].capability_id == "minecraft.build_plan_generate"
        assert len(graph.nodes[0].boundary_locks) > 0

    def test_build_builder_graph(self, builder):
        slots = {"spaces": [{"name": "living"}], "floors": 2}
        graph = builder.build_from_slots("builder", "requirement_parse", slots)
        assert graph.nodes[0].capability_id == "builder.exterior_drawing_generate"

    def test_build_animation_graph(self, builder):
        slots = {"framing": "close_up", "mood": "tense"}
        graph = builder.build_from_slots("animation", "shot_parse", slots)
        assert graph.nodes[0].capability_id == "animation.camera_walk_plan_generate"

    def test_build_cad_graph(self, builder):
        slots = {"constraints": [{"type": "dimension"}]}
        graph = builder.build_from_slots("cad", "constraint_parse", slots)
        assert graph.nodes[0].capability_id == "cad.design_drawing_generate"

    def test_build_bundle_single(self, builder):
        graph = builder.build_from_slots("minecraft", "build_parse", {"ops": [1]})
        bundle = builder.build_bundle(graph)
        assert bundle.output_type == "executable_command_graph"
        assert bundle.baseline is not None
        assert len(bundle.variants) == 0

    def test_build_bundle_with_variants(self, builder):
        baseline = builder.build_from_slots("minecraft", "build_parse", {"ops": [1]})
        variant = builder.build_from_slots(
            "minecraft", "build_parse", {"ops": [2]},
            variant_family="creative_variant",
        )
        bundle = builder.build_bundle(baseline, [variant])
        assert bundle.output_type == "executable_command_graph_with_variants"
        assert len(bundle.variants) == 1

    def test_none_slots_empty_graph(self, builder):
        graph = builder.build_from_slots("minecraft", "build_parse", None)
        assert len(graph.nodes) == 0

    def test_graph_node_capability_exists_in_registry(self, builder):
        """Graph node capability_id should be in the capability registry."""
        from vllm_orchestrator.src.app.domain.capability_contract import get_capability
        slots = {"target_anchor": {"type": "relative"}, "operations": [{"op": "place"}]}
        graph = builder.build_from_slots("minecraft", "build_parse", slots)
        for node in graph.nodes:
            cap = get_capability(node.capability_id)
            assert cap is not None, f"Capability {node.capability_id} not in registry"

    def test_graph_boundary_locks_present(self, builder):
        """Graph nodes should have boundary_locks."""
        slots = {"constraints": [{"type": "dimension"}]}
        graph = builder.build_from_slots("cad", "constraint_parse", slots)
        for node in graph.nodes:
            assert len(node.boundary_locks) > 0, f"Node {node.node_id} missing boundary_locks"


class TestCrossDomainContamination:
    """D. Cross-domain contamination tests."""

    def test_builder_graph_no_animation_capability(self, builder):
        slots = {"spaces": [{"name": "living"}], "floors": 2}
        graph = builder.build_from_slots("builder", "requirement_parse", slots)
        for node in graph.nodes:
            assert not node.capability_id.startswith("animation."), \
                f"Builder graph contains animation capability: {node.capability_id}"

    def test_animation_graph_no_builder_capability(self, builder):
        slots = {"framing": "wide", "mood": "calm"}
        graph = builder.build_from_slots("animation", "shot_parse", slots)
        for node in graph.nodes:
            assert not node.capability_id.startswith("builder."), \
                f"Animation graph contains builder capability: {node.capability_id}"

    def test_cad_graph_no_minecraft_capability(self, builder):
        slots = {"constraints": [{"type": "sealing"}]}
        graph = builder.build_from_slots("cad", "constraint_parse", slots)
        for node in graph.nodes:
            assert not node.capability_id.startswith("minecraft."), \
                f"CAD graph contains minecraft capability: {node.capability_id}"
