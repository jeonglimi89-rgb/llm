"""
domain/heuristics.py — Domain-specific heuristic packs.

Heuristics are organized into three categories per domain:
- safety: MUST pass regardless of creative mode
- quality: always applied, affects scoring
- creativity: only applied when creative mode is active

Each heuristic has:
- applies_when: condition for activation ("always", "mode==expressive", "novelty>0.5")
- priority: 0=highest
- check_fn_name: name of the check function to invoke
- repair_hint: guidance for how to fix violations
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import json


@dataclass
class Heuristic:
    heuristic_id: str
    domain: str
    category: str           # "safety"|"creativity"|"quality"
    applies_when: str       # "always"|"mode==expressive"|"novelty>0.5"
    priority: int           # 0=highest
    check_fn_name: str
    repair_hint: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Heuristic:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class HeuristicPack:
    """All heuristics for a single domain, split by category."""

    domain: str
    safety_heuristics: list[Heuristic] = field(default_factory=list)
    creativity_heuristics: list[Heuristic] = field(default_factory=list)
    quality_heuristics: list[Heuristic] = field(default_factory=list)

    def all_heuristics(self) -> list[Heuristic]:
        return self.safety_heuristics + self.quality_heuristics + self.creativity_heuristics

    def all_applicable(
        self,
        creative_profile: Optional[Any] = None,
    ) -> list[Heuristic]:
        """Return heuristics sorted by priority, filtered by applies_when."""
        all_h = self.all_heuristics()
        filtered = [
            h for h in all_h
            if _matches_condition(h.applies_when, creative_profile)
        ]
        return sorted(filtered, key=lambda h: h.priority)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "safety": [h.to_dict() for h in self.safety_heuristics],
            "creativity": [h.to_dict() for h in self.creativity_heuristics],
            "quality": [h.to_dict() for h in self.quality_heuristics],
        }

    @classmethod
    def from_dict(cls, domain: str, d: dict) -> HeuristicPack:
        return cls(
            domain=domain,
            safety_heuristics=[Heuristic.from_dict(h) for h in d.get("safety", [])],
            creativity_heuristics=[Heuristic.from_dict(h) for h in d.get("creativity", [])],
            quality_heuristics=[Heuristic.from_dict(h) for h in d.get("quality", [])],
        )


def _matches_condition(condition: str, cp: Optional[Any]) -> bool:
    """Evaluate simple condition strings against a CreativeProfile."""
    if condition == "always":
        return True
    if cp is None:
        # Without creative profile, only "always" conditions match
        return False

    # Simple parser for conditions like "mode==expressive", "novelty>0.5"
    if "==" in condition:
        field_name, val = condition.split("==", 1)
        field_name = field_name.strip()
        val = val.strip()
        return str(getattr(cp, field_name, "")).lower() == val.lower()
    elif ">" in condition:
        field_name, val = condition.split(">", 1)
        field_name = field_name.strip()
        val = val.strip()
        try:
            return float(getattr(cp, field_name, 0)) > float(val)
        except (TypeError, ValueError):
            return False
    elif "<" in condition:
        field_name, val = condition.split("<", 1)
        field_name = field_name.strip()
        val = val.strip()
        try:
            return float(getattr(cp, field_name, 0)) < float(val)
        except (TypeError, ValueError):
            return False

    return False


# ── Default heuristic packs ──

def _build_default_packs() -> dict[str, HeuristicPack]:
    """Build built-in heuristic packs matching user requirements."""
    return {
        "minecraft": HeuristicPack(
            domain="minecraft",
            safety_heuristics=[
                Heuristic("mc_block_existence", "minecraft", "safety", "always", 0,
                          "check_block_existence", "Replace invalid blocks with nearest valid block",
                          "All blocks must be valid Minecraft blocks"),
                Heuristic("mc_structural_support", "minecraft", "safety", "always", 1,
                          "check_structural_support", "Add support blocks beneath floating structures",
                          "Structures must be structurally sound"),
                Heuristic("mc_anchor_validity", "minecraft", "safety", "always", 2,
                          "check_anchor_validity", "Specify a valid anchor point",
                          "Target anchor must be valid"),
            ],
            quality_heuristics=[
                Heuristic("mc_style_coherence", "minecraft", "quality", "always", 3,
                          "check_style_coherence", "Align block choices with declared style",
                          "Block palette must match declared style"),
                Heuristic("mc_silhouette_proportion", "minecraft", "quality", "always", 4,
                          "check_silhouette", "Adjust height/width ratio for better silhouette",
                          "Building silhouette should follow architectural proportions"),
                Heuristic("mc_facade_rhythm", "minecraft", "quality", "always", 5,
                          "check_facade_rhythm", "Add window/detail spacing pattern",
                          "Facade should have regular rhythm of details"),
                Heuristic("mc_npc_role_coherence", "minecraft", "quality", "always", 6,
                          "check_npc_role", "Align NPC traits with assigned role",
                          "NPC role, personality, and dialogue must be consistent"),
                Heuristic("mc_resource_palette_logic", "minecraft", "quality", "always", 7,
                          "check_resource_palette", "Ensure resource pack materials are internally consistent",
                          "Resource pack palette/material consistency"),
            ],
            creativity_heuristics=[
                Heuristic("mc_palette_diversity", "minecraft", "creativity", "novelty>0.5", 10,
                          "check_palette_diversity", "Add accent blocks to palette",
                          "Palette should have sufficient diversity for creative mode"),
                Heuristic("mc_theme_variation", "minecraft", "creativity", "mode==balanced", 11,
                          "check_theme_variation", "Mix compatible architectural elements",
                          "Theme variation should be intentional and coherent"),
                Heuristic("mc_world_expansion", "minecraft", "creativity", "mode==expressive", 12,
                          "check_world_expansion", "Expand world concept while maintaining core identity",
                          "World expansion should add novelty without breaking coherence"),
            ],
        ),
        "builder": HeuristicPack(
            domain="builder",
            safety_heuristics=[
                Heuristic("builder_code_compliance", "builder", "safety", "always", 0,
                          "check_code_compliance", "Adjust plan to meet building code requirements",
                          "Building code compliance is mandatory"),
                Heuristic("builder_circulation_min", "builder", "safety", "always", 1,
                          "check_circulation_minimum", "Ensure minimum corridor width and exit paths",
                          "Circulation paths must meet minimum standards"),
                Heuristic("builder_wet_zone_align", "builder", "safety", "always", 2,
                          "check_wet_zone_alignment", "Stack wet zones vertically for plumbing efficiency",
                          "Wet zones (kitchen, bathroom) must be logically grouped"),
            ],
            quality_heuristics=[
                Heuristic("builder_exterior_massing", "builder", "quality", "always", 3,
                          "check_exterior_massing", "Adjust building massing for better proportions",
                          "Exterior massing should follow design principles"),
                Heuristic("builder_entrance_emphasis", "builder", "quality", "always", 4,
                          "check_entrance_emphasis", "Define clear main entrance with hierarchy",
                          "Building entrance should be clearly emphasized"),
                Heuristic("builder_facade_rhythm", "builder", "quality", "always", 5,
                          "check_facade_rhythm", "Add window spacing and material variation pattern",
                          "Facade should have rhythmic pattern"),
                Heuristic("builder_interior_adjacency", "builder", "quality", "always", 6,
                          "check_interior_adjacency", "Place related rooms adjacent to each other",
                          "Interior room adjacency should be logical"),
                Heuristic("builder_privacy_zoning", "builder", "quality", "always", 7,
                          "check_privacy_zoning", "Separate public and private zones clearly",
                          "Privacy zoning: public/semi-public/private gradient"),
            ],
            creativity_heuristics=[
                Heuristic("builder_massing_variation", "builder", "creativity", "novelty>0.3", 10,
                          "check_massing_variation", "Try alternative massing without breaking code",
                          "Massing variation while maintaining compliance"),
                Heuristic("builder_elevation_rhythm", "builder", "creativity", "novelty>0.3", 11,
                          "check_elevation_rhythm_var", "Propose alternative facade rhythms",
                          "Elevation rhythm variation"),
            ],
        ),
        "animation": HeuristicPack(
            domain="animation",
            safety_heuristics=[
                Heuristic("anim_180_degree_rule", "animation", "safety", "always", 0,
                          "check_180_degree_rule", "Maintain consistent screen direction across shots",
                          "180-degree rule must be maintained"),
                Heuristic("anim_camera_continuity", "animation", "safety", "always", 1,
                          "check_camera_continuity", "Ensure smooth camera transitions between shots",
                          "Camera continuity across shot boundaries"),
                Heuristic("anim_character_identity_lock", "animation", "safety", "always", 2,
                          "check_character_identity", "Preserve character visual identity across scenes",
                          "Character identity must not drift"),
            ],
            quality_heuristics=[
                Heuristic("anim_shot_readability", "animation", "quality", "always", 3,
                          "check_shot_readability", "Simplify framing to improve visual clarity",
                          "Each shot should convey one clear visual message"),
                Heuristic("anim_style_consistency", "animation", "quality", "always", 4,
                          "check_style_consistency", "Align visual elements with established style guide",
                          "Visual style must remain consistent throughout"),
                Heuristic("anim_feedback_quality", "animation", "quality", "always", 5,
                          "check_feedback_quality", "Make feedback actionable and specific",
                          "Style feedback must be concrete and fixable"),
            ],
            creativity_heuristics=[
                Heuristic("anim_framing_exploration", "animation", "creativity", "novelty>0.4", 10,
                          "check_framing_exploration", "Try alternative framing while preserving readability",
                          "Framing variation within continuity rules"),
                Heuristic("anim_camera_motion_variety", "animation", "creativity", "novelty>0.4", 11,
                          "check_camera_motion_variety", "Propose alternative camera movements",
                          "Camera motion variety for emotional impact"),
                Heuristic("anim_emotional_staging", "animation", "creativity", "mode==balanced", 12,
                          "check_emotional_staging", "Intensify emotional staging cues",
                          "Emotional staging nuance and timing"),
            ],
        ),
        "cad": HeuristicPack(
            domain="cad",
            safety_heuristics=[
                Heuristic("cad_dimension_sanity", "cad", "safety", "always", 0,
                          "check_dimension_sanity", "Correct dimensions to physically plausible values",
                          "All dimensions must be positive and reasonable"),
                Heuristic("cad_fastening_logic", "cad", "safety", "always", 1,
                          "check_fastening_logic", "Ensure all parts have valid fastening methods",
                          "Assembly fastening must be specified"),
                Heuristic("cad_waterproof_integrity", "cad", "safety", "always", 2,
                          "check_waterproof_integrity", "Verify sealing zones cover all ingress points",
                          "Waterproof/dustproof integrity must be maintained"),
            ],
            quality_heuristics=[
                Heuristic("cad_part_logic", "cad", "quality", "always", 3,
                          "check_part_logic", "Ensure each part has a clear function",
                          "Every part must serve a defined function"),
                Heuristic("cad_assembly_access", "cad", "quality", "always", 4,
                          "check_assembly_access", "Verify assembly/disassembly clearance",
                          "Assembly access and maintenance paths"),
                Heuristic("cad_drawing_completeness", "cad", "quality", "always", 5,
                          "check_drawing_completeness", "Add missing views or dimensions",
                          "Drawing must include all necessary views and dimensions"),
            ],
            creativity_heuristics=[
                Heuristic("cad_concept_alternative", "cad", "creativity", "novelty>0.2", 10,
                          "check_concept_validity", "Validate engineering merit of alternative concept",
                          "Creative concept must have engineering validity, not just form change"),
                Heuristic("cad_form_factor", "cad", "creativity", "novelty>0.2", 11,
                          "check_form_factor_feasibility", "Verify manufacturability of alternative form",
                          "Form factor exploration within manufacturing constraints"),
            ],
        ),
        "product_design": HeuristicPack(
            domain="product_design",
            safety_heuristics=[
                Heuristic("pd_dimension_sanity", "product_design", "safety", "always", 0,
                          "check_dimension_sanity", "Correct dimensions to physically plausible values",
                          "Product dimensions must be reasonable"),
                Heuristic("pd_certification_check", "product_design", "safety", "always", 1,
                          "check_certification", "Verify certification requirements are addressed",
                          "Required certifications must be specified"),
            ],
            quality_heuristics=[
                Heuristic("pd_bom_completeness", "product_design", "quality", "always", 3,
                          "check_bom_completeness", "Add missing BOM items",
                          "BOM must cover all functional requirements"),
                Heuristic("pd_user_target", "product_design", "quality", "always", 4,
                          "check_user_target", "Define specific target user",
                          "Target user must be clearly specified"),
            ],
            creativity_heuristics=[
                Heuristic("pd_concept_novelty", "product_design", "creativity", "novelty>0.2", 10,
                          "check_concept_novelty", "Ensure concept adds product value, not just aesthetics",
                          "Product concept must offer practical innovation"),
            ],
        ),
    }


def load_heuristic_packs(
    configs_dir: Optional[Path] = None,
) -> dict[str, HeuristicPack]:
    """Load heuristic packs. Uses built-in defaults, with optional JSON override.

    If configs_dir/heuristic_packs.json exists, it is merged on top of defaults.
    """
    packs = _build_default_packs()

    if configs_dir:
        json_path = Path(configs_dir) / "heuristic_packs.json"
        if json_path.exists():
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            for domain, pack_data in data.items():
                packs[domain] = HeuristicPack.from_dict(domain, pack_data)

    return packs
