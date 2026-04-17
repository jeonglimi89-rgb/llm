"""
command_graph.py — Executable Command Graph

사용자 요청이 통과하는 구조화된 파이프라인.
각 노드: (input_dict) → output_dict + telemetry.

Build Graph:
  intent_parse → task_select → domain_extract → planner
    → variant_plan → compile → detect → critique
    → repair_plan → repair_apply → final_emit
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("command_graph")


@dataclass
class NodeResult:
    node_name: str
    success: bool
    output: dict
    latency_ms: int = 0
    error: str = ""


@dataclass
class GraphExecution:
    graph_name: str
    nodes_completed: list[NodeResult] = field(default_factory=list)
    accumulated: dict = field(default_factory=dict)
    total_latency_ms: int = 0
    success: bool = True
    final_output: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "graph_name": self.graph_name,
            "success": self.success,
            "total_latency_ms": self.total_latency_ms,
            "nodes": [
                {"name": n.node_name, "success": n.success, "latency_ms": n.latency_ms, "error": n.error}
                for n in self.nodes_completed
            ],
            "output": self.final_output,
        }


@dataclass
class GraphNode:
    name: str
    handler: Callable[[dict], dict]
    required: bool = True
    timeout_s: float = 30.0


class CommandGraph:
    """실행 가능한 커맨드 그래프.

    각 노드는 순서대로 실행되며, 이전 노드의 output이
    다음 노드의 input에 누적 merge됨.
    """

    def __init__(self, name: str):
        self.name = name
        self._nodes: list[GraphNode] = []

    def add_node(
        self,
        name: str,
        handler: Callable[[dict], dict],
        required: bool = True,
        timeout_s: float = 30.0,
    ) -> "CommandGraph":
        self._nodes.append(GraphNode(name=name, handler=handler, required=required, timeout_s=timeout_s))
        return self

    def execute(self, initial_input: dict) -> GraphExecution:
        """그래프 전체 실행."""
        execution = GraphExecution(graph_name=self.name, accumulated=dict(initial_input))
        graph_start = time.perf_counter()

        for node in self._nodes:
            t0 = time.perf_counter()
            try:
                output = node.handler(execution.accumulated)
                latency = round((time.perf_counter() - t0) * 1000)

                result = NodeResult(
                    node_name=node.name,
                    success=True,
                    output=output,
                    latency_ms=latency,
                )
                execution.nodes_completed.append(result)
                execution.accumulated.update(output)

                log.info(f"[{self.name}] {node.name}: OK ({latency}ms)")

            except Exception as e:
                latency = round((time.perf_counter() - t0) * 1000)
                result = NodeResult(
                    node_name=node.name,
                    success=False,
                    output={},
                    latency_ms=latency,
                    error=str(e),
                )
                execution.nodes_completed.append(result)
                log.warning(f"[{self.name}] {node.name}: FAILED ({e})")

                if node.required:
                    execution.success = False
                    break
                # optional 노드: 실패해도 계속

        execution.total_latency_ms = round((time.perf_counter() - graph_start) * 1000)
        execution.final_output = execution.accumulated
        return execution


# ─── Pre-built Graph Definitions ─────────────────────────────────────

def build_minecraft_graph(
    intent_parser: Callable,
    task_selector: Callable,
    domain_extractor: Callable,
    planner: Callable,
    variant_planner: Callable,
    compiler: Callable,
    detector: Callable,
    critic: Callable,
    repair_planner: Callable,
    repair_applier: Callable,
    final_emitter: Callable,
) -> CommandGraph:
    """마인크래프트 빌드 LLM-active 커맨드 그래프 생성."""
    return (
        CommandGraph("minecraft_llm_active_build")
        .add_node("intent_parse", intent_parser)
        .add_node("task_select", task_selector)
        .add_node("domain_extract", domain_extractor)
        .add_node("planner", planner)
        .add_node("variant_plan", variant_planner)
        .add_node("compile", compiler)
        .add_node("detect", detector)
        .add_node("critique", critic)
        .add_node("repair_plan", repair_planner, required=False)
        .add_node("repair_apply", repair_applier, required=False)
        .add_node("final_emit", final_emitter)
    )
