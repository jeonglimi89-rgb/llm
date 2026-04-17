"""
orchestration/task_chain.py — Multi-step task chain engine.

0.5B 모델에 복잡한 설계를 한 번에 시키는 대신, 작은 step 으로 나눠서
각각 독립 dispatch. LLM step 과 tool step 을 자유롭게 섞을 수 있음.

Step 종류:
  - LLM step: task_name 으로 dispatcher.dispatch 호출. prompt 는 step 전용.
  - Tool step: "_tool:cad.generate_part" 처럼 _tool: prefix.
    LLM 호출 없이 tool_registry.call() 로 직접 실행.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.contracts import TaskRequest, TaskResult
from ..orchestration.router import Router
from ..orchestration.dispatcher import Dispatcher
from ..tools.registry import ToolRegistry
from ..observability.logger import get_logger, log_event

_log = get_logger("chain")

TOOL_PREFIX = "_tool:"


@dataclass
class ChainStepDef:
    """chain 내 단일 step 정의."""
    task_name: str          # "constraint_parse" 또는 "_tool:cad.generate_part"
    prompt: Optional[str]   # LLM step 전용 프롬프트 (tool step 이면 None)
    required: bool = True   # False → 실패해도 chain 계속

    @property
    def is_tool_step(self) -> bool:
        return self.task_name.startswith(TOOL_PREFIX)

    @property
    def tool_name(self) -> str:
        """_tool:cad.generate_part → cad.generate_part"""
        return self.task_name[len(TOOL_PREFIX):]


@dataclass
class ChainDefinition:
    """도메인별 multi-step chain 정의."""
    domain: str
    name: str
    steps: list[ChainStepDef]


@dataclass
class StepResult:
    step_index: int
    task_name: str
    is_tool: bool
    success: bool
    slots: Optional[dict] = None
    tool_output: Optional[dict] = None
    error: Optional[str] = None
    latency_ms: int = 0


@dataclass
class ChainResult:
    chain_name: str
    domain: str
    steps_completed: list[StepResult] = field(default_factory=list)
    final_output: Optional[dict] = None
    total_latency_ms: int = 0

    @property
    def success(self) -> bool:
        return all(s.success for s in self.steps_completed if s.task_name != "")

    def to_dict(self) -> dict:
        return {
            "chain_name": self.chain_name,
            "domain": self.domain,
            "steps_completed": [asdict(s) for s in self.steps_completed],
            "final_output": self.final_output,
            "total_latency_ms": self.total_latency_ms,
            "success": self.success,
        }


def load_chain_definitions(configs_dir: Path) -> dict[str, ChainDefinition]:
    """configs/task_chains.json 에서 chain 정의 로드."""
    path = configs_dir / "task_chains.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    chains: dict[str, ChainDefinition] = {}
    for name, data in raw.items():
        steps = [
            ChainStepDef(
                task_name=s["task_name"],
                prompt=s.get("prompt"),
                required=s.get("required", True),
            )
            for s in data.get("steps", [])
        ]
        chains[name] = ChainDefinition(
            domain=data["domain"],
            name=name,
            steps=steps,
        )
    return chains


class TaskChainEngine:
    """Multi-step chain 실행 엔진.

    LLM step 은 dispatcher.dispatch, tool step 은 tool_registry.call.
    이전 step 의 결과가 다음 step 의 입력으로 자동 전달.
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        router: Router,
        tool_registry: ToolRegistry,
    ):
        self._dispatcher = dispatcher
        self._router = router
        self._tools = tool_registry

    def execute_chain(
        self,
        chain: ChainDefinition,
        user_input: str,
        context: Optional[dict] = None,
        enrichment: Optional[dict] = None,
    ) -> ChainResult:
        start = time.time()
        result = ChainResult(chain_name=chain.name, domain=chain.domain)

        # 누적 context: 각 step 의 결과가 다음 step 의 입력으로
        accumulated: dict[str, Any] = {}
        if enrichment:
            accumulated.update(enrichment)

        last_llm_slots: Optional[dict] = None
        last_tool_output: Optional[dict] = None

        for i, step_def in enumerate(chain.steps):
            step_start = time.time()

            if step_def.is_tool_step:
                # Tool step: 누적 context 전체를 input 으로 (template + LLM slots 병합)
                tool_input = dict(accumulated)
                if last_tool_output:
                    tool_input.update(last_tool_output)
                step_result = self._execute_tool_step(
                    i, step_def, accumulated, tool_input,
                )
                if step_result.success and step_result.tool_output:
                    # tool 결과의 "result" 키를 다음 step 에 전달
                    inner = step_result.tool_output.get("result", step_result.tool_output)
                    accumulated[step_def.tool_name] = inner
                    last_tool_output = inner
            else:
                # LLM step: dispatcher 로 slot 추출
                step_result = self._execute_llm_step(
                    i, step_def, chain.domain, user_input, accumulated,
                )
                if step_result.success and step_result.slots:
                    if isinstance(step_result.slots, dict):
                        accumulated.update(step_result.slots)
                    else:
                        accumulated[step_def.task_name] = step_result.slots
                    last_llm_slots = step_result.slots

            step_result.latency_ms = int((time.time() - step_start) * 1000)
            result.steps_completed.append(step_result)

            log_event(
                _log, "chain_step_complete",
                chain=chain.name, step=i, task=step_def.task_name,
                success=step_result.success, latency_ms=step_result.latency_ms,
            )

            # required step 실패 → chain 중단
            if not step_result.success and step_def.required:
                break

        result.final_output = accumulated
        result.total_latency_ms = int((time.time() - start) * 1000)
        return result

    def _execute_llm_step(
        self,
        step_index: int,
        step_def: ChainStepDef,
        domain: str,
        user_input: str,
        accumulated: dict,
    ) -> StepResult:
        """LLM slot 추출 step."""
        try:
            # 이전 결과를 context 에 포함해 LLM 에게 힌트
            context_hint = ""
            if accumulated:
                context_hint = f"\n\nPrevious extraction results:\n{json.dumps(accumulated, ensure_ascii=False)[:500]}"

            prompt = (step_def.prompt or "") + context_hint
            request = TaskRequest(
                domain=domain,
                task_name=step_def.task_name,
                user_input=user_input,
            )
            spec = self._router.resolve(request)
            task_result = self._dispatcher.dispatch(
                request, spec,
                system_prompt_override=prompt if prompt.strip() else None,
            )

            if task_result.slots is not None:
                return StepResult(
                    step_index=step_index,
                    task_name=step_def.task_name,
                    is_tool=False,
                    success=True,
                    slots=task_result.slots,
                )
            else:
                return StepResult(
                    step_index=step_index,
                    task_name=step_def.task_name,
                    is_tool=False,
                    success=False,
                    error=f"LLM parse failed: {task_result.errors}",
                )
        except Exception as e:
            return StepResult(
                step_index=step_index,
                task_name=step_def.task_name,
                is_tool=False,
                success=False,
                error=str(e),
            )

    def _execute_tool_step(
        self,
        step_index: int,
        step_def: ChainStepDef,
        accumulated: dict,
        last_output: Optional[dict],
    ) -> StepResult:
        """Tool adapter 직접 실행 step."""
        try:
            # Tool 입력: 이전 step 의 결과 또는 누적 context
            tool_input = last_output or accumulated
            tool_result = self._tools.call(step_def.tool_name, tool_input)

            if "error" in tool_result:
                return StepResult(
                    step_index=step_index,
                    task_name=step_def.task_name,
                    is_tool=True,
                    success=False,
                    error=tool_result["error"],
                )

            return StepResult(
                step_index=step_index,
                task_name=step_def.task_name,
                is_tool=True,
                success=True,
                tool_output=tool_result,
            )
        except Exception as e:
            return StepResult(
                step_index=step_index,
                task_name=step_def.task_name,
                is_tool=True,
                success=False,
                error=str(e),
            )
