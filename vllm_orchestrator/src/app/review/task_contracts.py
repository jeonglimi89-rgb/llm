"""
review/task_contracts.py — 태스크 별 출력 계약 (D등급 태스크 강화)

각 태스크가 가지는 명시적 contract:
  - allowed_keys / required_keys / forbidden_keys
  - allow_external_urls / require_korean_in
  - 추가로 검사할 semantic detector 들

evaluate_task_contract(task_type, user_input, payload) 가 다섯 게이트 (schema,
language, semantic, domain_guard, contract) 의 GateResult 를 만들어
review/layered.py 의 compose_judgment 에 넘긴다.

타깃 D등급 태스크
=================
1. builder.requirement_parse / builder.patch_intent_parse — 한국어 키만 허용
2. cad.constraint_parse                                   — validator-shape 거부
3. minecraft.style_check                                  — CSS 어휘 거부
4. animation.camera_intent_parse                          — URL 거부, framing/mood 어휘
5. animation.lighting_intent_parse                        — 한국어 reasoning + anchor 보존
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Iterable

from .judgment import Severity
from .layered import (
    LayeredJudgment, GateResult, FailureCategory, compose_judgment,
)
from .semantic_validators import (
    DetectorResult,
    detect_chinese_keys,
    detect_japanese_in_keys,
    detect_non_korean_in_required_field,
    detect_validator_shape,
    detect_css_property_leak,
    detect_url_hallucination,
    detect_semantic_anchor_loss,
    detect_known_lossy_english,
    detect_empty_or_trivial_payload,
    detect_input_echo,
    _all_keys,
    _all_string_values,
)


# ---------------------------------------------------------------------------
# Allowed-key 화이트리스트 (도메인별)
# ---------------------------------------------------------------------------

# 한국어 도메인 슬롯의 표준 영문 snake_case 키.
# 한자/일본어 키는 detect_chinese_keys / detect_japanese_in_keys 가 잡고,
# 여기서는 추가로 "허용 어휘" 를 정의한다 (None 이면 자유).
BUILDER_ALLOWED_KEYS = {
    # 공통
    "project_type", "floors", "spaces", "preferences", "constraints",
    "style", "style_family", "exterior_style", "context", "context_query",
    # 평면
    "floor", "rooms", "name", "type", "x", "y", "w", "h", "area_m2", "count", "priority",
    "min_area_m2", "preferred_area_m2",
    # patch
    "operation_type", "target", "delta", "preserve", "scope",
    "intent", "reason", "patch", "summary",
    # 메타
    "metadata", "total_rooms", "total_area_m2",
    # Enhanced prompt fields
    "building_use", "site", "lot_area_m2", "lot_orientation", "street_facing",
    "massing", "total_gfa_m2", "building_coverage_ratio", "floor_area_ratio",
    "height_m", "form", "adjacency", "natural_light", "privacy",
    "wet_zones", "stacked_vertically", "kitchen_location", "bathroom_count", "plumbing_strategy",
    "circulation", "entry_sequence", "stair_type", "stair_position", "corridor_strategy",
    "facade", "primary_style", "main_materials", "window_ratio", "balcony_type", "roof_type",
    "interior_character", "ceiling_height_m", "ambiance", "feature_elements",
    "sustainability", "passive_solar", "cross_ventilation", "rainwater_harvesting", "insulation_grade",
    "code_flags", "elevator_required", "accessible_entrance", "fire_separation_walls", "parking_spaces",
    "privacy_bias", "openness_bias", "budget_tier",
    "user_priorities", "narrative", "downstream_effects", "field", "from", "to",
}

CAD_ALLOWED_KEYS = {
    # constraint
    "constraints", "constraint_type", "description", "category", "severity", "details",
    "input_requirements", "output_requirements", "component_name", "quantity", "unit",
    # part
    "parts", "interfaces", "electrical", "drainage", "structural",
    "name", "type", "dimensions", "material",
    # system split
    "systems", "system", "subsystems",
    # Enhanced prompt fields
    "product_category", "overall_dimensions", "width_mm", "depth_mm", "height_mm",
    "weight_g_target", "weight_g", "volume_cm3",
    "tolerance_mm", "materials", "primary", "secondary", "material_name", "color", "finish",
    "manufacturing", "primary_method", "mold_complexity", "expected_volume",
    "surface_finish_sp", "draft_angle_deg",
    "sealing", "ip_rating", "sealing_zones", "location", "method", "max_immersion_depth_m",
    "has_pcb", "pcb_size_mm", "power_source", "battery_capacity_mah", "charge_port",
    "nominal_voltage_v", "nominal_current_ma", "connectors",
    "mechanical_interfaces", "interface_type", "fastener_spec", "thread_depth_mm",
    "thermal", "heat_source_watts", "cooling_strategy", "max_ambient_c", "max_internal_c",
    "ventilation_openings",
    "part_id", "part_name", "role", "mates_with",
    "assembly_sequence", "wiring_routes", "drainage_paths",
    "from", "to", "wire_gauge_awg", "length_mm", "connector", "slope_deg",
    "certifications_required", "user_priorities", "narrative", "preferences",
    "budget_level", "target_cost_krw", "weight_target_g",
    "critical_parts", "dependencies", "rationale",
    "rank", "affected_parts", "field", "old", "new",
    "width", "depth", "height",
    # priority
    "priority", "priorities",
    # 메타
    "part_count", "system_count", "waterproof",
    "summary", "patch",
    # template echo tolerance (enrichment metadata)
    "display_name", "category", "typical_footprint", "keywords",
    "silhouette_rules", "structural_motifs", "default_operations",
}

MINECRAFT_ALLOWED_KEYS = {
    # edit
    "target_anchor", "anchor_type", "anchor_id", "anchor", "operations", "preserve", "scope",
    "type", "delta", "material", "count", "verdict", "reason",
    # style check 도메인 어휘
    "theme", "style", "blocks", "block", "block_count", "palette",
    "style_score", "issues", "score",
    # 메타
    "metadata", "x", "y", "z",
    "summary",
    # LLM Active Orchestration — build_planner
    "build_type", "footprint", "width", "depth", "shape",
    "silhouette_strategy", "wall_height", "roof", "peak_height", "overhang",
    "ornament_density", "defense_level", "palette_strategy",
    "material_hints", "key_features", "interior_rooms", "exterior_elements",
    "creative_notes", "tone", "scale", "mood",
    # variant_planner
    "variants", "label", "strategy", "description", "axes_override",
    "footprint_adjust", "roof_override", "extra_features",
    "weight", "symmetry", "window_rhythm", "roof_sharpness", "wall_depth",
    "interior_priority", "verticality", "organic", "facade_emphasis",
    "width_delta", "depth_delta", "peak_height_delta",
    # build_critic
    "overall_quality", "theme_adherence", "weaknesses", "severity",
    "category", "probable_cause", "repair_code", "expected_impact",
    "strengths", "priority_repairs", "creative_suggestion",
    # repair_planner
    "repair_steps", "action", "priority", "estimated_block_count", "parameters",
    "direction", "amount",
    "expected_improvements", "risk_areas", "repair_order_rationale",
    # BuildSpecV1 compatibility
    "version", "kind", "buildingType", "materialHints", "constraints",
    "maxTowers", "avoidOverDecoration", "symmetryBias",
    # LLM이 생성하는 추가 풍부 필드 (창의성 허용)
    "floors", "wall", "window", "door", "interior", "exterior",
    "orientation", "floor_heights", "has_basement", "has_attic",
    "vertical_progression", "count", "features", "rooms", "name",
    "height_per_floor", "thickness", "corner_style", "detail_pattern",
    "ornamentation", "size", "placement", "location",
    "architectural_notes", "unique_features", "visual_anchors",
    "stories", "foundation", "chimney", "entrance",
    "dimensions", "height", "layout", "room_type", "purpose",
    "number", "shape_detail", "opening", "entryway",
    "primary_material", "secondary_material", "accent_material",
    "time_period", "region", "cultural_influence",
    "floor", "floor_count", "roofing", "decoration",
    "layered_detail", "depth_of_wall", "cornerstone",
    "windows", "doors", "balconies", "porches",
    "terrace", "garden_layout", "pathway",
    "adjacency", "connection", "integration",
    "palette_description", "mood_description",
    # template echo tolerance (minecraft template fields)
    "display_name", "category", "typical_footprint", "keywords",
    "silhouette_rules", "structural_motifs", "default_operations",
    # Extended build_planner fields (from enhanced prompt)
    "wall_accent", "trim", "primary_materials", "walls", "floor",
    "landscape_context", "biome_fit", "terrain_adaptation", "surrounding_elements",
    "lighting_scheme", "verticality", "symmetry_bias",
    "narrative_hook", "narrative", "story", "story_hook",
    "window", "wall_thickness", "corner", "pattern",
    "count_per_floor", "position", "porch_type", "steps", "door_style",
    "grid", "rhythm", "approach",
    # variant fields
    "variant_name", "changes", "modifications",
    # critic fields
    "structure_integrity", "aesthetics", "functionality", "innovation",
    "problems", "suggestions", "note", "comments",
    # repair fields
    "step", "step_id", "target", "delta",
    # common extras
    "ratio", "max", "min", "is", "has",
    # number fallbacks
    "value", "quantity", "level",
}

ANIMATION_ALLOWED_KEYS = {
    # shot
    "shot_id", "duration_frames", "framing", "lens_mm", "mood", "lighting",
    "characters", "acting", "camera", "intent", "reasoning",
    "edit", "patch", "preserve",
    "speed", "camera_move",  # LLM이 자주 생성하는 shot_parse 키
    # camera intent
    "movement", "angle", "subject", "focus",
    # lighting intent
    "key_light", "fill_light", "back_light", "color_temperature",
    "atmosphere", "mood_tag", "intensity",
    # 공통
    "metadata", "summary", "emotion_hint",
    # Enhanced prompt fields
    "scene_type", "aspect_ratio", "movement_speed", "height_m", "distance_to_subject_m",
    "focus_type", "focus_point", "primary", "secondary", "composition",
    "key_direction", "key_intensity", "color_temperature_k", "contrast_ratio",
    "practical_sources", "mood_descriptor",
    "color_palette", "dominant_hues", "accent_hue", "saturation", "tonal_range",
    "pacing", "shot_duration_seconds", "fps", "rhythm", "cut_in", "cut_out",
    "emotion", "arc", "character_expression",
    "narrative_context", "story_position", "reveal_type", "tension_level",
    "preceding_shot_hint", "following_shot_hint",
    "sound_suggestion", "diegetic", "score_mood", "score_intensity",
    "reference_style", "key_features", "continuity_anchors", "narrative_intent",
    "movement_motivation", "start_frame_hint", "end_frame_hint",
    "fill_ratio", "source_motivation", "color_grading_intent",
    "field", "delta", "reason",
}

DOMAIN_ALLOWED_KEYS = {
    "builder": BUILDER_ALLOWED_KEYS,
    "cad": CAD_ALLOWED_KEYS,
    "minecraft": MINECRAFT_ALLOWED_KEYS,
    "animation": ANIMATION_ALLOWED_KEYS,
}


# 태스크별 미세 조정용 추가 허용 키 (None=상속)
TASK_EXTRA_ALLOWED: dict[str, set[str]] = {
    # cad.constraint_parse 는 validator-shape 와 충돌하므로 valid/message/error 를
    # 명시적으로 *추가하지 않는다*. 그렇게 하면 detect_validator_shape 가 잡는다.
}


# ---------------------------------------------------------------------------
# Contract dataclass
# ---------------------------------------------------------------------------

@dataclass
class TaskContract:
    """태스크 출력 계약"""
    task_type: str
    domain: str

    # 키 정책
    allowed_keys: Optional[set[str]] = None     # None = 도메인 default
    required_keys: set[str] = field(default_factory=set)
    forbidden_keys: set[str] = field(default_factory=set)

    # 언어 정책
    require_korean_in: set[str] = field(default_factory=set)   # 필드 이름 (값에 한국어 필수)
    forbid_chinese_keys: bool = True
    forbid_japanese_keys: bool = True

    # 도메인 가드 정책
    forbid_validator_shape: bool = True
    forbid_css_vocab: bool = False
    allow_external_urls: bool = False

    # 의미 정책
    semantic_anchors: Optional[dict[str, list[str]]] = None
    forbid_input_echo: bool = True
    forbid_known_lossy_english: bool = True

    # 빈/trivial payload 거부
    forbid_empty: bool = True


# ---------------------------------------------------------------------------
# Contract 정의 (5개 D등급 태스크 + fallback)
# ---------------------------------------------------------------------------

def _build_default_contract(task_type: str, domain: str) -> TaskContract:
    """도메인 기본 contract"""
    return TaskContract(
        task_type=task_type,
        domain=domain,
        allowed_keys=DOMAIN_ALLOWED_KEYS.get(domain),
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,
        forbid_css_vocab=(domain == "minecraft"),  # minecraft 만 기본 ON
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    )


# 5개 D등급 task contract — 명시적으로 강화
TASK_CONTRACTS: dict[str, TaskContract] = {

    # 1) builder.patch_intent_parse — 한국어 키 강제
    "builder.patch_intent_parse": TaskContract(
        task_type="builder.patch_intent_parse",
        domain="builder",
        allowed_keys=BUILDER_ALLOWED_KEYS | {"intent", "operation_type", "target", "delta"},
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    ),

    "builder.requirement_parse": TaskContract(
        task_type="builder.requirement_parse",
        domain="builder",
        allowed_keys=BUILDER_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    ),

    # 2) cad.constraint_parse — validator-shape 거부 우선
    "cad.constraint_parse": TaskContract(
        task_type="cad.constraint_parse",
        domain="cad",
        allowed_keys=CAD_ALLOWED_KEYS,
        required_keys={"constraints"} if False else set(),  # 너무 강하면 정상 출력도 fail → 끔
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,    # ★ HR-004 회귀 방어
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    ),

    # 3) minecraft.style_check — CSS 어휘 거부
    "minecraft.style_check": TaskContract(
        task_type="minecraft.style_check",
        domain="minecraft",
        allowed_keys=MINECRAFT_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=False,   # style_check 는 verdict 형태가 정상
        forbid_css_vocab=True,          # ★ HR-008 회귀 방어
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    ),

    # 4) animation.camera_intent_parse — URL 환각 거부
    "animation.camera_intent_parse": TaskContract(
        task_type="animation.camera_intent_parse",
        domain="animation",
        allowed_keys=ANIMATION_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,
        forbid_css_vocab=False,
        allow_external_urls=False,      # ★ HR-011 회귀 방어
        forbid_input_echo=True,
        forbid_known_lossy_english=True,
        forbid_empty=True,
    ),

    # 5) animation.lighting_intent_parse — 한국어 + anchor 보존
    "animation.lighting_intent_parse": TaskContract(
        task_type="animation.lighting_intent_parse",
        domain="animation",
        allowed_keys=ANIMATION_ALLOWED_KEYS,
        require_korean_in={"reasoning", "intent", "atmosphere", "mood_tag"},  # ★ HR-012
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=True,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=True,
        forbid_known_lossy_english=True,    # ★ HR-012 "outside night"
        forbid_empty=True,
    ),

    # ── LLM Active Orchestration — Creative tasks with relaxed contracts ──
    "minecraft.build_planner": TaskContract(
        task_type="minecraft.build_planner",
        domain="minecraft",
        allowed_keys=MINECRAFT_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=False,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=False,
        forbid_known_lossy_english=False,
        forbid_empty=True,
    ),
    "minecraft.variant_planner": TaskContract(
        task_type="minecraft.variant_planner",
        domain="minecraft",
        allowed_keys=MINECRAFT_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=False,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=False,
        forbid_known_lossy_english=False,
        forbid_empty=True,
    ),
    "minecraft.build_critic": TaskContract(
        task_type="minecraft.build_critic",
        domain="minecraft",
        allowed_keys=MINECRAFT_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=False,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=False,
        forbid_known_lossy_english=False,
        forbid_empty=True,
    ),
    "minecraft.repair_planner": TaskContract(
        task_type="minecraft.repair_planner",
        domain="minecraft",
        allowed_keys=MINECRAFT_ALLOWED_KEYS,
        forbid_chinese_keys=True,
        forbid_japanese_keys=True,
        forbid_validator_shape=False,
        forbid_css_vocab=False,
        allow_external_urls=False,
        forbid_input_echo=False,
        forbid_known_lossy_english=False,
        forbid_empty=True,
    ),
}


def get_task_contract(task_type: str) -> TaskContract:
    """등록된 contract 가 없으면 도메인 기본을 합성해서 반환."""
    if task_type in TASK_CONTRACTS:
        return TASK_CONTRACTS[task_type]
    domain = task_type.split(".", 1)[0] if "." in task_type else task_type
    return _build_default_contract(task_type, domain)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _key_whitelist_check(payload: Any, allowed: set[str]) -> DetectorResult:
    """top-level 과 1단계 child 객체 키들이 allowed 에 있는지 검사.

    완전히 자유 (리스트 안 등) 는 건너뛰지만 "객체의 키 자체" 만큼은 확인.
    """
    if not isinstance(payload, (dict, list)):
        return DetectorResult(passed=True)

    bad: list[dict[str, Any]] = []
    for path, key in _all_keys(payload):
        if not isinstance(key, str):
            continue
        # 한국어 키도 허용하지 않음 (영문 snake_case 가 표준)
        # 단, allowed 에 들어 있거나 __metadata__ 류 underscore prefix 는 통과
        if key in allowed:
            continue
        if key.startswith("_"):
            continue
        bad.append({"path": path, "key": key})

    if bad:
        # 한자/가나는 별도 detector 가 우선 잡으므로 여기는 wrong_locale 이 아닌
        # task_contract_violation 으로 분류
        return DetectorResult(
            passed=False,
            severity=Severity.HIGH.value,
            failure_category=FailureCategory.TASK_CONTRACT_VIOLATION.value,
            rationale=f"화이트리스트 외 키 {len(bad)}개",
            evidence=bad[:20],
        )
    return DetectorResult(passed=True)


def _required_keys_check(payload: Any, required: set[str]) -> DetectorResult:
    if not required:
        return DetectorResult(passed=True)
    if not isinstance(payload, dict):
        return DetectorResult(
            passed=False,
            severity=Severity.HIGH.value,
            failure_category=FailureCategory.TASK_CONTRACT_VIOLATION.value,
            rationale="required_keys 검사하려는데 top-level 이 dict 가 아님",
            evidence=[{"reason": "non_dict_payload"}],
        )
    missing = sorted(required - set(payload.keys()))
    if missing:
        return DetectorResult(
            passed=False,
            severity=Severity.HIGH.value,
            failure_category=FailureCategory.TASK_CONTRACT_VIOLATION.value,
            rationale=f"필수 키 누락: {missing}",
            evidence=[{"missing": missing}],
        )
    return DetectorResult(passed=True)


def _forbidden_keys_check(payload: Any, forbidden: set[str]) -> DetectorResult:
    if not forbidden:
        return DetectorResult(passed=True)
    bad: list[dict[str, Any]] = []
    for path, key in _all_keys(payload):
        if isinstance(key, str) and key in forbidden:
            bad.append({"path": path, "key": key})
    if bad:
        return DetectorResult(
            passed=False,
            severity=Severity.HIGH.value,
            failure_category=FailureCategory.TASK_CONTRACT_VIOLATION.value,
            rationale=f"금지 키 {len(bad)}개",
            evidence=bad[:20],
        )
    return DetectorResult(passed=True)


def _merge_detector_into_gate(gate: GateResult, det: DetectorResult) -> None:
    """detector 결과를 gate 에 누적. 더 높은 severity 가 이김."""
    if not det.passed:
        gate.passed = False
        # severity 누적
        from .layered import _SEVERITY_RANK
        if _SEVERITY_RANK.get(det.severity, 0) > _SEVERITY_RANK.get(gate.severity, 0):
            gate.severity = det.severity
        if det.failure_category and det.failure_category != FailureCategory.NONE.value:
            if det.failure_category not in gate.failure_categories:
                gate.failure_categories.append(det.failure_category)
        if det.rationale:
            sep = " | " if gate.rationale else ""
            gate.rationale = f"{gate.rationale}{sep}{det.rationale}"
        gate.evidence.extend(det.evidence)


def evaluate_task_contract(
    task_type: str,
    user_input: str,
    payload: Any,
    *,
    schema_validated: bool = True,
    artifact_id: str = "",
) -> LayeredJudgment:
    """단일 태스크 출력에 대해 5개 게이트를 평가하고 LayeredJudgment 반환.

    Parameters
    ----------
    task_type : "builder.requirement_parse" 같은 풀 task_type
    user_input : LLM 에 들어간 사용자 입력
    payload : LLM 출력 JSON 을 파싱한 결과 (None 도 허용)
    schema_validated : 외부에서 이미 schema 검증을 했으면 결과 전달.
                       False 면 schema gate 가 hard fail.
    """
    contract = get_task_contract(task_type)
    domain = contract.domain
    aid = artifact_id or task_type

    # ---- gate 1: schema -------------------------------------------------
    schema_gate = GateResult(name="schema", passed=schema_validated)
    if not schema_validated:
        schema_gate.severity = Severity.CRITICAL.value
        schema_gate.failure_categories.append(FailureCategory.SCHEMA_FAILURE.value)
        schema_gate.rationale = "schema validation upstream said False"

    # 빈 payload 도 schema 단계에서 본다 (공용)
    if contract.forbid_empty:
        empty_det = detect_empty_or_trivial_payload(payload)
        if not empty_det.passed:
            # empty 는 schema 게이트 쪽에 같이 묶음
            _merge_detector_into_gate(schema_gate, empty_det)

    # ---- gate 2: language -----------------------------------------------
    language_gate = GateResult(name="language", passed=True)
    if contract.forbid_chinese_keys:
        _merge_detector_into_gate(language_gate, detect_chinese_keys(payload))
    if contract.forbid_japanese_keys:
        _merge_detector_into_gate(language_gate, detect_japanese_in_keys(payload))
    if contract.require_korean_in:
        _merge_detector_into_gate(
            language_gate,
            detect_non_korean_in_required_field(payload, contract.require_korean_in),
        )

    # ---- gate 3: semantic ------------------------------------------------
    semantic_gate = GateResult(name="semantic", passed=True)
    _merge_detector_into_gate(
        semantic_gate,
        detect_semantic_anchor_loss(user_input, payload, anchors=contract.semantic_anchors),
    )
    if contract.forbid_known_lossy_english:
        _merge_detector_into_gate(
            semantic_gate, detect_known_lossy_english(user_input, payload)
        )
    if contract.forbid_input_echo:
        _merge_detector_into_gate(
            semantic_gate, detect_input_echo(user_input, payload)
        )

    # ---- gate 4: domain_guard -------------------------------------------
    domain_gate = GateResult(name="domain_guard", passed=True)
    if contract.forbid_validator_shape:
        _merge_detector_into_gate(domain_gate, detect_validator_shape(payload))
    if contract.forbid_css_vocab:
        _merge_detector_into_gate(domain_gate, detect_css_property_leak(payload))
    _merge_detector_into_gate(
        domain_gate,
        detect_url_hallucination(payload, allow_urls=contract.allow_external_urls),
    )

    # ---- gate 5: contract -----------------------------------------------
    contract_gate = GateResult(name="contract", passed=True)
    # Creative LLM-active tasks: 화이트리스트 우회 (창의적 키 허용)
    _CREATIVE_TASKS = {
        "minecraft.build_planner", "minecraft.variant_planner",
        "minecraft.build_critic", "minecraft.repair_planner",
        "minecraft.brainstorm", "minecraft.scene_graph",
        "minecraft.palette_only",
        "npc.npc_planner", "npc.npc_critic",
        "resourcepack.rp_planner", "resourcepack.rp_critic",
    }
    is_creative = task_type in _CREATIVE_TASKS

    if contract.allowed_keys is not None and not is_creative:
        _merge_detector_into_gate(contract_gate, _key_whitelist_check(payload, contract.allowed_keys))
    if contract.required_keys:
        _merge_detector_into_gate(contract_gate, _required_keys_check(payload, contract.required_keys))
    if contract.forbidden_keys:
        _merge_detector_into_gate(contract_gate, _forbidden_keys_check(payload, contract.forbidden_keys))

    gates = [schema_gate, language_gate, semantic_gate, domain_gate, contract_gate]

    return compose_judgment(
        artifact_id=aid,
        domain=domain,
        task_type=task_type,
        gates=gates,
        parsed_payload=payload,
        user_input=user_input,
        recommended_action=_recommended_action_for(gates),
    )


def _recommended_action_for(gates: list[GateResult]) -> str:
    """gate 결과 → 짧은 권장 조치 문자열."""
    failed = [g for g in gates if not g.passed]
    if not failed:
        return "no action — all gates passed"

    msgs: list[str] = []
    for g in failed:
        cats = g.failure_categories
        if FailureCategory.WRONG_KEY_LOCALE.value in cats:
            msgs.append("프롬프트에 영문 snake_case 키 명시 + 한자 키 거부 예제 추가")
        elif FailureCategory.WRONG_LANGUAGE.value in cats:
            msgs.append("reasoning/intent 필드는 한국어로 출력하도록 프롬프트 강화")
        elif FailureCategory.VALIDATOR_SHAPED_RESPONSE.value in cats:
            msgs.append("constraint 출력 schema 를 validator 와 분리, valid/message 키 금지")
        elif FailureCategory.CSS_PROPERTY_LEAK.value in cats:
            msgs.append("Minecraft 도메인 어휘만 허용, font_family/padding 등 web 어휘 금지")
        elif FailureCategory.HALLUCINATED_EXTERNAL_REFERENCE.value in cats:
            msgs.append("URL/도메인 출력 금지, http/www/example.com 거부 명시")
        elif FailureCategory.SEMANTIC_MISTRANSLATION.value in cats:
            msgs.append("입력 한국어 anchor 토큰 보존 강제, 영어 paraphrase 금지")
        elif FailureCategory.SCHEMA_PASS_BUT_SEMANTIC_FAIL.value in cats:
            msgs.append("입력 echo 금지, 의미 슬롯 추출 강제")
        elif FailureCategory.TASK_CONTRACT_VIOLATION.value in cats:
            msgs.append("출력 키를 화이트리스트 안으로 제한")
        else:
            msgs.append(f"{g.name} 게이트 실패: {g.rationale}")
    # dedup
    seen = set()
    out: list[str] = []
    for m in msgs:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return " / ".join(out)
