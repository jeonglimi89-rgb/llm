"""
routing/tool_router.py - 도메인 엔진 디스패치

LLM이 "어떤 도구를 부를지 선택"하면 여기서 실제 엔진을 호출한다.
현재는 더미 엔진. 각 도메인 엔진이 연결되면 실제 호출로 교체.
"""

from __future__ import annotations

from typing import Any, Callable


# ---------------------------------------------------------------------------
# 도구 레지스트리
# ---------------------------------------------------------------------------

class ToolRouter:
    """도메인 엔진 도구 레지스트리 + 디스패치"""

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._register_defaults()

    def register(self, name: str, func: Callable) -> None:
        self._tools[name] = func

    def call(self, name: str, params: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}", "available": list(self._tools.keys())}
        try:
            return self._tools[name](params)
        except Exception as e:
            return {"error": f"Tool '{name}' failed: {e}"}

    def list_tools(self) -> list[dict]:
        return [{"name": k, "type": "function"} for k in self._tools]

    def _register_defaults(self):
        """더미 엔진 등록. 실제 엔진 연결 시 register()로 교체."""

        # --- Animation ---
        self.register("brainstorm_animation", _dummy("brainstorm_animation"))
        self.register("generate_animation_plan", _dummy("generate_animation_plan"))
        self.register("solve_shot_camera", _dummy("solve_shot_camera"))
        self.register("validate_continuity", _dummy("validate_continuity"))

        # --- Minecraft ---
        self.register("brainstorm_minecraft_build", _dummy("brainstorm_minecraft_build"))
        self.register("compile_minecraft_archetype", _dummy("compile_minecraft_archetype"))
        self.register("validate_block_palette", _dummy("validate_block_palette"))
        self.register("score_human_like_build", _dummy("score_human_like_build"))

        # --- Builder ---
        self.register("lookup_site_regulations", _dummy("lookup_site_regulations"))
        self.register("generate_builder_plan", _builder_plan)
        self.register("generate_2d_floorplan", _generate_2d_floorplan)
        self.register("validate_builder_constraints", _builder_validate)

        # --- CAD ---
        self.register("brainstorm_cad_design", _dummy("brainstorm_cad_design"))
        self.register("generate_cad_plan", _dummy("generate_cad_plan"))
        self.register("generate_parametric_part", _dummy("generate_parametric_part"))
        self.register("solve_assembly_constraints", _dummy("solve_assembly_constraints"))
        self.register("route_wiring", _dummy("route_wiring"))
        self.register("route_drainage", _dummy("route_drainage"))
        self.register("validate_cad_geometry", _dummy("validate_cad_geometry"))
        self.register("generate_bom", _dummy("generate_bom"))


def _generate_2d_floorplan(params: dict) -> dict:
    """LLM slots → Builder 백엔드 /p3/generate_plan 호출 → SVG 반환"""
    import json
    import urllib.request
    import urllib.error

    backend_url = "http://localhost:8000/p3/generate_plan"
    payload = {
        "building_type": params.get("building_type", "apartment"),
        "floors": params.get("floors", 2),
        "rooms": params.get("rooms", []),
        "style": params.get("style", "modern"),
        "export_format": params.get("export_format", "svg"),
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            backend_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # Fallback: builder_planner만으로 JSON 평면 반환
        try:
            from tools.adapters.builder_planner import generate_plan
            plan = generate_plan(params.get("slots", params))
            return {"success": True, "plan_json": plan, "note": "SVG unavailable, JSON plan returned"}
        except Exception as e2:
            return {"error": f"generate_2d_floorplan failed: {e} / {e2}"}


def _builder_plan(params: dict) -> dict:
    """builder_planner.generate_plan 호출"""
    try:
        import sys
        from pathlib import Path
        orch_src = str(Path(__file__).resolve().parents[2] / "vllm_orchestrator" / "src" / "app")
        if orch_src not in sys.path:
            sys.path.insert(0, orch_src)
        from tools.adapters.builder_planner import generate_plan
        return generate_plan(params.get("slots", params))
    except Exception as e:
        return {"error": f"builder_planner failed: {e}", "echo_params": params}


def _builder_validate(params: dict) -> dict:
    """builder_validator.validate_plan 호출"""
    try:
        import sys
        from pathlib import Path
        orch_src = str(Path(__file__).resolve().parents[2] / "vllm_orchestrator" / "src" / "app")
        if orch_src not in sys.path:
            sys.path.insert(0, orch_src)
        from tools.adapters.builder_validator import validate_plan
        return validate_plan(params.get("plan", params))
    except Exception as e:
        return {"error": f"builder_validator failed: {e}", "echo_params": params}


def _dummy(name: str) -> Callable:
    """더미 도구: 입력을 그대로 에코"""
    def handler(params: dict) -> dict:
        return {
            "tool": name,
            "status": "dummy_ok",
            "echo_params": params,
            "note": f"[{name}] dummy engine — replace with real implementation",
        }
    return handler
