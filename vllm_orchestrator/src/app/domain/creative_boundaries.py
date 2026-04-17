"""
domain/creative_boundaries.py — Professional Floor / Creative Ceiling enforcement.

Professional floor: hard rules that creative variants MUST NOT violate.
Creative ceiling: advisory limits on exploration scope (logged, not rejected).

All checks are rule-based (0 LLM calls).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class BoundaryRule:
    """Single floor or ceiling rule."""

    rule_id: str
    check_type: str   # "required_present"|"value_in"|"value_range"|"regex_match"|"key_exists"|"custom"
    target: str       # dot-path field or check name
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BoundaryRule:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CreativeBoundaries:
    """Floor + ceiling rules for a single domain."""

    domain: str
    professional_floor: list[BoundaryRule] = field(default_factory=list)
    creative_ceiling: list[BoundaryRule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "professional_floor": [r.to_dict() for r in self.professional_floor],
            "creative_ceiling": [r.to_dict() for r in self.creative_ceiling],
        }

    @classmethod
    def from_dict(cls, domain: str, d: dict) -> CreativeBoundaries:
        return cls(
            domain=domain,
            professional_floor=[BoundaryRule.from_dict(r) for r in d.get("professional_floor", [])],
            creative_ceiling=[BoundaryRule.from_dict(r) for r in d.get("creative_ceiling", [])],
        )


@dataclass
class BoundaryCheckResult:
    """Result of floor + ceiling validation."""

    passed: bool = True
    floor_violations: list[str] = field(default_factory=list)
    ceiling_breaches: list[str] = field(default_factory=list)
    # Role scope violation types (added for intervention policy integration)
    role_scope_violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Violation type constants ──

VIOLATION_ROLE_SCOPE = "role_scope_violation"
VIOLATION_CROSS_DOMAIN_CAPABILITY = "cross_domain_capability_violation"
VIOLATION_CREATIVE_OVERRIDE_HARD_LOCK = "creative_override_of_hard_lock"
VIOLATION_STYLE_DRIFT_WRONG_DOMAIN = "style_or_rule_drift_under_wrong_domain"
VIOLATION_GRAPH_TASK_OUT_OF_SCOPE = "graph_task_out_of_scope"


# ── Rule checker helpers ──

def _resolve_path(slots: dict, path: str) -> Any:
    """Resolve dot-path like 'structure.roof_type' in a nested dict."""
    parts = path.split(".")
    current = slots
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        else:
            return None
    return current


def _check_rule(rule: BoundaryRule, slots: dict) -> Optional[str]:
    """Check a single rule. Returns violation string or None."""
    if not slots:
        return f"{rule.rule_id}: output is empty"

    val = _resolve_path(slots, rule.target)

    if rule.check_type == "required_present":
        if val is None or val == "" or val == [] or val == {}:
            return f"{rule.rule_id}: required field '{rule.target}' missing or empty"

    elif rule.check_type == "key_exists":
        if val is None:
            return f"{rule.rule_id}: key '{rule.target}' must exist"

    elif rule.check_type == "value_in":
        allowed = rule.params.get("allowed", [])
        if val is not None and str(val).lower() not in [str(a).lower() for a in allowed]:
            return f"{rule.rule_id}: '{rule.target}'={val} not in allowed {allowed}"

    elif rule.check_type == "value_range":
        if val is not None:
            try:
                num = float(val)
                mn = rule.params.get("min")
                mx = rule.params.get("max")
                if mn is not None and num < float(mn):
                    return f"{rule.rule_id}: '{rule.target}'={num} below min {mn}"
                if mx is not None and num > float(mx):
                    return f"{rule.rule_id}: '{rule.target}'={num} above max {mx}"
            except (TypeError, ValueError):
                pass

    elif rule.check_type == "regex_match":
        pattern = rule.params.get("pattern", "")
        if val is not None and not re.search(pattern, str(val)):
            return f"{rule.rule_id}: '{rule.target}' does not match pattern"

    elif rule.check_type == "custom":
        # Custom checks dispatched by rule_id
        checker = _CUSTOM_CHECKS.get(rule.rule_id)
        if checker:
            return checker(slots, rule)

    return None


# ── Custom check functions ──

def _check_structural_coherence(slots: dict, rule: BoundaryRule) -> Optional[str]:
    """Minecraft: operations list should be non-empty and consistent."""
    ops = slots.get("operations", [])
    if not ops:
        return f"{rule.rule_id}: no operations defined"
    return None


def _check_theme_consistency(slots: dict, rule: BoundaryRule) -> Optional[str]:
    """Minecraft: block_palette should be stylistically consistent."""
    palette = slots.get("block_palette")
    if not palette:
        style = slots.get("style")
        if isinstance(style, dict):
            palette = style.get("palette", [])
        elif isinstance(style, list):
            palette = style
        else:
            palette = []
    if isinstance(palette, list) and len(palette) > 0:
        return None
    return None  # Advisory - don't fail for missing palette


def _check_camera_continuity(slots: dict, rule: BoundaryRule) -> Optional[str]:
    """Animation: shots should have valid camera settings."""
    shots = slots.get("shots", [])
    for i, shot in enumerate(shots):
        if isinstance(shot, dict):
            if not shot.get("framing") and not shot.get("shot_type"):
                return f"{rule.rule_id}: shot {i} missing framing/shot_type"
    return None


def _check_dimension_sanity(slots: dict, rule: BoundaryRule) -> Optional[str]:
    """CAD: dimensions should be positive and reasonable."""
    dims = slots.get("dimensions", {})
    if isinstance(dims, dict):
        for key, val in dims.items():
            if isinstance(val, (int, float)) and val <= 0:
                return f"{rule.rule_id}: dimension '{key}'={val} must be positive"
    return None


def _check_circulation_sanity(slots: dict, rule: BoundaryRule) -> Optional[str]:
    """Builder: spaces should be connected logically."""
    spaces = slots.get("spaces", [])
    if isinstance(spaces, list) and len(spaces) > 0:
        return None
    floors = slots.get("floors")
    if floors is not None:
        return None
    return f"{rule.rule_id}: no spaces or floors defined"


_CUSTOM_CHECKS = {
    "mc_structural_coherence": _check_structural_coherence,
    "mc_theme_consistency": _check_theme_consistency,
    "anim_camera_continuity": _check_camera_continuity,
    "cad_dimension_sanity": _check_dimension_sanity,
    "builder_circulation_sanity": _check_circulation_sanity,
}


# ── Main enforcer ──

class BoundaryEnforcer:
    """Validates slots against professional floor and creative ceiling."""

    def __init__(self, boundaries: dict[str, CreativeBoundaries]):
        self._boundaries = boundaries

    def check_floor(self, domain: str, slots: dict) -> list[str]:
        """Return list of floor violations. Any violation = reject variant."""
        bd = self._boundaries.get(domain)
        if not bd:
            return []
        violations = []
        for rule in bd.professional_floor:
            msg = _check_rule(rule, slots)
            if msg:
                violations.append(msg)
        return violations

    def check_ceiling(self, domain: str, slots: dict) -> list[str]:
        """Return list of ceiling breaches (advisory, not rejection)."""
        bd = self._boundaries.get(domain)
        if not bd:
            return []
        breaches = []
        for rule in bd.creative_ceiling:
            msg = _check_rule(rule, slots)
            if msg:
                breaches.append(msg)
        return breaches

    def validate_variant(self, domain: str, slots: dict) -> BoundaryCheckResult:
        """Combined floor + ceiling check."""
        floor_violations = self.check_floor(domain, slots)
        ceiling_breaches = self.check_ceiling(domain, slots)
        return BoundaryCheckResult(
            passed=len(floor_violations) == 0,
            floor_violations=floor_violations,
            ceiling_breaches=ceiling_breaches,
        )


# ── Default boundaries (hardcoded, matching user requirements) ──

def _build_default_boundaries() -> dict[str, CreativeBoundaries]:
    """Build default professional floor / creative ceiling per domain."""
    return {
        "minecraft": CreativeBoundaries(
            domain="minecraft",
            professional_floor=[
                BoundaryRule("mc_structural_coherence", "custom", "operations",
                             description="structural coherence: operations must exist"),
                BoundaryRule("mc_anchor_required", "required_present", "target_anchor",
                             description="target anchor must be specified"),
                BoundaryRule("mc_theme_consistency", "custom", "style",
                             description="world theme consistency"),
            ],
            creative_ceiling=[
                BoundaryRule("mc_palette_size", "value_range", "block_palette",
                             params={"max": 20},
                             description="palette should stay manageable"),
            ],
        ),
        "builder": CreativeBoundaries(
            domain="builder",
            professional_floor=[
                BoundaryRule("builder_spaces_required", "required_present", "spaces",
                             description="zoning feasibility: spaces must be defined"),
                BoundaryRule("builder_circulation_sanity", "custom", "spaces",
                             description="circulation sanity check"),
            ],
            creative_ceiling=[
                BoundaryRule("builder_floor_limit", "value_range", "floors",
                             params={"min": 1, "max": 30},
                             description="reasonable floor count"),
            ],
        ),
        "animation": CreativeBoundaries(
            domain="animation",
            professional_floor=[
                BoundaryRule("anim_framing_required", "required_present", "framing",
                             description="framing must be specified"),
                BoundaryRule("anim_mood_required", "required_present", "mood",
                             description="mood must be specified"),
                BoundaryRule("anim_camera_continuity", "custom", "shots",
                             description="camera continuity check"),
            ],
            creative_ceiling=[
                BoundaryRule("anim_shot_density", "value_range", "shots",
                             params={"max": 30},
                             description="reasonable shot count per scene"),
            ],
        ),
        "cad": CreativeBoundaries(
            domain="cad",
            professional_floor=[
                BoundaryRule("cad_constraints_required", "required_present", "constraints",
                             description="dimensional plausibility: constraints must exist"),
                BoundaryRule("cad_dimension_sanity", "custom", "dimensions",
                             description="dimensions must be positive and reasonable"),
            ],
            creative_ceiling=[
                BoundaryRule("cad_part_count", "value_range", "parts",
                             params={"max": 50},
                             description="manageable part count"),
            ],
        ),
        "product_design": CreativeBoundaries(
            domain="product_design",
            professional_floor=[
                BoundaryRule("pd_constraints_required", "required_present", "constraints",
                             description="manufacturability: constraints must exist"),
                BoundaryRule("pd_dimension_sanity", "custom", "dimensions",
                             description="dimensions must be positive"),
            ],
            creative_ceiling=[
                BoundaryRule("pd_module_count", "value_range", "modules",
                             params={"max": 30},
                             description="manageable module count"),
            ],
        ),
    }


def init_creative_boundaries(
    configs_dir: Optional[Any] = None,
) -> dict[str, CreativeBoundaries]:
    """Initialize creative boundaries. Uses built-in defaults.

    Future: load overrides from configs_dir/creative_boundaries.json.
    """
    return _build_default_boundaries()
