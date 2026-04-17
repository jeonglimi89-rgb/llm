"""
domain/capability_contract.py — Capability Contract Registry.

Each capability is a fine-grained unit of work that an app can perform.
Capabilities are bound to a specific app domain and intervention scope.

A capability contract includes:
- What the capability does
- What inputs/outputs it expects
- What locks it must respect
- Whether creativity is allowed
- What verifier hooks apply
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CapabilityContract:
    """Contract for a single app capability."""
    capability_id: str                  # "minecraft.build_plan_generate"
    app_domain: str                     # "minecraft"
    allowed_task_family: str            # "build"
    intervention_scope: str             # "build" | "npc" | "exterior_drawing" etc.
    creativity_allowed: bool = True
    creativity_tier: str = "strict"     # "strict" | "guided" | "expressive"
    hard_locks: list[str] = field(default_factory=list)
    verifier_hooks: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CapabilityContract:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Capability Contract Registry ──

CAPABILITY_REGISTRY: dict[str, CapabilityContract] = {
    # ── Minecraft AI ──
    "minecraft.build_plan_generate": CapabilityContract(
        capability_id="minecraft.build_plan_generate",
        app_domain="minecraft",
        allowed_task_family="build",
        intervention_scope="build",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["theme_coherence", "structure_coherence"],
        verifier_hooks=["boundary_check", "style_coherence_check"],
        preconditions=["user_input_parsed"],
        postconditions=["target_anchor_set", "operations_non_empty"],
        description="Generate a Minecraft build plan from user description",
    ),
    "minecraft.build_style_validate": CapabilityContract(
        capability_id="minecraft.build_style_validate",
        app_domain="minecraft",
        allowed_task_family="build",
        intervention_scope="build",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["theme_coherence", "structure_coherence"],
        verifier_hooks=["palette_check", "proportion_check"],
        description="Validate style coherence of a build plan",
    ),
    "minecraft.npc_concept_generate": CapabilityContract(
        capability_id="minecraft.npc_concept_generate",
        app_domain="minecraft",
        allowed_task_family="npc",
        intervention_scope="npc",
        creativity_allowed=True,
        creativity_tier="expressive",
        hard_locks=["role_coherence", "world_theme_coherence"],
        verifier_hooks=["npc_role_check", "world_consistency_check"],
        description="Generate NPC concept with personality and role",
    ),
    "minecraft.npc_role_validate": CapabilityContract(
        capability_id="minecraft.npc_role_validate",
        app_domain="minecraft",
        allowed_task_family="npc",
        intervention_scope="npc",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["role_coherence"],
        verifier_hooks=["role_consistency_check"],
        description="Validate NPC role consistency",
    ),
    "minecraft.resourcepack_style_plan": CapabilityContract(
        capability_id="minecraft.resourcepack_style_plan",
        app_domain="minecraft",
        allowed_task_family="resourcepack",
        intervention_scope="resourcepack",
        creativity_allowed=True,
        creativity_tier="expressive",
        hard_locks=["pack_consistency", "material_coherence"],
        verifier_hooks=["palette_consistency_check"],
        description="Plan resource pack style and palette",
    ),
    "minecraft.resourcepack_consistency_check": CapabilityContract(
        capability_id="minecraft.resourcepack_consistency_check",
        app_domain="minecraft",
        allowed_task_family="resourcepack",
        intervention_scope="resourcepack",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["pack_consistency"],
        verifier_hooks=["material_unity_check"],
        description="Check resource pack internal consistency",
    ),

    # ── Builder AI ──
    "builder.exterior_drawing_generate": CapabilityContract(
        capability_id="builder.exterior_drawing_generate",
        app_domain="builder",
        allowed_task_family="exterior_drawing",
        intervention_scope="exterior_drawing",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["zoning_compliance", "circulation_sanity", "facade_consistency"],
        verifier_hooks=["code_compliance_check", "massing_check"],
        description="Generate building exterior drawing / facade plan",
    ),
    "builder.interior_drawing_generate": CapabilityContract(
        capability_id="builder.interior_drawing_generate",
        app_domain="builder",
        allowed_task_family="interior_drawing",
        intervention_scope="interior_drawing",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["zoning_compliance", "circulation_sanity", "wet_zone_logic"],
        verifier_hooks=["adjacency_check", "privacy_zoning_check"],
        description="Generate building interior drawing / floor plan",
    ),
    "builder.exterior_interior_consistency_check": CapabilityContract(
        capability_id="builder.exterior_interior_consistency_check",
        app_domain="builder",
        allowed_task_family="exterior_drawing",
        intervention_scope="exterior_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["facade_consistency"],
        description="Check exterior/interior plan consistency",
    ),
    "builder.plan_adjacency_check": CapabilityContract(
        capability_id="builder.plan_adjacency_check",
        app_domain="builder",
        allowed_task_family="interior_drawing",
        intervention_scope="interior_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["circulation_sanity", "wet_zone_logic"],
        description="Check interior room adjacency logic",
    ),
    "builder.facade_massing_variant_plan": CapabilityContract(
        capability_id="builder.facade_massing_variant_plan",
        app_domain="builder",
        allowed_task_family="exterior_drawing",
        intervention_scope="exterior_drawing",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["zoning_compliance"],
        description="Plan facade massing variants",
    ),

    # ── Animation AI ──
    "animation.camera_walk_plan_generate": CapabilityContract(
        capability_id="animation.camera_walk_plan_generate",
        app_domain="animation",
        allowed_task_family="camera_walking",
        intervention_scope="camera_walking",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["camera_continuity", "180_degree_rule"],
        verifier_hooks=["continuity_check", "readability_check"],
        description="Generate camera walking plan for a scene",
    ),
    "animation.camera_continuity_check": CapabilityContract(
        capability_id="animation.camera_continuity_check",
        app_domain="animation",
        allowed_task_family="camera_walking",
        intervention_scope="camera_walking",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["camera_continuity", "180_degree_rule"],
        description="Check camera continuity across shots",
    ),
    "animation.style_lock_check": CapabilityContract(
        capability_id="animation.style_lock_check",
        app_domain="animation",
        allowed_task_family="style_lock",
        intervention_scope="style_lock",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["style_lock", "character_identity_continuity"],
        verifier_hooks=["identity_drift_check"],
        description="Check style lock compliance",
    ),
    "animation.style_feedback_generate": CapabilityContract(
        capability_id="animation.style_feedback_generate",
        app_domain="animation",
        allowed_task_family="style_feedback",
        intervention_scope="style_feedback",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["style_lock", "character_identity_continuity"],
        verifier_hooks=["feedback_actionability_check"],
        description="Generate actionable style feedback",
    ),
    "animation.identity_drift_check": CapabilityContract(
        capability_id="animation.identity_drift_check",
        app_domain="animation",
        allowed_task_family="style_lock",
        intervention_scope="style_lock",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["character_identity_continuity"],
        description="Check for character identity drift",
    ),

    # ── CAD AI ──
    "cad.design_brief_parse": CapabilityContract(
        capability_id="cad.design_brief_parse",
        app_domain="cad",
        allowed_task_family="design_drawing",
        intervention_scope="design_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["dimensional_plausibility"],
        description="Parse design brief into constraints",
    ),
    "cad.design_drawing_generate": CapabilityContract(
        capability_id="cad.design_drawing_generate",
        app_domain="cad",
        allowed_task_family="design_drawing",
        intervention_scope="design_drawing",
        creativity_allowed=True,
        creativity_tier="guided",
        hard_locks=["dimensional_plausibility", "manufacturability", "assembly_logic", "routing_feasibility"],
        verifier_hooks=["dimension_check", "assembly_check", "routing_check"],
        description="Generate engineering design drawing",
    ),
    "cad.assembly_feasibility_check": CapabilityContract(
        capability_id="cad.assembly_feasibility_check",
        app_domain="cad",
        allowed_task_family="design_drawing",
        intervention_scope="design_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["assembly_logic"],
        description="Check assembly feasibility",
    ),
    "cad.routing_precheck": CapabilityContract(
        capability_id="cad.routing_precheck",
        app_domain="cad",
        allowed_task_family="design_drawing",
        intervention_scope="design_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["routing_feasibility"],
        description="Pre-check wiring/drainage routing",
    ),
    "cad.manufacturability_check": CapabilityContract(
        capability_id="cad.manufacturability_check",
        app_domain="cad",
        allowed_task_family="design_drawing",
        intervention_scope="design_drawing",
        creativity_allowed=False,
        creativity_tier="strict",
        hard_locks=["manufacturability"],
        description="Check manufacturability constraints",
    ),
}


def get_capability(capability_id: str) -> Optional[CapabilityContract]:
    return CAPABILITY_REGISTRY.get(capability_id)


def list_capabilities_for_domain(domain: str) -> list[CapabilityContract]:
    return [c for c in CAPABILITY_REGISTRY.values() if c.app_domain == domain]


def get_hard_locks_for_domain(domain: str) -> set[str]:
    """Return all hard locks across all capabilities for a domain."""
    locks = set()
    for c in CAPABILITY_REGISTRY.values():
        if c.app_domain == domain:
            locks.update(c.hard_locks)
    return locks
