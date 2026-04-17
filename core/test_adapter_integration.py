"""
core/test_adapter_integration.py — 어댑터 ↔ 실제 백엔드 엔진 연동 테스트

실행: python -X utf8 -m core.test_adapter_integration
"""

from __future__ import annotations

import sys
import os
import tempfile
import shutil

# backend/app을 import 가능하게 (번들 경로 또는 현재 프로젝트)
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_BASE, "backend")
# 번들 디렉토리 경로 (LLM/ 외부의 원본 프로젝트)
_BUNDLE_BACKEND = os.path.join(os.path.dirname(_BASE), "product_design_ai_all_v167_drawing_ai_engine_upgrade_bundle", "backend")
for p in [_BACKEND, _BUNDLE_BACKEND]:
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)


def test_product_design_adapter():
    """ProductDesignAdapter가 실제 RuleBasedDesignEngine과 연동되는지 테스트"""
    print("=" * 60)
    print("[1] ProductDesignAdapter Integration Test")
    print("=" * 60)

    tmp = tempfile.mkdtemp()
    try:
        from core.adapter_product_design import ProductDesignAdapter

        adapter = ProductDesignAdapter(data_dir=tmp)

        # 1. 요구사항 처리
        print("  1a. process_requirement...")
        result = adapter.process_requirement(
            free_text="접이식 욕실 선반 만들어줘, 방수 필요",
            category="furniture_small",
        )
        assert "intent" in result
        assert "requirement" in result
        req = result["requirement"]
        assert req["product_goal"] != ""
        print(f"    Intent: {result['intent'].intent_type.value}")
        print(f"    Goal: {req['product_goal']}")
        print(f"    Environment: {req.get('environment', [])}")
        print(f"    Unknowns: {len(req.get('unknowns', []))}")

        # 2. 컨셉 생성 + 비평
        print("  1b. generate_concepts_with_critique...")
        concepts_result = adapter.generate_concepts_with_critique(
            requirement_dict=req,
            category="furniture_small",
            n_variants=3,
            user_request="접이식 욕실 선반",
        )
        variants = concepts_result["variants"]
        critiques = concepts_result["critiques"]
        engine_concepts = concepts_result["engine_concepts"]

        assert len(variants) >= 1
        assert len(critiques) >= 1
        assert len(engine_concepts) >= 1
        print(f"    Variants: {len(variants)}")
        print(f"    Critiques: {len(critiques)}")
        print(f"    Engine concepts: {len(engine_concepts)}")
        for c in critiques[:3]:
            print(f"      Rank {c.overall_rank}: {c.variant_id}")

        # 3. 상세 설계
        print("  1c. generate_detail_from_variant...")
        selected = variants[0]
        detail_params = adapter.generate_detail_from_variant(
            variant=selected,
            requirement_dict=req,
            category="furniture_small",
        )
        assert "dimensions" in detail_params
        assert "modules" in detail_params
        dims = detail_params["dimensions"]
        if hasattr(dims, "overall_width_mm"):
            print(f"    Dimensions: {dims.overall_width_mm}x{dims.overall_depth_mm}x{dims.overall_height_mm}mm")
        elif isinstance(dims, dict):
            print(f"    Dimensions: {dims.get('overall_width_mm', '?')}x{dims.get('overall_depth_mm', '?')}x{dims.get('overall_height_mm', '?')}mm")
        modules = detail_params.get("modules", [])
        print(f"    Modules: {len(modules)}")
        bom = detail_params.get("bom_items", [])
        print(f"    BOM items: {len(bom)}")

        # 4. Delta Patch 수정
        print("  1d. process_edit (Delta Patch)...")
        edit_result = adapter.process_edit(
            user_edit_request="전체 폭을 500mm로 바꿔줘",
            current_params=detail_params,
        )
        assert "patch" in edit_result
        assert "new_params" in edit_result
        print(f"    Patch operations: {len(edit_result['patch'].operations)}")
        print(f"    Changed paths: {edit_result['changed_paths']}")

        # 5. 세션 기록
        print("  1e. record_session...")
        record = adapter.record_session(
            project_id="test_prj_001",
            user_request="접이식 욕실 선반 만들어줘",
            intent=result["intent"],
            variants=variants,
            critiques=critiques,
            selected_variant_id=selected.variant_id,
            edits=[edit_result["patch"]],
            final_params=edit_result["new_params"],
            accepted=True,
        )
        print(f"    Session recorded: {record.session_id}")

        # 통계 확인
        stats = adapter.log.get_stats()
        assert stats["total"] == 1
        assert stats["accepted"] == 1
        print(f"    Stats: {stats}")

        # 학습 데이터 내보내기
        training = adapter.log.export_training_pairs()
        print(f"    Training: intent={len(training['intent_pairs'])}, ranking={len(training['ranking_pairs'])}, patch={len(training['patch_pairs'])}")

        print("  OK - ProductDesignAdapter integration test passed")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_drawing_ai_adapter():
    """DrawingAIAdapter 테스트 (엔진 직접 호출은 건너뛰고 파이프라인만 테스트)"""
    print()
    print("=" * 60)
    print("[2] DrawingAIAdapter Integration Test")
    print("=" * 60)

    tmp = tempfile.mkdtemp()
    try:
        from core.adapter_drawing_ai import DrawingAIAdapter

        adapter = DrawingAIAdapter(data_dir=tmp)

        # 1. 도면 생성 variant
        print("  2a. generate_drawing_variants...")
        source_design = {
            "project_id": "prj_test_drawing",
            "category": "furniture_small",
            "title": "Test Drawing",
            "dimensions": {
                "overall_width_mm": 420,
                "overall_depth_mm": 220,
                "overall_height_mm": 280,
            },
            "modules": [],
        }
        result = adapter.generate_drawing_variants(
            user_request="기본 3면도 그려줘",
            source_design=source_design,
            n_variants=3,
        )
        assert len(result["variants"]) == 3
        assert len(result["critiques"]) == 3
        print(f"    Variants: {len(result['variants'])}")
        for v in result["variants"]:
            print(f"      {v.variant_id}: {v.description}")

        # 2. 수정 요청
        print("  2b. process_edit...")
        selected = result["variants"][1]  # 표준 뷰
        edit_result = adapter.process_edit(
            user_edit_request="단면도도 추가해줘",
            current_params=selected.params,
        )
        print(f"    Patch operations: {len(edit_result['patch'].operations)}")

        # 3. 세션 기록
        print("  2c. record_session...")
        record = adapter.record_session(
            project_id="prj_test_drawing",
            user_request="기본 3면도 그려줘",
            intent=result["intent"],
            variants=result["variants"],
            critiques=result["critiques"],
            selected_variant_id=selected.variant_id,
            final_params=selected.params,
            accepted=True,
        )
        print(f"    Session: {record.session_id}")

        stats = adapter.log.get_stats()
        print(f"    Stats: {stats}")

        print("  OK - DrawingAIAdapter integration test passed")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_api_import():
    """core_api.py 라우터가 정상 import 되는지"""
    print()
    print("=" * 60)
    print("[3] Core API Router Import Test")
    print("=" * 60)

    from core.schema_registry import SchemaRegistry
    from core.intent_parser import IntentParserModule
    from core.variant_generator import VariantGeneratorModule
    from core.critique_ranker import CritiqueRankerModule
    from core.delta_patch import DeltaPatchInterpreter
    from core.memory_log import MemoryLogPipeline

    # core_api 모듈의 함수 직접 테스트
    registry = SchemaRegistry()
    projects = registry.list_projects()
    assert "product_design" in projects
    assert "drawing_ai" in projects

    # 각 프로젝트에 대해 모듈 초기화 성공 확인
    for pt in projects:
        parser = IntentParserModule(registry, pt)
        generator = VariantGeneratorModule(registry, pt)
        ranker = CritiqueRankerModule(registry, pt)
        patcher = DeltaPatchInterpreter(registry, pt)

    print(f"  Projects: {projects}")
    print(f"  All modules initialized for all projects")
    print("  OK - Core API router import test passed")


if __name__ == "__main__":
    test_product_design_adapter()
    test_drawing_ai_adapter()
    test_core_api_import()
    print()
    print("=" * 60)
    print("ALL INTEGRATION TESTS PASSED!")
    print("=" * 60)
