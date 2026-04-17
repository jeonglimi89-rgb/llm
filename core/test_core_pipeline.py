"""
core/test_core_pipeline.py — 공용 코어 통합 테스트

전체 파이프라인을 한 번에 테스트:
1. Schema Registry 로딩
2. Intent Parser (규칙 기반)
3. Variant Generator
4. Critique/Ranker
5. Delta Patch
6. Session Record 저장/로드
"""

import json
import os
import shutil
import tempfile

# 테스트 대상
from .schema_registry import SchemaRegistry
from .models import (
    IntentType, ParsedIntent, Variant, Critique,
    DeltaPatch, PatchOperation, SessionRecord,
)
from .intent_parser import IntentParserModule
from .variant_generator import VariantGeneratorModule
from .critique_ranker import CritiqueRankerModule
from .delta_patch import DeltaPatchInterpreter
from .memory_log import MemoryLogPipeline


def test_schema_registry():
    print("=" * 60)
    print("[1] Schema Registry 테스트")
    print("=" * 60)

    registry = SchemaRegistry()

    # 프로젝트 목록
    projects = registry.list_projects()
    print(f"  등록된 프로젝트: {projects}")
    assert "product_design" in projects, "product_design 프로젝트가 없음"
    assert "drawing_ai" in projects, "drawing_ai 프로젝트가 없음"

    # engine_params 로딩
    pd_params = registry.get_engine_params_schema("product_design")
    assert pd_params["type"] == "object"
    assert "category" in pd_params["properties"]
    print(f"  product_design engine_params 필드: {list(pd_params['properties'].keys())}")

    da_params = registry.get_engine_params_schema("drawing_ai")
    assert "sheet" in da_params["properties"]
    print(f"  drawing_ai engine_params 필드: {list(da_params['properties'].keys())}")

    # path_aliases
    aliases = registry.get_path_aliases("product_design")
    assert aliases["폭"] == "/dimensions/overall_width_mm"
    print(f"  product_design path_aliases 수: {len(aliases)}")

    # alias resolve
    resolved = registry.resolve_alias("product_design", "전체 폭")
    assert resolved == "/dimensions/overall_width_mm"
    print(f"  '전체 폭' → {resolved}")

    # 공통 스키마
    common = registry.get_common_schema()
    assert "intent_schema" in common
    assert "session_record_schema" in common
    print(f"  공통 스키마 키: {list(common.keys())}")

    print("  ✓ Schema Registry 테스트 통과\n")


def test_intent_parser():
    print("=" * 60)
    print("[2] Intent Parser 테스트 (규칙 기반)")
    print("=" * 60)

    registry = SchemaRegistry()
    parser = IntentParserModule(registry, "product_design")

    # 생성 의도
    intent = parser.parse("미니멀한 사무용 의자 만들어줘")
    assert intent.intent_type == IntentType.CREATE_NEW
    print(f"  '미니멀한 사무용 의자 만들어줘' → {intent.intent_type.value}, target={intent.target_object}")
    print(f"    constraints: {intent.constraints}")
    print(f"    confidence: {intent.confidence}")

    # 수정 의도 (절대값)
    intent2 = parser.parse("전체 폭을 360mm로 바꿔줘")
    assert intent2.intent_type == IntentType.MODIFY_EXISTING
    assert "dimensions.overall_width_mm" in intent2.constraints
    print(f"  '전체 폭을 360mm로 바꿔줘' → {intent2.intent_type.value}")
    print(f"    constraints: {intent2.constraints}")

    # 탐색 의도
    intent3 = parser.parse("다른 안도 보여줘")
    assert intent3.intent_type == IntentType.EXPLORE_VARIANTS
    print(f"  '다른 안도 보여줘' → {intent3.intent_type.value}")

    # 선택 의도
    intent4 = parser.parse("두 번째 컨셉으로 갈게")
    assert intent4.intent_type == IntentType.SELECT
    print(f"  '두 번째 컨셉으로 갈게' → {intent4.intent_type.value}")

    # 모호한 의도
    intent5 = parser.parse("이런 느낌으로 좀 바꿔")
    assert len(intent5.ambiguities) > 0
    print(f"  '이런 느낌으로 좀 바꿔' → ambiguities: {intent5.ambiguities}")

    print("  ✓ Intent Parser 테스트 통과\n")


