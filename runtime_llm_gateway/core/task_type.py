"""
core/task_type.py - 4개 프로그램 태스크 카탈로그

분류:
  [운영] CPU에서 A등급 검증 완료, 즉시 사용 가능
  [freeform] 스키마 불필요, 자유 텍스트 응답
  [7B보관] 복합 스키마, GPU 7B 이상 전용 (CPU에서 D등급)
"""

# ===================================================================
# 태스크 → 모델 풀 매핑
# ===================================================================

TASK_POOL_MAP: dict[str, str] = {
    # --- Builder AI ---
    "builder.requirement_parse":    "strict-json-pool",     # [운영] A
    "builder.patch_intent_parse":   "strict-json-pool",     # [운영] A (분해)
    "builder.zone_priority_parse":  "strict-json-pool",     # [운영] A
    "builder.exterior_style_parse": "strict-json-pool",     # [운영] A
    "builder.context_query":        "strict-json-pool",     # [운영] A (재설계)
    "builder.scene_chat":           "strict-json-pool",     # [운영] 3D 씬 채팅
    "builder.floor_plan_generate":  "strict-json-pool",     # [운영] LLM 직접 평면도 좌표 생성
    "builder.smalltalk_assist":     "fast-chat-pool",       # [freeform]
    "builder.project_context":      "long-context-pool",    # [7B보관] → context_query 대체
    "builder.patch_parse":          "strict-json-pool",     # [7B보관] → patch_intent_parse 대체

    # --- Minecraft AI ---
    "minecraft.edit_parse":         "strict-json-pool",     # [운영] A
    "minecraft.style_check":        "strict-json-pool",     # [운영] A (분해)
    "minecraft.anchor_resolution":  "strict-json-pool",     # [운영] A
    "minecraft.patch_commentary":   "fast-chat-pool",       # [freeform]
    "minecraft.history_context":    "long-context-pool",    # [freeform] (동작 중)
    "minecraft.style_guard":        "strict-json-pool",     # [7B보관] → style_check 대체

    # --- Animation AI ---
    "animation.shot_parse":              "strict-json-pool",  # [운영] A
    "animation.camera_intent_parse":     "strict-json-pool",  # [운영] A (분해)
    "animation.lighting_intent_parse":   "strict-json-pool",  # [운영] A (분해)
    "animation.edit_patch_parse":        "strict-json-pool",  # [운영] A
    "animation.context_query":           "strict-json-pool",  # [운영] A (재설계)
    "animation.ui_chat":                 "fast-chat-pool",    # [freeform]
    "animation.shot_history":            "long-context-pool",  # [7B보관] → context_query 대체
    "animation.camera_map":              "strict-json-pool",   # [7B보관] → camera_intent_parse 대체
    "animation.lighting_map":            "strict-json-pool",   # [7B보관] → lighting_intent_parse 대체

    # --- CAD AI ---
    "cad.constraint_parse":    "strict-json-pool",     # [운영] A
    "cad.patch_parse":         "strict-json-pool",     # [운영] A
    "cad.system_split_parse":  "strict-json-pool",     # [운영] A (2/3)
    "cad.priority_parse":      "strict-json-pool",     # [운영] A (분해)
    "cad.context_query":       "strict-json-pool",     # [운영] A (재설계)
    "cad.rule_lookup_context": "long-context-pool",    # [7B보관] → context_query 대체

    # --- Embedding (cross-domain) ---
    "builder.rule_search":      "embedding-pool",      # [freeform]
    "minecraft.history_search": "embedding-pool",      # [freeform]
    "animation.shot_search":    "embedding-pool",      # [freeform]
    "cad.part_search":          "embedding-pool",      # [freeform]
}

# ===================================================================
# 태스크 → 스키마 매핑
# ===================================================================

TASK_SCHEMA_MAP: dict[str, str] = {
    # --- 운영 A등급 (CPU 검증 완료) ---
    "builder.requirement_parse":    "builder/requirement_v1",
    "builder.patch_intent_parse":   "builder/patch_intent_v1",
    "builder.zone_priority_parse":  "builder/zone_priority_v1",
    "builder.exterior_style_parse": "builder/exterior_style_v1",
    "builder.context_query":        "common/context_query_v1",
    "builder.scene_chat":           "builder/scene_action_v1",
    "builder.floor_plan_generate":  "builder/floor_plan_v1",

    "minecraft.edit_parse":         "minecraft/edit_patch_v1",
    "minecraft.style_check":        "minecraft/style_check_v1",
    "minecraft.anchor_resolution":  "minecraft/anchor_v1",

    "animation.shot_parse":              "animation/shot_graph_v1",
    "animation.camera_intent_parse":     "animation/camera_intent_v1",
    "animation.lighting_intent_parse":   "animation/lighting_intent_v1",
    "animation.edit_patch_parse":        "animation/edit_intent_v1",
    "animation.context_query":           "common/context_query_v1",

    "cad.constraint_parse":    "cad/constraint_v1",
    "cad.patch_parse":         "cad/patch_v1",
    "cad.system_split_parse":  "cad/system_split_v1",
    "cad.priority_parse":      "cad/priority_v1",
    "cad.context_query":       "common/context_query_v1",

    # --- 7B 전용 보관 (CPU에서 D등급) ---
    "builder.patch_parse":     "builder/patch_v1",
    "minecraft.style_guard":   "minecraft/style_guard_v1",
    "animation.camera_map":    "animation/camera_lighting_v1",
    "animation.lighting_map":  "animation/camera_lighting_v1",
}

# ===================================================================
# 분류 집합
# ===================================================================

PROGRAMS = ["builder", "minecraft", "animation", "cad"]

# CPU에서 A등급 운영 가능한 strict-json 태스크
OPERATIONAL_TASKS = [
    "builder.requirement_parse",
    "builder.patch_intent_parse",
    "builder.zone_priority_parse",
    "builder.exterior_style_parse",
    "builder.context_query",
    "builder.scene_chat",
    "builder.floor_plan_generate",
    "minecraft.edit_parse",
    "minecraft.style_check",
    "minecraft.anchor_resolution",
    "animation.shot_parse",
    "animation.camera_intent_parse",
    "animation.lighting_intent_parse",
    "animation.edit_patch_parse",
    "animation.context_query",
    "cad.constraint_parse",
    "cad.patch_parse",
    "cad.system_split_parse",
    "cad.priority_parse",
    "cad.context_query",
]

# GPU 7B 전용 보관 (CPU에서 비활성)
GPU_ONLY_TASKS = [
    "builder.patch_parse",
    "builder.project_context",
    "minecraft.style_guard",
    "animation.camera_map",
    "animation.lighting_map",
    "animation.shot_history",
    "cad.rule_lookup_context",
]

# 스키마 불필요 (freeform/embedding)
FREEFORM_TASKS = [
    "builder.smalltalk_assist",
    "minecraft.patch_commentary",
    "minecraft.history_context",
    "animation.ui_chat",
    "builder.rule_search",
    "minecraft.history_search",
    "animation.shot_search",
    "cad.part_search",
]
