"""tools/registry.py - 도구 레지스트리. 실제 adapter 모음.

Contract (vocabulary)
=====================
- **real tool**: registered with ``register(name, func, real=True)``. Handler
  executes inline when ``call(name, params)`` is invoked. Handler returns a
  dict with ``status="executed"`` plus the actual computed payload.
- **manifest tool**: registered with ``register(name, func, real=False)``.
  Handler is intended to write a job manifest (via ``manifest_writer.write_manifest``)
  instead of running inline. Returns a dict with ``status="manifest_written"``.
  **Currently the production registry has zero manifest tools** — the path
  is preserved for forward compatibility, but ``create_default_registry()``
  registers all 14 tools as real.

Single source of truth
======================
The expected default registry composition is pinned in
``tools/registry_contract.py``. Tests read from there so any drift between
the registry and the constants raises ``RegistryContractError`` instead of
silently rotting an unrelated assertion.

Note on the ``write_manifest`` import below
===========================================
``write_manifest`` is imported but not invoked by ``create_default_registry()``
itself — it remains exported because external integrations and any future
deferred-job tool registration can still use it without re-introducing the
API. Removing the import would break those forward-compat call sites.
"""
from __future__ import annotations

from typing import Any, Callable
from .manifest_writer import write_manifest  # noqa: F401  (forward-compat re-export; see module docstring)

# 기존 엔진
from .adapters.minecraft_compiler import compile_edit as _mc_compile
from .adapters.minecraft_palette_validator import validate_palette as _mc_validate_palette
from .adapters.builder_planner import generate_plan as _builder_plan
from .adapters.builder_validator import validate_plan as _builder_validate

# CAD 엔진 (5)
from .adapters.cad_part_generator import generate_part as _cad_gen_part
from .adapters.cad_assembly_solver import solve_assembly as _cad_assembly
from .adapters.cad_wiring_router import route_wiring as _cad_wiring
from .adapters.cad_drainage_router import route_drainage as _cad_drainage
from .adapters.cad_geometry_validator import validate_geometry as _cad_validate

# Animation 엔진 (3)
from .adapters.animation_shot_solver import solve_shot as _anim_solve_shot
from .adapters.animation_preview import render_preview as _anim_preview
from .adapters.animation_continuity import check_continuity as _anim_continuity


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._real: set[str] = set()

    def register(self, name: str, func: Callable, real: bool = False) -> None:
        self._tools[name] = func
        if real:
            self._real.add(name)

    def call(self, name: str, params: dict) -> dict:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        try:
            return self._tools[name](params)
        except Exception as e:
            return {"error": f"{name} failed: {e}"}

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_real_tools(self) -> list[str]:
        return sorted(self._real)

    def list_manifest_tools(self) -> list[str]:
        return sorted(set(self._tools.keys()) - self._real)


