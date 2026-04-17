"""
core/adapter_drawing_ai.py — Drawing AI 엔진 어댑터

기존 structural_drawing_engine.py, integrated_drawing_export.py를
core/ 파이프라인과 연결.
"""

from __future__ import annotations

import sys
import os
from copy import deepcopy
from typing import Any, Optional

from .models import (
    IntentType,
    ParsedIntent,
    Variant,
    Critique,
    DeltaPatch,
    SessionRecord,
)
from .schema_registry import SchemaRegistry
from .intent_parser import IntentParserModule
from .variant_generator import VariantGeneratorModule
from .critique_ranker import CritiqueRankerModule
from .delta_patch import DeltaPatchInterpreter
from .memory_log import MemoryLogPipeline


# Drawing AI 전용 뷰 프리셋
VIEW_PRESETS = {
    "minimal": [
        {"view_type": "front", "show_hidden_lines": True, "show_center_lines": True},
    ],
    "standard": [
        {"view_type": "front", "show_hidden_lines": True, "show_center_lines": True},
        {"view_type": "top", "show_hidden_lines": True, "show_center_lines": True},
        {"view_type": "side", "show_hidden_lines": True, "show_center_lines": True},
    ],
    "detailed": [
        {"view_type": "front", "show_hidden_lines": True, "show_center_lines": True},
        {"view_type": "top", "show_hidden_lines": True, "show_center_lines": True},
        {"view_type": "side", "show_hidden_lines": True, "show_center_lines": True},
        {"view_type": "section", "show_hatching": True, "section_plane": {"label": "A-A", "axis": "x", "offset_mm": 0}},
        {"view_type": "detail", "scale_override": "2:1"},
    ],
}

LAYER_PRESETS = {
    "structure_only": [
        {"layer_type": "structure", "visible": True, "color_hex": "#8B8B8B"},
    ],
    "structure_electrical": [
        {"layer_type": "structure", "visible": True, "color_hex": "#8B8B8B"},
        {"layer_type": "electrical", "visible": True, "color_hex": "#FF6B35"},
    ],
    "all_systems": [
        {"layer_type": "structure", "visible": True, "color_hex": "#8B8B8B"},
        {"layer_type": "electrical", "visible": True, "color_hex": "#FF6B35"},
        {"layer_type": "water_supply", "visible": True, "color_hex": "#2196F3"},
        {"layer_type": "drainage", "visible": True, "color_hex": "#4CAF50"},
        {"layer_type": "sensor_wire", "visible": True, "color_hex": "#9C27B0"},
        {"layer_type": "maintenance_path", "visible": True, "color_hex": "#FFC107"},
    ],
}


