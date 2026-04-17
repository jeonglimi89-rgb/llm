"""
domain/registry.py - 도메인/태스크 등록 테이블

각 태스크의 메타정보: 프롬프트 경로, 스키마 경로, pool 유형, timeout 클래스.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TaskSpec:
    task_type: str              # "builder.requirement_parse"
    domain: str                 # "builder"
    task_name: str              # "requirement_parse"
    pool_type: str              # "strict_json"
    prompt_file: str            # "builder/extract_slots.md"
    schema_file: str            # "builder/slots.schema.json"
    timeout_class: str = "strict_json"
    is_heavy: bool = False
    enabled: bool = True
    is_creative: bool = False           # True = high-creativity task
    creativity_tier: str = "strict"     # "strict"|"guided"|"expressive"


# 운영 A등급 태스크
TASK_REGISTRY: dict[str, TaskSpec] = {
    # Builder
    "builder.requirement_parse": TaskSpec("builder.requirement_parse", "builder", "requirement_parse", "strict_json", "builder/extract_slots.md", "builder/slots.schema.json", is_heavy=True),
    "builder.patch_intent_parse": TaskSpec("builder.patch_intent_parse", "builder", "patch_intent_parse", "strict_json", "builder/extract_slots.md", "builder/slots.schema.json"),
    "builder.zone_priority_parse": TaskSpec("builder.zone_priority_parse", "builder", "zone_priority_parse", "strict_json", "builder/extract_slots.md", "builder/slots.schema.json"),
    "builder.exterior_style_parse": TaskSpec("builder.exterior_style_parse", "builder", "exterior_style_parse", "strict_json", "builder/extract_slots.md", "builder/slots.schema.json"),
    "builder.context_query": TaskSpec("builder.context_query", "builder", "context_query", "strict_json", "shared/slot_extraction_rules.md", "common/slot_bundle.schema.json", is_heavy=True),

    # Minecraft — Legacy (하위 호환)
    "minecraft.build_parse": TaskSpec("minecraft.build_parse", "minecraft", "build_parse", "strict_json", "minecraft/extract_slots.md", "minecraft/build_spec.schema.json", is_heavy=True),
    "minecraft.edit_parse": TaskSpec("minecraft.edit_parse", "minecraft", "edit_parse", "strict_json", "minecraft/extract_slots.md", "minecraft/slots.schema.json"),
    "minecraft.style_check": TaskSpec("minecraft.style_check", "minecraft", "style_check", "strict_json", "minecraft/validate.md", "minecraft/validated_slots.schema.json"),
    "minecraft.anchor_resolution": TaskSpec("minecraft.anchor_resolution", "minecraft", "anchor_resolution", "strict_json", "minecraft/extract_slots.md", "minecraft/slots.schema.json"),

    # Minecraft — Associative brainstorm (webapp: POST /tasks/submit {domain:minecraft, task_name:brainstorm})
    "minecraft.brainstorm": TaskSpec("minecraft.brainstorm", "minecraft", "brainstorm", "creative_json", "minecraft/brainstorm.md", "minecraft/brainstorm.schema.json", timeout_class="creative_json", is_heavy=False, is_creative=True, creativity_tier="expressive"),
    "minecraft.scene_graph": TaskSpec("minecraft.scene_graph", "minecraft", "scene_graph", "creative_json", "minecraft/scene_graph.md", "minecraft/scene_graph.schema.json", timeout_class="creative_json", is_heavy=True, is_creative=True, creativity_tier="expressive"),

    # Minecraft — LLM Active Orchestration
    "minecraft.build_planner": TaskSpec("minecraft.build_planner", "minecraft", "build_planner", "creative_json", "minecraft/build_planner.md", "minecraft/build_plan.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "minecraft.palette_only": TaskSpec("minecraft.palette_only", "minecraft", "palette_only", "creative_json", "minecraft/palette_only.md", "minecraft/build_plan.schema.json", is_heavy=False, is_creative=True, creativity_tier="guided"),
    "minecraft.variant_planner": TaskSpec("minecraft.variant_planner", "minecraft", "variant_planner", "creative_json", "minecraft/variant_planner.md", "minecraft/variants.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "minecraft.build_critic": TaskSpec("minecraft.build_critic", "minecraft", "build_critic", "creative_json", "minecraft/build_critic.md", "minecraft/critique.schema.json", is_heavy=True, is_creative=True, creativity_tier="guided"),
    "minecraft.repair_planner": TaskSpec("minecraft.repair_planner", "minecraft", "repair_planner", "creative_json", "minecraft/repair_planner.md", "minecraft/repair.schema.json", is_creative=True, creativity_tier="guided"),

    # NPC — LLM Active
    "npc.npc_planner": TaskSpec("npc.npc_planner", "npc", "npc_planner", "creative_json", "npc/npc_planner.md", "npc/character.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "npc.npc_critic": TaskSpec("npc.npc_critic", "npc", "npc_critic", "creative_json", "npc/npc_critic.md", "npc/character.schema.json", is_creative=True, creativity_tier="guided"),

    # RP — LLM Active
    "resourcepack.rp_planner": TaskSpec("resourcepack.rp_planner", "resourcepack", "rp_planner", "creative_json", "resourcepack/rp_planner.md", "resourcepack/style.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "resourcepack.rp_critic": TaskSpec("resourcepack.rp_critic", "resourcepack", "rp_critic", "creative_json", "resourcepack/rp_critic.md", "resourcepack/style.schema.json", is_creative=True, creativity_tier="guided"),

    # Animation
    "animation.shot_parse": TaskSpec("animation.shot_parse", "animation", "shot_parse", "strict_json", "animation/extract_slots.md", "animation/slots.schema.json", is_heavy=True),
    "animation.camera_intent_parse": TaskSpec("animation.camera_intent_parse", "animation", "camera_intent_parse", "strict_json", "animation/extract_slots.md", "animation/slots.schema.json"),
    "animation.lighting_intent_parse": TaskSpec("animation.lighting_intent_parse", "animation", "lighting_intent_parse", "strict_json", "animation/extract_slots.md", "animation/slots.schema.json"),
    "animation.edit_patch_parse": TaskSpec("animation.edit_patch_parse", "animation", "edit_patch_parse", "strict_json", "animation/extract_slots.md", "animation/slots.schema.json"),
    "animation.creative_direction": TaskSpec("animation.creative_direction", "animation", "creative_direction", "creative_json", "animation/creative_shot_direct.md", "animation/creative_direction.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "animation.context_query": TaskSpec("animation.context_query", "animation", "context_query", "strict_json", "shared/slot_extraction_rules.md", "common/slot_bundle.schema.json", is_heavy=True),

    # Resource Pack
    "resourcepack.style_parse": TaskSpec("resourcepack.style_parse", "resourcepack", "style_parse", "creative_json", "resourcepack/generate_style.md", "resourcepack/style.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),

    # NPC
    "npc.character_parse": TaskSpec("npc.character_parse", "npc", "character_parse", "creative_json", "npc/generate_character.md", "npc/character.schema.json", is_heavy=True, is_creative=True, creativity_tier="expressive"),
    "npc.dialogue_generate": TaskSpec("npc.dialogue_generate", "npc", "dialogue_generate", "creative_json", "npc/generate_character.md", "npc/character.schema.json", is_creative=True, creativity_tier="expressive"),

    # CAD
    "cad.constraint_parse": TaskSpec("cad.constraint_parse", "cad", "constraint_parse", "strict_json", "cad/extract_slots.md", "cad/slots.schema.json", is_heavy=True),
    "cad.patch_parse": TaskSpec("cad.patch_parse", "cad", "patch_parse", "strict_json", "cad/extract_slots.md", "cad/slots.schema.json"),
    "cad.system_split_parse": TaskSpec("cad.system_split_parse", "cad", "system_split_parse", "strict_json", "cad/extract_slots.md", "cad/slots.schema.json"),
    "cad.priority_parse": TaskSpec("cad.priority_parse", "cad", "priority_parse", "strict_json", "cad/extract_slots.md", "cad/slots.schema.json"),
    "cad.context_query": TaskSpec("cad.context_query", "cad", "context_query", "strict_json", "shared/slot_extraction_rules.md", "common/slot_bundle.schema.json", is_heavy=True),

    # Product Design
    "product_design.requirement_parse": TaskSpec("product_design.requirement_parse", "product_design", "requirement_parse", "strict_json", "product_design/extract_slots.md", "product_design/slots.schema.json", is_heavy=True),
    "product_design.concept_parse": TaskSpec("product_design.concept_parse", "product_design", "concept_parse", "strict_json", "product_design/extract_slots.md", "product_design/slots.schema.json", is_heavy=True),
    "product_design.bom_parse": TaskSpec("product_design.bom_parse", "product_design", "bom_parse", "strict_json", "product_design/extract_slots.md", "product_design/slots.schema.json"),
    "product_design.patch_parse": TaskSpec("product_design.patch_parse", "product_design", "patch_parse", "strict_json", "product_design/extract_slots.md", "product_design/slots.schema.json"),
    "product_design.context_query": TaskSpec("product_design.context_query", "product_design", "context_query", "strict_json", "shared/slot_extraction_rules.md", "common/slot_bundle.schema.json", is_heavy=True),
}


def get_task_spec(task_type: str) -> Optional[TaskSpec]:
    return TASK_REGISTRY.get(task_type)


def list_enabled_tasks() -> list[str]:
    return [t for t, s in TASK_REGISTRY.items() if s.enabled]