def create_default_registry() -> ToolRegistry:
    reg = ToolRegistry()

    # ===== Minecraft (2) =====
    def _mc_compile_h(params):
        result = _mc_compile(params)
        return {
            "tool": "minecraft.compile_archetype",
            "status": "executed",
            "block_count": result["metadata"]["block_count"],
            "removed_count": result["metadata"]["removed_count"],
            "preserved": result["preserved"],
            "result": result,
        }
    reg.register("minecraft.compile_archetype", _mc_compile_h, real=True)

    def _mc_palette_h(params):
        theme = params.pop("_theme", "") if isinstance(params, dict) else ""
        result = _mc_validate_palette(params, theme=theme)
        return {
            "tool": "minecraft.validate_palette",
            "status": "executed",
            "verdict": result["verdict"],
            "style_score": result["style_score"],
            "unique_types": result["stats"]["unique_block_types"],
            "result": result,
        }
    reg.register("minecraft.validate_palette", _mc_palette_h, real=True)

    # minecraft.place_blocks: compile_archetype 결과를 받아 좌표 리스트 반환
    def _mc_place_h(params):
        # compile result 또는 직접 슬롯 지원
        if "blocks" in params:
            blocks = params["blocks"]
        else:
            comp = _mc_compile(params)
            blocks = comp.get("blocks", [])
        return {
            "tool": "minecraft.place_blocks",
            "status": "executed",
            "placement_count": len(blocks),
            "placements": blocks,
        }
    reg.register("minecraft.place_blocks", _mc_place_h, real=True)

    # ===== Builder (3) =====
    def _builder_gen_h(params):
        result = _builder_plan(params)
        return {
            "tool": "builder.generate_plan",
            "status": "executed",
            "floors": result["metadata"]["floors"],
            "total_rooms": result["metadata"]["total_rooms"],
            "total_area_m2": result["metadata"]["total_area_m2"],
            "style": result["metadata"]["style"],
            "result": result,
        }
    reg.register("builder.generate_plan", _builder_gen_h, real=True)

    def _builder_val_h(params):
        result = _builder_validate(params)
        return {
            "tool": "builder.validate",
            "status": "executed",
            "verdict": result["verdict"],
            "critical_issues": result["stats"]["critical_issues"],
            "warnings": result["stats"]["warnings"],
            "result": result,
        }
    reg.register("builder.validate", _builder_val_h, real=True)

    # builder.export: 평면을 텍스트 명세로 직렬화
    def _builder_export_h(params):
        plan = params.get("result", params)
        floor_plans = plan.get("floor_plans", [])
        lines = []
        for fp in floor_plans:
            lines.append(f"== Floor {fp.get('floor', '?')} ==")
            for r in fp.get("rooms", []):
                lines.append(f"  {r.get('name','?')} ({r.get('type','?')}): {r.get('w','?')}x{r.get('h','?')}m at ({r.get('x','?')},{r.get('y','?')}) = {r.get('area_m2','?')}m²")
        return {
            "tool": "builder.export",
            "status": "executed",
            "format": "text",
            "content": "\n".join(lines),
            "line_count": len(lines),
        }
    reg.register("builder.export", _builder_export_h, real=True)

    # ===== CAD (5) =====
    def _cad_gen_h(params):
        result = _cad_gen_part(params)
        return {
            "tool": "cad.generate_part",
            "status": "executed",
            "part_count": result["metadata"]["part_count"],
            "system_count": result["metadata"]["system_count"],
            "waterproof": result["metadata"]["waterproof"],
            "result": result,
        }
    reg.register("cad.generate_part", _cad_gen_h, real=True)

    def _cad_asm_h(params):
        result = _cad_assembly(params.get("result", params))
        return {
            "tool": "cad.solve_assembly",
            "status": "executed",
            "step_count": result["metadata"]["step_count"],
            "collision_count": result["metadata"]["collision_count"],
            "result": result,
        }
    reg.register("cad.solve_assembly", _cad_asm_h, real=True)

    def _cad_wire_h(params):
        part_result = params.get("part_result", params)
        asm_result = params.get("assembly_result")
        result = _cad_wiring(part_result, asm_result)
        return {
            "tool": "cad.route_wiring",
            "status": "executed",
            "wire_count": result["metadata"]["wire_count"],
            "total_length_mm": result["metadata"]["total_length_mm"],
            "conflict_count": result["metadata"]["conflict_count"],
            "result": result,
        }
    reg.register("cad.route_wiring", _cad_wire_h, real=True)

    def _cad_drain_h(params):
        part_result = params.get("part_result", params)
        asm_result = params.get("assembly_result")
        result = _cad_drainage(part_result, asm_result)
        return {
            "tool": "cad.route_drainage",
            "status": "executed",
            "drain_count": result["metadata"]["drain_count"],
            "valid_count": result["metadata"]["valid_count"],
            "critical_issues": result["metadata"]["critical_issues"],
            "result": result,
        }
    reg.register("cad.route_drainage", _cad_drain_h, real=True)

    def _cad_validate_h(params):
        part = params.get("part_result", params)
        asm = params.get("assembly_result")
        wire = params.get("wiring_result")
        drain = params.get("drainage_result")
        result = _cad_validate(part, asm, wire, drain)
        return {
            "tool": "cad.validate_geometry",
            "status": "executed",
            "verdict": result["verdict"],
            "total_issues": result["stats"]["total_issues"],
            "critical": result["stats"]["critical"],
            "result": result,
        }
    reg.register("cad.validate_geometry", _cad_validate_h, real=True)

    # ===== Animation (3) =====
    def _anim_solve_h(params):
        result = _anim_solve_shot(params)
        return {
            "tool": "animation.solve_shot",
            "status": "executed",
            "duration_frames": result["duration_frames"],
            "framing": result["camera"]["framing"],
            "result": result,
        }
    reg.register("animation.solve_shot", _anim_solve_h, real=True)

    def _anim_preview_h(params):
        shot = params.get("result", params)
        result = _anim_preview(shot)
        return {
            "tool": "animation.render_preview",
            "status": "executed",
            "keyframe_count": result["keyframe_count"],
            "duration_frames": result["duration_frames"],
            "result": result,
        }
    reg.register("animation.render_preview", _anim_preview_h, real=True)

    def _anim_continuity_h(params):
        shots = params if isinstance(params, list) else params.get("shots", [params])
        result = _anim_continuity(shots)
        return {
            "tool": "animation.check_continuity",
            "status": "executed",
            "verdict": result["verdict"],
            "shot_count": result["stats"]["shot_count"],
            "issues": len(result["issues"]),
            "result": result,
        }
    reg.register("animation.check_continuity", _anim_continuity_h, real=True)

    return reg