class DrawingAIAdapter:
    """
    core/ 파이프라인을 기존 Drawing AI 엔진 위에 얹는 어댑터.

    역할:
    1. 사용자 도면 요청 → Intent 해석
    2. Intent → 도면 파라미터 Variant 생성 (뷰 구성, 치수선, 레이어 등)
    3. Variant → 기존 엔진 호출 파라미터로 변환
    4. 기존 엔진이 SVG/PDF 렌더링
    5. 수정 요청 → Delta Patch (뷰 추가/제거, 치수 변경 등)
    """

    PROJECT_TYPE = "drawing_ai"

    def __init__(self, data_dir: str = "data"):
        self.registry = SchemaRegistry()
        self.intent_parser = IntentParserModule(self.registry, self.PROJECT_TYPE)
        self.variant_gen = VariantGeneratorModule(self.registry, self.PROJECT_TYPE)
        self.ranker = CritiqueRankerModule(self.registry, self.PROJECT_TYPE)
        self.patcher = DeltaPatchInterpreter(self.registry, self.PROJECT_TYPE)
        self.log = MemoryLogPipeline(data_dir, self.PROJECT_TYPE)

    # ------------------------------------------------------------------
    # 1. 도면 생성 요청 처리
    # ------------------------------------------------------------------

    def generate_drawing_variants(
        self,
        user_request: str,
        source_design: dict,
        n_variants: int = 3,
    ) -> dict:
        """
        도면 생성 요청 → 뷰 구성 후보안 + 비평.

        source_design: 설계 데이터 (project_id, category, dimensions, modules 등)

        Returns:
            {
                "intent": ParsedIntent,
                "variants": list[Variant],
                "critiques": list[Critique],
            }
        """
        intent = self.intent_parser.parse(user_request, {"source_design": source_design})

        # 기본 도면 파라미터 구성
        base_params = self._build_base_drawing_params(source_design, intent)

        # Variant 생성: 뷰 구성/레이어 가시성/주석 밀도 축으로 변이
        variants = self._generate_view_variants(base_params, intent, n_variants)

        # 비평
        critiques = self.ranker.critique_all(variants, intent)

        return {
            "intent": intent,
            "variants": variants,
            "critiques": critiques,
        }

    # ------------------------------------------------------------------
    # 2. 선택된 Variant로 도면 렌더링
    # ------------------------------------------------------------------

    def render_drawing(
        self,
        variant: Variant,
        bundle_dict: dict,
    ) -> dict:
        """
        선택된 Variant의 params로 기존 Drawing 엔진 호출.

        Returns:
            {
                "svg": str,
                "params_used": dict,
            }
        """
        try:
            from app.schemas import ProjectBundle
            from app.structural_drawing_engine import generate_full_drawing_svg
            from app.integrated_drawing_export import generate_integrated_drawing

            bundle = ProjectBundle(**bundle_dict)
            svg = generate_integrated_drawing(bundle)

            return {
                "svg": svg,
                "params_used": variant.params,
            }
        except ImportError:
            return {
                "svg": "<!-- Drawing engine not available -->",
                "params_used": variant.params,
            }

    # ------------------------------------------------------------------
    # 3. 수정 요청 처리
    # ------------------------------------------------------------------

    def process_edit(
        self,
        user_edit_request: str,
        current_params: dict,
    ) -> dict:
        """
        도면 수정 요청 → Delta Patch.
        예: "단면도도 추가해줘", "배선 레이어 숨겨줘"
        """
        intent = self.intent_parser.parse(user_edit_request)
        patch = self.patcher.interpret(user_edit_request, current_params, intent)
        new_params = self.patcher.apply(current_params, patch)

        return {
            "intent": intent,
            "patch": patch,
            "new_params": new_params,
        }

    # ------------------------------------------------------------------
    # 4. 세션 기록
    # ------------------------------------------------------------------

    def record_session(
        self,
        project_id: str,
        user_request: str,
        intent: ParsedIntent,
        variants: list[Variant],
        critiques: list[Critique],
        selected_variant_id: Optional[str] = None,
        edits: Optional[list[DeltaPatch]] = None,
        final_params: Optional[dict] = None,
        accepted: bool = False,
    ) -> SessionRecord:
        record = SessionRecord(
            project_id=project_id,
            project_type=self.PROJECT_TYPE,
            user_request=user_request,
            parsed_intent=intent,
            variants_generated=variants,
            critiques=critiques,
            user_selected_variant_id=selected_variant_id,
            user_edits=edits or [],
            final_params=final_params or {},
            final_accepted=accepted,
        )
        self.log.record_session(record)
        return record

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _build_base_drawing_params(self, source_design: dict, intent: ParsedIntent) -> dict:
        """source_design + intent에서 기본 도면 파라미터 구성"""
        params = {
            "sheet": {
                "width": 1400,
                "height": 960,
                "scale": "1:1",
                "projection_method": "third_angle",
                "title_block": {
                    "drawing_number": f"DRW-{source_design.get('project_id', 'XXXX')[:8]}",
                    "title": source_design.get("title", ""),
                    "material": "",
                    "revision": "A",
                },
            },
            "views": VIEW_PRESETS["standard"],
            "dimensions": [],
            "annotations": [],
            "system_layers": LAYER_PRESETS["structure_only"],
            "source_design": source_design,
            "output_format": "svg",
            "three_js_model": False,
        }

        # intent constraints 적용
        if intent.constraints.get("output_format"):
            params["output_format"] = intent.constraints["output_format"]
        if intent.constraints.get("three_js_model"):
            params["three_js_model"] = True

        return params

    def _generate_view_variants(
        self, base: dict, intent: ParsedIntent, n: int
    ) -> list[Variant]:
        """뷰 구성을 달리한 3가지 변이"""
        variants = []

        # Variant A: 최소 뷰 (정면도만)
        params_a = deepcopy(base)
        params_a["views"] = VIEW_PRESETS["minimal"]
        params_a["system_layers"] = LAYER_PRESETS["structure_only"]
        variants.append(Variant(
            params=params_a,
            description="최소 뷰: 정면도만, 구조 레이어만",
            diff_from_base={"views": "minimal", "layers": "structure_only"},
            generation_method="preset",
            tags=["view_set:minimal", "layer:structure_only"],
        ))

        # Variant B: 표준 뷰 (3면도)
        params_b = deepcopy(base)
        params_b["views"] = VIEW_PRESETS["standard"]
        params_b["system_layers"] = LAYER_PRESETS["structure_electrical"]
        variants.append(Variant(
            params=params_b,
            description="표준 뷰: 3면도, 구조+전기 레이어",
            diff_from_base={"views": "standard", "layers": "structure_electrical"},
            generation_method="preset",
            tags=["view_set:standard", "layer:structure_electrical"],
        ))

        # Variant C: 상세 뷰 (3면도 + 단면 + 상세)
        if n >= 3:
            params_c = deepcopy(base)
            params_c["views"] = VIEW_PRESETS["detailed"]
            params_c["system_layers"] = LAYER_PRESETS["all_systems"]
            variants.append(Variant(
                params=params_c,
                description="상세 뷰: 3면도+단면+상세, 전체 시스템 레이어",
                diff_from_base={"views": "detailed", "layers": "all_systems"},
                generation_method="preset",
                tags=["view_set:detailed", "layer:all_systems"],
            ))

        return variants[:n]