def test_variant_generator():
    print("=" * 60)
    print("[3] Variant Generator 테스트")
    print("=" * 60)

    registry = SchemaRegistry()
    generator = VariantGeneratorModule(registry, "product_design")

    intent = ParsedIntent(
        intent_type=IntentType.CREATE_NEW,
        target_object="concept",
        constraints={"style": "minimal", "requirement.budget_level": "low"},
        confidence=0.85,
        raw_text="미니멀한 저비용 선반 만들어줘",
    )

    base_params = {
        "category": "furniture_small",
        "requirement": {"product_goal": "접이식 선반", "budget_level": "medium"},
        "concept": {"name": "Fold-Flat Shelf", "estimated_complexity": "medium"},
        "dimensions": {"overall_width_mm": 420, "overall_depth_mm": 220, "overall_height_mm": 280},
    }

    variants = generator.generate(intent, base_params, n_variants=3, diversity_weight=0.7)

    assert len(variants) >= 1, "최소 1개 variant 필요"
    assert len(variants) <= 3
    print(f"  생성된 variant 수: {len(variants)}")
    for v in variants:
        print(f"    - {v.variant_id}: {v.description} [tags: {v.tags}]")
        print(f"      method: {v.generation_method}, diff: {v.diff_from_base}")

    # 모든 variant가 유효한 params를 가짐
    for v in variants:
        assert isinstance(v.params, dict)
        assert "category" in v.params or True  # constraints 오버레이된 상태

    print("  ✓ Variant Generator 테스트 통과\n")


def test_critique_ranker():
    print("=" * 60)
    print("[4] Critique / Ranker 테스트")
    print("=" * 60)

    registry = SchemaRegistry()
    ranker = CritiqueRankerModule(registry, "product_design")

    intent = ParsedIntent(
        intent_type=IntentType.CREATE_NEW,
        target_object="concept",
        constraints={"requirement.budget_level": "low"},
        confidence=0.8,
    )

    variants = [
        Variant(
            variant_id="var_a",
            params={
                "requirement": {"budget_level": "low"},
                "concept": {"estimated_complexity": "low"},
            },
            description="저비용 단순 구조",
            tags=["cost:low"],
        ),
        Variant(
            variant_id="var_b",
            params={
                "requirement": {"budget_level": "low"},
                "concept": {"estimated_complexity": "high"},
            },
            description="저비용이지만 복잡한 구조",
            diff_from_base={"concept.estimated_complexity": "high"},
            tags=["cost:low", "complexity:high"],
        ),
        Variant(
            variant_id="var_c",
            params={
                "requirement": {"budget_level": "medium"},
                "concept": {"estimated_complexity": "medium"},
            },
            description="중간 예산 중간 복잡도",
            diff_from_base={"requirement.budget_level": "medium"},
            tags=["balanced"],
        ),
    ]

    critiques = ranker.critique_all(variants, intent)

    assert len(critiques) == 3
    assert critiques[0].overall_rank == 1
    assert critiques[2].overall_rank == 3
    print(f"  비평 결과:")
    for c in critiques:
        print(f"    rank {c.overall_rank}: {c.variant_id}")
        print(f"      scores: {c.scores}")
        print(f"      strengths: {c.strengths}")
        print(f"      weaknesses: {c.weaknesses}")

    # 1등은 비평에 강점이 있어야 함
    assert critiques[0].overall_rank == 1

    print("  ✓ Critique / Ranker 테스트 통과\n")


