"""
core/adapter_product_design.py — Product Design AI 엔진 어댑터

기존 backend/app/의 RuleBasedDesignEngine, compile_chat_change_packet 등을
core/ 파이프라인(Intent → Variant → Critique → Patch → Log)과 연결.

이 어댑터는 기존 코드를 수정하지 않고 위에 얹는 방식으로 동작.
기존 API 엔드포인트는 그대로 유지하면서, core/ 파이프라인을 통한
새로운 경로를 추가한다.
"""

from __future__ import annotations

import sys
import os
from copy import deepcopy
from typing import Any, Optional

# backend/app을 import 가능하게
_backend_app_dir = os.path.join(os.path.dirname(__file__), "..", "backend", "app")
if _backend_app_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_backend_app_dir))

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


class ProductDesignAdapter:
    """
    core/ 파이프라인을 기존 Product Design AI 엔진 위에 얹는 어댑터.

    역할:
    1. 사용자 자연어 → core/ Intent Parser → 구조화된 의도
    2. 의도 → 기존 엔진 호출 파라미터로 변환
    3. 기존 엔진 결과 → core/ Variant 형식으로 래핑
    4. Variant[] → core/ Critique/Ranker
    5. 수정 요청 → core/ Delta Patch → 기존 엔진 재호출
    6. 전체 세션 → core/ Memory Log
    """

    PROJECT_TYPE = "product_design"

    def __init__(self, data_dir: str = "data"):
        self.registry = SchemaRegistry()
        self.intent_parser = IntentParserModule(self.registry, self.PROJECT_TYPE)
        self.variant_gen = VariantGeneratorModule(self.registry, self.PROJECT_TYPE)
        self.ranker = CritiqueRankerModule(self.registry, self.PROJECT_TYPE)
        self.patcher = DeltaPatchInterpreter(self.registry, self.PROJECT_TYPE)
        self.log = MemoryLogPipeline(data_dir, self.PROJECT_TYPE)

        # 기존 엔진 (지연 로딩)
        self._engine = None

    @property
    def engine(self):
        """기존 RuleBasedDesignEngine 지연 로딩"""
        if self._engine is None:
            from app.ai_engine import RuleBasedDesignEngine
            self._engine = RuleBasedDesignEngine()
        return self._engine

    # ------------------------------------------------------------------
    # 1. 요구사항 단계: 자유 텍스트 → 구조화
    # ------------------------------------------------------------------

    def process_requirement(
        self,
        free_text: str,
        category: str = "small_appliance",
        context: Optional[dict] = None,
    ) -> dict:
        """
        사용자 자유 텍스트를 처리하여 구조화된 요구사항 + 의도 반환.

        Returns:
            {
                "intent": ParsedIntent,
                "requirement": dict (기존 Requirement 형식),
                "unknowns": list[str],
            }
        """
        # core/ Intent Parser로 의도 해석
        intent = self.intent_parser.parse(free_text, context or {})

        # 기존 엔진으로 요구사항 구조화
        from app.schemas import RequirementInput
        req_input = RequirementInput(
            free_text=free_text,
            budget_level=intent.constraints.get("budget_level", "medium"),
            power_type=intent.constraints.get("power_type", ""),
        )
        requirement = self.engine.extract_requirements(req_input)
        req_dict = requirement.model_dump() if hasattr(requirement, "model_dump") else vars(requirement)

        return {
            "intent": intent,
            "requirement": req_dict,
            "unknowns": req_dict.get("unknowns", []),
        }

    # ------------------------------------------------------------------
    # 2. 컨셉 단계: 후보안 생성 + 비평/랭킹
    # ------------------------------------------------------------------

    def generate_concepts_with_critique(
        self,
        requirement_dict: dict,
        category: str = "small_appliance",
        n_variants: int = 3,
        diversity_weight: float = 0.5,
        user_request: str = "",
    ) -> dict:
        """
        후보 컨셉 생성 + 비평 + 랭킹.

        기존 엔진의 generate_concepts()를 호출하되,
        결과를 core/ Variant/Critique로 래핑.

        Returns:
            {
                "variants": list[Variant],
                "critiques": list[Critique],
                "engine_concepts": list[dict],  # 기존 형식 (호환용)
            }
        """
        # 의도 해석
        intent = self.intent_parser.parse(
            user_request or "컨셉 생성",
            {"category": category},
        )

        # 기존 엔진으로 컨셉 생성
        from app.schemas import Requirement
        requirement = Requirement(**requirement_dict)
        concepts = self.engine.generate_concepts(requirement, category)

        # 기존 컨셉 → core/ Variant 래핑
        variants = []
        for concept in concepts:
            concept_dict = concept.model_dump() if hasattr(concept, "model_dump") else vars(concept)
            params = {
                "category": category,
                "requirement": requirement_dict,
                "concept": {
                    "name": concept_dict.get("name", ""),
                    "summary": concept_dict.get("summary", ""),
                    "estimated_complexity": concept_dict.get("estimated_complexity", "medium"),
                    "estimated_cost_level": concept_dict.get("estimated_cost_level", "medium"),
                    "best_for": concept_dict.get("best_for", ""),
                },
            }
            diff = {
                "concept.name": concept_dict.get("name"),
                "concept.estimated_complexity": concept_dict.get("estimated_complexity"),
            }
            variants.append(Variant(
                params=params,
                description=f"{concept_dict.get('name', '?')} - {concept_dict.get('summary', '')}",
                diff_from_base=diff,
                generation_method="engine_template",
                tags=[
                    f"complexity:{concept_dict.get('estimated_complexity', '?')}",
                    f"cost:{concept_dict.get('estimated_cost_level', '?')}",
                ],
            ))

        # 추가 variant (core/의 axes 기반 변이)
        if len(variants) < n_variants:
            extra = self.variant_gen.generate(
                intent,
                base_params=variants[0].params if variants else {},
                n_variants=n_variants - len(variants),
                diversity_weight=diversity_weight,
            )
            variants.extend(extra)

        # 비평/랭킹
        critiques = self.ranker.critique_all(variants[:n_variants], intent)

        # 기존 형식 호환
        engine_concepts = [
            c.model_dump() if hasattr(c, "model_dump") else vars(c)
            for c in concepts
        ]

        return {
            "variants": variants[:n_variants],
            "critiques": critiques,
            "engine_concepts": engine_concepts,
        }

    # ------------------------------------------------------------------
    # 3. 상세 설계 단계
    # ------------------------------------------------------------------

    def generate_detail_from_variant(
        self,
        variant: Variant,
        requirement_dict: dict,
        category: str = "small_appliance",
    ) -> dict:
        """
        선택된 Variant → 기존 엔진의 generate_detail() 호출.
        결과를 engine_params 형식으로 반환.
        """
        from app.schemas import Requirement
        requirement = Requirement(**requirement_dict)
        concept_name = variant.params.get("concept", {}).get("name", "")

        detail = self.engine.generate_detail(requirement, concept_name, category)
        detail_dict = detail.model_dump() if hasattr(detail, "model_dump") else vars(detail)

        # engine_params 형식으로 병합
        engine_params = deepcopy(variant.params)
        engine_params["dimensions"] = detail_dict.get("dimensions")
        engine_params["modules"] = detail_dict.get("modules", [])
        engine_params["bom_items"] = detail_dict.get("bom_items", [])
        engine_params["risks"] = detail_dict.get("risks", [])
        engine_params["fabrication_mode"] = detail_dict.get("fabrication_mode")
        engine_params["wiring"] = {
            "routes": detail_dict.get("wiring_routes", []),
            "connectors": detail_dict.get("connector_specs", []),
            "pcb_specs": detail_dict.get("pcb_specs", []),
            "cable_channels": detail_dict.get("cable_channels", []),
        }
        engine_params["fluid"] = {
            "drainage_paths": detail_dict.get("drainage_paths", []),
            "sealing_zones": detail_dict.get("sealing_zones", []),
            "hose_specs": detail_dict.get("hose_specs", []),
        }
        engine_params["system_layers"] = detail_dict.get("system_layers", [])
        engine_params["interference_records"] = detail_dict.get("interference_records", [])
        engine_params["maintenance_paths"] = detail_dict.get("maintenance_paths", [])

        return engine_params

    # ------------------------------------------------------------------
    # 4. 수정 요청 처리 (Delta Patch)
    # ------------------------------------------------------------------

    def process_edit(
        self,
        user_edit_request: str,
        current_params: dict,
        project_id: str = "",
    ) -> dict:
        """
        수정 요청 → Delta Patch → 적용.

        Returns:
            {
                "intent": ParsedIntent,
                "patch": DeltaPatch,
                "new_params": dict,
                "changed_paths": list[str],
            }
        """
        intent = self.intent_parser.parse(user_edit_request, {
            "current_artifact_id": project_id,
        })

        patch = self.patcher.interpret(user_edit_request, current_params, intent)
        new_params = self.patcher.apply(current_params, patch)

        changed_paths = [op.path for op in patch.operations]

        return {
            "intent": intent,
            "patch": patch,
            "new_params": new_params,
            "changed_paths": changed_paths,
        }

    # ------------------------------------------------------------------
    # 5. 채팅 명령 처리 (기존 compile_chat_change_packet 래핑)
    # ------------------------------------------------------------------

    def process_chat_command(
        self,
        instruction: str,
        bundle_dict: dict,
    ) -> dict:
        """
        채팅 명령을 처리. core/ Intent Parser를 먼저 적용하고,
        기존 compile_chat_change_packet도 병렬 실행하여 결과 비교.

        Returns:
            {
                "core_intent": ParsedIntent,
                "core_patch": DeltaPatch,
                "legacy_actions": list[dict],  # 기존 DesignChangeAction
                "agreement": bool,  # 두 결과가 일치하는지
            }
        """
        # core/ 경로
        core_intent = self.intent_parser.parse(instruction)

        # 현재 params 추출 (bundle에서)
        current_params = self._extract_params_from_bundle(bundle_dict)
        core_patch = self.patcher.interpret(instruction, current_params, core_intent)

        # 기존 경로
        legacy_actions = []
        try:
            from app.schemas import ProjectBundle, ChatChangeRequest
            from app.design_foundation import compile_chat_change_packet

            bundle = ProjectBundle(**bundle_dict)
            request = ChatChangeRequest(instruction=instruction)
            packet = compile_chat_change_packet(bundle, request)
            legacy_actions = [
                a.model_dump() if hasattr(a, "model_dump") else vars(a)
                for a in packet.actions
            ]
        except Exception:
            pass

        # 비교: 두 경로가 같은 필드를 수정하는지
        core_paths = {op.path for op in core_patch.operations}
        legacy_paths = {a.get("field_path", "") for a in legacy_actions}
        agreement = bool(core_paths & legacy_paths) or (not core_paths and not legacy_paths)

        return {
            "core_intent": core_intent,
            "core_patch": core_patch,
            "legacy_actions": legacy_actions,
            "agreement": agreement,
        }

    # ------------------------------------------------------------------
    # 6. 전체 세션 기록
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
        """전체 세션을 기록하고 SessionRecord 반환."""
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
    # 헬퍼
    # ------------------------------------------------------------------

    def _extract_params_from_bundle(self, bundle_dict: dict) -> dict:
        """ProjectBundle dict에서 engine_params 형식 추출"""
        params: dict[str, Any] = {}

        project = bundle_dict.get("project", {})
        params["category"] = project.get("category", "small_appliance")

        req = bundle_dict.get("requirement", {})
        if req:
            params["requirement"] = req

        detail = bundle_dict.get("detailed_design", {})
        if detail:
            params["dimensions"] = detail.get("dimensions")
            params["modules"] = detail.get("modules", [])
            params["bom_items"] = detail.get("bom_items", [])
            params["risks"] = detail.get("risks", [])

        concepts = bundle_dict.get("concepts", [])
        selected_id = bundle_dict.get("selected_concept_id")
        if concepts and selected_id:
            for c in concepts:
                if c.get("option_id") == selected_id:
                    params["concept"] = {
                        "name": c.get("name", ""),
                        "summary": c.get("summary", ""),
                        "estimated_complexity": c.get("estimated_complexity", "medium"),
                        "estimated_cost_level": c.get("estimated_cost_level", "medium"),
                    }
                    break

        return params
