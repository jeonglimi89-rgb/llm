"""
domain/intervention_policy.py — Domain Intervention Policy.

Enforces that each app operates ONLY within its defined role scope.
Any cross-domain capability invocation is immediately rejected.

Policy is checked after route/classify and BEFORE any execution.
All checks are rule-based (0 LLM calls).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class InterventionPolicyResult:
    """Result of intervention policy check."""
    passed: bool = True
    app_domain: str = ""
    requested_task_family: str = ""
    violation_type: str = ""          # "" | "role_scope_violation" | "cross_domain_capability" | "unknown_task_family"
    violation_detail: str = ""
    allowed_families: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Hard-coded app role definitions (user-confirmed) ──

_APP_ALLOWED_FAMILIES: dict[str, list[str]] = {
    "minecraft": ["build", "npc", "resourcepack"],
    "builder": ["exterior_drawing", "interior_drawing"],
    "animation": ["camera_walking", "style_lock", "style_feedback"],
    "cad": ["design_drawing"],
    "product_design": ["design_drawing"],
}

# Task name → task family mapping (inferred from task_name patterns)
_TASK_TO_FAMILY: dict[str, str] = {
    # Minecraft
    "build_parse": "build",
    "build_plan_generate": "build",
    "build_style_validate": "build",
    "edit_parse": "build",
    "style_check": "build",
    "anchor_resolution": "build",
    "npc_concept_generate": "npc",
    "npc_role_validate": "npc",
    "character_parse": "npc",
    "dialogue_generate": "npc",
    "resourcepack_style_plan": "resourcepack",
    "resourcepack_consistency_check": "resourcepack",
    "style_parse": "resourcepack",
    # Builder
    "requirement_parse": "exterior_drawing",    # builder default family
    "patch_intent_parse": "exterior_drawing",
    "zone_priority_parse": "interior_drawing",
    "exterior_style_parse": "exterior_drawing",
    "exterior_drawing_generate": "exterior_drawing",
    "interior_drawing_generate": "interior_drawing",
    "exterior_interior_consistency_check": "exterior_drawing",
    "plan_adjacency_check": "interior_drawing",
    "facade_massing_variant_plan": "exterior_drawing",
    # Animation
    "shot_parse": "camera_walking",
    "camera_intent_parse": "camera_walking",
    "camera_walk_plan_generate": "camera_walking",
    "camera_continuity_check": "camera_walking",
    "lighting_intent_parse": "camera_walking",
    "edit_patch_parse": "camera_walking",
    "creative_direction": "camera_walking",
    "style_lock_check": "style_lock",
    "style_feedback_generate": "style_feedback",
    "identity_drift_check": "style_lock",
    # CAD
    "constraint_parse": "design_drawing",
    "patch_parse": "design_drawing",
    "system_split_parse": "design_drawing",
    "priority_parse": "design_drawing",
    "design_brief_parse": "design_drawing",
    "design_drawing_generate": "design_drawing",
    "assembly_feasibility_check": "design_drawing",
    "routing_precheck": "design_drawing",
    "manufacturability_check": "design_drawing",
    # Product Design
    "requirement_parse_pd": "design_drawing",
    "concept_parse": "design_drawing",
    "bom_parse": "design_drawing",
    # Context query (universal — allowed in all domains)
    "context_query": "_universal",
}

# Cross-domain deny list (explicit denial messages)
_CROSS_DOMAIN_DENY: dict[str, dict[str, str]] = {
    "minecraft": {
        "exterior_drawing": "Minecraft AI cannot generate building exterior drawings. Use Builder AI.",
        "interior_drawing": "Minecraft AI cannot generate building interior drawings. Use Builder AI.",
        "camera_walking": "Minecraft AI cannot plan camera walking. Use Animation AI.",
        "style_lock": "Minecraft AI cannot perform style lock checks. Use Animation AI.",
        "style_feedback": "Minecraft AI cannot generate style feedback. Use Animation AI.",
        "design_drawing": "Minecraft AI cannot generate engineering drawings. Use CAD AI.",
    },
    "builder": {
        "build": "Builder AI cannot generate Minecraft builds. Use Minecraft AI.",
        "npc": "Builder AI cannot generate NPCs. Use Minecraft AI.",
        "resourcepack": "Builder AI cannot generate resource packs. Use Minecraft AI.",
        "camera_walking": "Builder AI cannot plan camera walking. Use Animation AI.",
        "style_lock": "Builder AI cannot perform style lock checks. Use Animation AI.",
        "style_feedback": "Builder AI cannot generate style feedback. Use Animation AI.",
        "design_drawing": "Builder AI cannot generate engineering drawings. Use CAD AI.",
    },
    "animation": {
        "build": "Animation AI cannot generate Minecraft builds. Use Minecraft AI.",
        "npc": "Animation AI cannot generate NPCs. Use Minecraft AI.",
        "resourcepack": "Animation AI cannot generate resource packs. Use Minecraft AI.",
        "exterior_drawing": "Animation AI cannot generate building drawings. Use Builder AI.",
        "interior_drawing": "Animation AI cannot generate building drawings. Use Builder AI.",
        "design_drawing": "Animation AI cannot generate engineering drawings. Use CAD AI.",
    },
    "cad": {
        "build": "CAD AI cannot generate Minecraft builds. Use Minecraft AI.",
        "npc": "CAD AI cannot generate NPCs. Use Minecraft AI.",
        "resourcepack": "CAD AI cannot generate resource packs. Use Minecraft AI.",
        "exterior_drawing": "CAD AI cannot generate building drawings. Use Builder AI.",
        "interior_drawing": "CAD AI cannot generate building drawings. Use Builder AI.",
        "camera_walking": "CAD AI cannot plan camera walking. Use Animation AI.",
        "style_lock": "CAD AI cannot perform style lock checks. Use Animation AI.",
        "style_feedback": "CAD AI cannot generate style feedback. Use Animation AI.",
    },
}


class InterventionPolicy:
    """Enforces domain role boundaries."""

    def check(
        self,
        app_domain: str,
        task_name: str,
        inferred_domain: str = "",
    ) -> InterventionPolicyResult:
        """Check if the task is within the app's allowed scope.

        Args:
            app_domain: The domain that was routed to (minecraft/builder/animation/cad)
            task_name: The specific task name being invoked
            inferred_domain: The domain inferred from classification (may differ)
        """
        allowed = _APP_ALLOWED_FAMILIES.get(app_domain, [])
        task_family = _TASK_TO_FAMILY.get(task_name, "")

        # Universal tasks (context_query) are always allowed
        if task_family == "_universal":
            return InterventionPolicyResult(
                passed=True,
                app_domain=app_domain,
                requested_task_family=task_family,
                allowed_families=allowed,
            )

        # Unknown task family
        if not task_family:
            # For domain-native tasks not yet mapped, check if task_name
            # starts with a pattern that matches the domain
            if self._is_likely_native(app_domain, task_name):
                return InterventionPolicyResult(
                    passed=True,
                    app_domain=app_domain,
                    requested_task_family="native",
                    allowed_families=allowed,
                )
            return InterventionPolicyResult(
                passed=False,
                app_domain=app_domain,
                requested_task_family=task_family,
                violation_type="unknown_task_family",
                violation_detail=f"Task '{task_name}' has no known family mapping for domain '{app_domain}'",
                allowed_families=allowed,
            )

        # Check if task family is in allowed list
        if task_family in allowed:
            return InterventionPolicyResult(
                passed=True,
                app_domain=app_domain,
                requested_task_family=task_family,
                allowed_families=allowed,
            )

        # Cross-domain violation
        deny_msg = _CROSS_DOMAIN_DENY.get(app_domain, {}).get(task_family, "")
        if not deny_msg:
            deny_msg = (
                f"Domain '{app_domain}' is not allowed to perform "
                f"task family '{task_family}'. Allowed: {allowed}"
            )

        return InterventionPolicyResult(
            passed=False,
            app_domain=app_domain,
            requested_task_family=task_family,
            violation_type="role_scope_violation",
            violation_detail=deny_msg,
            allowed_families=allowed,
        )

    def check_graph_node(
        self,
        app_domain: str,
        capability_id: str,
    ) -> InterventionPolicyResult:
        """Check if a graph node's capability is valid for the app domain."""
        # Extract domain prefix from capability_id
        parts = capability_id.split(".", 1)
        if len(parts) < 2:
            return InterventionPolicyResult(
                passed=False,
                app_domain=app_domain,
                violation_type="cross_domain_capability_violation",
                violation_detail=f"Invalid capability_id format: '{capability_id}'",
            )

        cap_domain = parts[0]
        # NPC and resourcepack are minecraft sub-domains
        if cap_domain in ("npc", "resourcepack"):
            cap_domain = "minecraft"

        if cap_domain != app_domain and cap_domain != "product_design":
            return InterventionPolicyResult(
                passed=False,
                app_domain=app_domain,
                violation_type="cross_domain_capability_violation",
                violation_detail=(
                    f"Capability '{capability_id}' belongs to domain '{cap_domain}', "
                    f"but current app domain is '{app_domain}'"
                ),
            )

        return InterventionPolicyResult(passed=True, app_domain=app_domain)

    def _is_likely_native(self, domain: str, task_name: str) -> bool:
        """Heuristic check if task_name is native to the domain."""
        # Builder tasks that start with common prefixes
        native_prefixes = {
            "minecraft": ["build", "edit", "style", "anchor", "npc", "resource", "block"],
            "builder": ["requirement", "patch", "zone", "exterior", "interior", "facade", "plan", "floor"],
            "animation": ["shot", "camera", "lighting", "edit", "creative", "style", "identity"],
            "cad": ["constraint", "patch", "system", "priority", "design", "assembly", "routing", "manufacture"],
            "product_design": ["requirement", "concept", "bom", "patch"],
        }
        prefixes = native_prefixes.get(domain, [])
        return any(task_name.startswith(p) for p in prefixes)