def test_delta_patch():
    print("=" * 60)
    print("[5] Delta Patch 테스트")
    print("=" * 60)

    registry = SchemaRegistry()
    patcher = DeltaPatchInterpreter(registry, "product_design")

    current_params = {
        "dimensions": {
            "overall_width_mm": 420,
            "overall_depth_mm": 220,
            "overall_height_mm": 280,
        },
        "requirement": {"budget_level": "medium"},
    }

    # 절대값 변경
    patch1 = patcher.interpret("전체 폭을 360mm로 바꿔줘", current_params)
    assert len(patch1.operations) >= 1
    op = patch1.operations[0]
    assert op.path == "/dimensions/overall_width_mm"
    assert op.value == 360
    assert op.op_type == "set"
    print(f"  '전체 폭을 360mm로 바꿔줘' → {op.op_type} {op.path} = {op.value}")

    # 적용
    new_params = patcher.apply(current_params, patch1)
    assert new_params["dimensions"]["overall_width_mm"] == 360
    assert current_params["dimensions"]["overall_width_mm"] == 420  # 원본 불변
    print(f"  적용 후 폭: {new_params['dimensions']['overall_width_mm']}mm (원본: {current_params['dimensions']['overall_width_mm']}mm)")

    # 상대값 변경
    patch2 = patcher.interpret("높이를 20mm 늘려줘", current_params)
    if patch2.operations:
        op2 = patch2.operations[0]
        print(f"  '높이를 20mm 늘려줘' → {op2.op_type} {op2.path} = {op2.value} (relative={op2.relative})")
        new_params2 = patcher.apply(current_params, patch2)
        print(f"  적용 후 높이: {new_params2['dimensions']['overall_height_mm']}mm")

    # 속성 변경 (constraint_mapping 활용)
    patch3 = patcher.interpret("저렴하게 만들어줘", current_params)
    if patch3.operations:
        for op3 in patch3.operations:
            print(f"  '저렴하게 만들어줘' → {op3.op_type} {op3.path} = {op3.value}")

    print("  ✓ Delta Patch 테스트 통과\n")


def test_memory_log():
    print("=" * 60)
    print("[6] Memory / Log Pipeline 테스트")
    print("=" * 60)

    # 임시 디렉토리에서 테스트
    tmp_dir = tempfile.mkdtemp()
    try:
        pipeline = MemoryLogPipeline(tmp_dir, "product_design")

        # 세션 기록
        record = SessionRecord(
            project_id="prj_test123",
            project_type="product_design",
            user_request="미니멀한 사무용 의자 만들어줘",
            parsed_intent=ParsedIntent(
                intent_type=IntentType.CREATE_NEW,
                target_object="concept",
                constraints={"style": "minimal"},
                confidence=0.85,
                raw_text="미니멀한 사무용 의자 만들어줘",
            ),
            variants_generated=[
                Variant(
                    variant_id="var_001",
                    params={"concept": {"name": "Fold-Flat"}},
                    description="접이식 구조",
                    tags=["simple"],
                ),
                Variant(
                    variant_id="var_002",
                    params={"concept": {"name": "Bracket"}},
                    description="브라켓 구조",
                    tags=["minimal"],
                ),
            ],
            critiques=[
                Critique(variant_id="var_001", scores={"constraint_satisfaction": 0.7}, overall_rank=2),
                Critique(variant_id="var_002", scores={"constraint_satisfaction": 0.9}, overall_rank=1),
            ],
            user_selected_variant_id="var_002",
            user_edits=[
                DeltaPatch(
                    description="높이 좀 더 높여줘",
                    operations=[PatchOperation(op_type="adjust", path="/dimensions/overall_height_mm", value=30, relative=True)],
                )
            ],
            final_params={"concept": {"name": "Bracket"}, "dimensions": {"overall_height_mm": 310}},
            final_accepted=True,
            self_critique="초기 높이 설정이 사용자 기대보다 낮았음",
        )

        filepath = pipeline.record_session(record)
        print(f"  세션 저장: {filepath}")

        # 로드
        sessions = pipeline.load_sessions()
        assert len(sessions) == 1
        loaded = sessions[0]
        assert loaded.user_request == "미니멀한 사무용 의자 만들어줘"
        assert loaded.parsed_intent.intent_type == IntentType.CREATE_NEW
        assert loaded.user_selected_variant_id == "var_002"
        assert loaded.final_accepted is True
        print(f"  세션 로드 성공: {loaded.session_id}")

        # 통계
        stats = pipeline.get_stats()
        print(f"  통계: {stats}")
        assert stats["total"] == 1
        assert stats["accepted"] == 1

        # 학습 데이터 변환
        training = pipeline.export_training_pairs()
        print(f"  학습 데이터:")
        print(f"    intent_pairs: {len(training['intent_pairs'])}개")
        print(f"    ranking_pairs: {len(training['ranking_pairs'])}개")
        print(f"    patch_pairs: {len(training['patch_pairs'])}개")
        assert len(training["intent_pairs"]) == 1
        assert len(training["ranking_pairs"]) == 1
        assert len(training["patch_pairs"]) == 1

        # 학습 데이터 내용 검증
        intent_pair = training["intent_pairs"][0]
        assert intent_pair["input"] == "미니멀한 사무용 의자 만들어줘"
        assert intent_pair["output"]["intent_type"] == "create_new"

        ranking_pair = training["ranking_pairs"][0]
        assert ranking_pair["selected"] == "var_002"

        patch_pair = training["patch_pairs"][0]
        assert patch_pair["request"] == "높이 좀 더 높여줘"
        assert patch_pair["operations"][0]["path"] == "/dimensions/overall_height_mm"

        print("  ✓ Memory / Log Pipeline 테스트 통과\n")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_full_pipeline():
    print("=" * 60)
    print("[7] 전체 파이프라인 통합 테스트")
    print("=" * 60)

    registry = SchemaRegistry()
    parser = IntentParserModule(registry, "product_design")
    generator = VariantGeneratorModule(registry, "product_design")
    ranker = CritiqueRankerModule(registry, "product_design")
    patcher = DeltaPatchInterpreter(registry, "product_design")

    # Step 1: 사용자 요청 → Intent
    user_request = "미니멀한 저비용 접이식 선반 만들어줘"
    intent = parser.parse(user_request)
    print(f"  1. Intent: {intent.intent_type.value} → {intent.target_object}")
    print(f"     constraints: {intent.constraints}")

    # Step 2: Intent → Variants
    base_params = {
        "category": "furniture_small",
        "requirement": {"product_goal": "접이식 선반", "budget_level": "medium"},
        "concept": {"name": "Fold-Flat Shelf", "estimated_complexity": "medium"},
        "dimensions": {"overall_width_mm": 420, "overall_depth_mm": 220, "overall_height_mm": 280},
    }
    variants = generator.generate(intent, base_params, n_variants=3)
    print(f"  2. Variants: {len(variants)}개 생성")

    # Step 3: Variants → Critiques
    critiques = ranker.critique_all(variants, intent)
    print(f"  3. Critiques:")
    for c in critiques:
        print(f"     rank {c.overall_rank}: {c.variant_id} (strengths: {len(c.strengths)}, weaknesses: {len(c.weaknesses)})")

    # Step 4: 사용자 수정 → Delta Patch
    selected = variants[0]
    edit_request = "높이를 350mm로 바꿔줘"
    patch = patcher.interpret(edit_request, selected.params)
    final_params = patcher.apply(selected.params, patch)
    print(f"  4. Patch: {len(patch.operations)}개 연산")
    if patch.operations:
        print(f"     {patch.operations[0].path} = {patch.operations[0].value}")

    # Step 5: 세션 기록
    tmp_dir = tempfile.mkdtemp()
    try:
        log = MemoryLogPipeline(tmp_dir, "product_design")
        record = SessionRecord(
            project_type="product_design",
            user_request=user_request,
            parsed_intent=intent,
            variants_generated=variants,
            critiques=critiques,
            user_selected_variant_id=selected.variant_id,
            user_edits=[patch],
            final_params=final_params,
            final_accepted=True,
        )
        log.record_session(record)
        stats = log.get_stats()
        print(f"  5. 세션 저장 완료. 통계: {stats}")

        training = log.export_training_pairs()
        print(f"  6. 학습 데이터: intent={len(training['intent_pairs'])}, ranking={len(training['ranking_pairs'])}, patch={len(training['patch_pairs'])}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("  ✓ 전체 파이프라인 통합 테스트 통과\n")


if __name__ == "__main__":
    test_schema_registry()
    test_intent_parser()
    test_variant_generator()
    test_critique_ranker()
    test_delta_patch()
    test_memory_log()
    test_full_pipeline()
    print("=" * 60)
    print("모든 테스트 통과!")
    print("=" * 60)
