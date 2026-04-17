"""
benchmarks/golden_benchmarks.py — Domain Golden Benchmarks v1.

Provides human-aligned rubric definitions and benchmark case specifications
for measuring LLM output quality across all 4 app domains.

Each benchmark case includes:
- Input specification
- Expected output characteristics
- Quality rubric (0-5 scale with criteria)
- Heuristic score correlation target

Designed for regression testing and human-aligned quality measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class RubricDimension:
    """Single quality dimension in a rubric."""
    name: str
    weight: float = 0.2
    description: str = ""
    score_5: str = ""       # what a perfect score looks like
    score_3: str = ""       # what an acceptable score looks like
    score_1: str = ""       # what a failing score looks like

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkCase:
    """Single golden benchmark test case."""
    case_id: str
    domain: str
    task_family: str
    description: str
    input_spec: dict[str, Any] = field(default_factory=dict)
    expected_output_keys: list[str] = field(default_factory=list)
    rubric: list[RubricDimension] = field(default_factory=list)
    heuristic_correlation: dict[str, float] = field(default_factory=dict)
    pass_threshold: float = 3.0     # minimum average rubric score to pass
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "task_family": self.task_family,
            "description": self.description,
            "input_spec": self.input_spec,
            "expected_output_keys": self.expected_output_keys,
            "rubric": [r.to_dict() for r in self.rubric],
            "pass_threshold": self.pass_threshold,
            "tags": self.tags,
        }


@dataclass
class BenchmarkResult:
    """Result of evaluating output against a benchmark case."""
    case_id: str
    scores: dict[str, float] = field(default_factory=dict)
    weighted_average: float = 0.0
    passed: bool = False
    missing_outputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Rubric templates ──

_MINECRAFT_BUILD_RUBRIC = [
    RubricDimension("structural_validity", 0.25,
                    "Build is structurally sound and buildable",
                    "All blocks have support, no floating elements, proper foundation",
                    "Minor floating elements but overall stable",
                    "Major structural issues, floating sections"),
    RubricDimension("style_coherence", 0.25,
                    "Block palette and proportions match the stated theme",
                    "Perfect theme match, consistent palette, proportional",
                    "Mostly consistent with minor palette drift",
                    "Theme mismatch, random block choices"),
    RubricDimension("functional_completeness", 0.25,
                    "Build serves its stated purpose",
                    "All functional elements present (doors, windows, rooms)",
                    "Most functional elements present",
                    "Missing critical functional elements"),
    RubricDimension("creative_quality", 0.25,
                    "Build shows thoughtful design beyond minimum",
                    "Distinctive design with intentional details",
                    "Standard design, adequate but unremarkable",
                    "Generic or template-like output"),
]

_MINECRAFT_NPC_RUBRIC = [
    RubricDimension("role_coherence", 0.30, "NPC role is clear and consistent"),
    RubricDimension("world_fit", 0.25, "NPC fits the world theme"),
    RubricDimension("personality_depth", 0.25, "Personality traits are distinct"),
    RubricDimension("dialogue_quality", 0.20, "Dialogue matches role and personality"),
]

_BUILDER_EXTERIOR_RUBRIC = [
    RubricDimension("code_compliance", 0.30,
                    "Plan meets building code requirements",
                    "All code requirements met, FAR/BCR within limits",
                    "Minor code issues, easily fixable",
                    "Major code violations"),
    RubricDimension("massing_quality", 0.25,
                    "Building massing is proportional and contextual"),
    RubricDimension("facade_design", 0.25,
                    "Facade has rhythm, materials, and entrance hierarchy"),
    RubricDimension("site_response", 0.20,
                    "Building responds to site conditions"),
]

_BUILDER_INTERIOR_RUBRIC = [
    RubricDimension("adjacency_logic", 0.25,
                    "Rooms are logically adjacent (kitchen near dining, etc.)"),
    RubricDimension("circulation_quality", 0.25,
                    "Circulation paths are efficient and code-compliant"),
    RubricDimension("wet_zone_logic", 0.25,
                    "Wet zones are stacked and grouped efficiently"),
    RubricDimension("spatial_quality", 0.25,
                    "Room proportions and natural light are adequate"),
]

_ANIMATION_CAMERA_RUBRIC = [
    RubricDimension("continuity", 0.30,
                    "Camera maintains spatial and temporal continuity",
                    "Perfect continuity, 180-degree rule maintained",
                    "Minor continuity gaps but readable",
                    "Disorienting continuity breaks"),
    RubricDimension("emotional_impact", 0.25,
                    "Camera choices amplify the intended emotion"),
    RubricDimension("shot_readability", 0.25,
                    "Each shot conveys one clear visual message"),
    RubricDimension("pacing", 0.20,
                    "Shot durations and transitions create good rhythm"),
]

_CAD_DESIGN_RUBRIC = [
    RubricDimension("dimensional_completeness", 0.25,
                    "All critical dimensions are specified",
                    "Every part has dimensions, tolerances, and fits",
                    "Most dimensions present, some inferred",
                    "Missing critical dimensions"),
    RubricDimension("manufacturability", 0.25,
                    "Design can be manufactured as specified"),
    RubricDimension("assembly_logic", 0.25,
                    "Parts can be assembled in logical sequence"),
    RubricDimension("routing_feasibility", 0.25,
                    "Wiring/drainage routes are physically possible"),
]


# ── Benchmark cases ──

BENCHMARK_CASES: list[BenchmarkCase] = [
    # Minecraft
    BenchmarkCase("mc_build_medieval_tower", "minecraft", "build",
                  "중세풍 방어 타워 생성",
                  {"build_type": "tower", "style_or_theme": "medieval", "anchor_type": "relative",
                   "constraints": ["cobblestone base", "height >= 15 blocks"]},
                  ["target_anchor", "operations", "block_palette", "style"],
                  _MINECRAFT_BUILD_RUBRIC,
                  {"mc_style_coherence": 0.8, "mc_structural_support": 0.7},
                  tags=["build", "medieval"]),

    BenchmarkCase("mc_build_modern_house", "minecraft", "build",
                  "모던 스타일 2층 주택",
                  {"build_type": "house", "style_or_theme": "modern",
                   "dimensions": {"width": 12, "depth": 10, "height": 8}},
                  ["target_anchor", "operations", "block_palette"],
                  _MINECRAFT_BUILD_RUBRIC,
                  tags=["build", "modern"]),

    BenchmarkCase("mc_npc_blacksmith", "minecraft", "npc",
                  "중세 마을 대장장이 NPC",
                  {"npc_role": "blacksmith", "world_theme": "medieval_village",
                   "personality_traits": ["gruff", "skilled", "fair"]},
                  ["npc_concept", "role_description"],
                  _MINECRAFT_NPC_RUBRIC,
                  tags=["npc"]),

    BenchmarkCase("mc_resourcepack_dark_fantasy", "minecraft", "resourcepack",
                  "다크 판타지 리소스팩",
                  {"pack_theme": "dark_fantasy", "target_blocks": ["stone", "wood", "leaves"],
                   "color_palette": ["#1a1a2e", "#16213e", "#0f3460"]},
                  ["style_plan", "palette"],
                  tags=["resourcepack"]),

    # Builder
    BenchmarkCase("builder_ext_residential_2f", "builder", "exterior_drawing",
                  "2층 단독주택 외부 도면",
                  {"building_type": "residential", "floors": 2, "total_area_m2": 120,
                   "facade_style": "modern_minimal"},
                  ["exterior_plan", "massing"],
                  _BUILDER_EXTERIOR_RUBRIC,
                  tags=["exterior", "residential"]),

    BenchmarkCase("builder_int_residential_2f", "builder", "interior_drawing",
                  "2층 단독주택 내부 도면",
                  {"floors": 2, "rooms": [
                      {"name": "거실", "area_m2": 25}, {"name": "주방", "area_m2": 12},
                      {"name": "안방", "area_m2": 18}, {"name": "침실", "area_m2": 12},
                      {"name": "화장실", "area_m2": 5}, {"name": "현관", "area_m2": 4}]},
                  ["spaces", "adjacency", "circulation"],
                  _BUILDER_INTERIOR_RUBRIC,
                  tags=["interior", "residential"]),

    BenchmarkCase("builder_consistency_check", "builder", "exterior_drawing",
                  "외부-내부 정합성 검증",
                  {"building_type": "residential", "floors": 2,
                   "rooms": [{"name": "거실"}, {"name": "주방"}, {"name": "침실"}]},
                  ["exterior_plan", "spaces"],
                  tags=["consistency"]),

    # Animation
    BenchmarkCase("anim_dialogue_closeup", "animation", "camera_walking",
                  "대화 장면 클로즈업 카메라 워킹",
                  {"scene_type": "dialogue", "emotion": "tension",
                   "duration_frames": 144, "characters": ["주인공", "적대자"]},
                  ["framing", "mood", "camera_move", "shots"],
                  _ANIMATION_CAMERA_RUBRIC,
                  tags=["camera", "dialogue"]),

    BenchmarkCase("anim_style_lock_check", "animation", "style_lock",
                  "스타일 락 유지 검증",
                  {"reference_style": {"art_style": "anime", "line_weight": "thin", "color_palette": "pastel"},
                   "check_target": {"art_style": "anime", "line_weight": "medium"}},
                  ["style_compliance", "drift_score"],
                  tags=["style_lock"]),

    BenchmarkCase("anim_style_feedback", "animation", "style_feedback",
                  "스타일 피드백 생성",
                  {"reference_style": {"art_style": "watercolor"},
                   "current_output": {"art_style": "oil_painting"},
                   "feedback_depth": "detailed"},
                  ["feedback_items", "severity_scores"],
                  tags=["style_feedback"]),

    # CAD
    BenchmarkCase("cad_shower_filter", "cad", "design_drawing",
                  "샤워 필터 설계도",
                  {"product_category": "small_appliance",
                   "dimensions": {"width_mm": 80, "depth_mm": 80, "height_mm": 200},
                   "sealing_grade": "IP67", "material": "ABS+PC"},
                  ["constraints", "systems", "parts"],
                  _CAD_DESIGN_RUBRIC,
                  tags=["cad", "waterproof"]),

    BenchmarkCase("cad_iot_sensor", "cad", "design_drawing",
                  "IoT 센서 모듈 설계도",
                  {"product_category": "iot_device",
                   "dimensions": {"width_mm": 60, "depth_mm": 40, "height_mm": 25},
                   "sealing_grade": "IP65"},
                  ["constraints", "systems"],
                  _CAD_DESIGN_RUBRIC,
                  tags=["cad", "iot"]),
]


def get_benchmarks_for_domain(domain: str) -> list[BenchmarkCase]:
    return [b for b in BENCHMARK_CASES if b.domain == domain]


def get_benchmark(case_id: str) -> Optional[BenchmarkCase]:
    return next((b for b in BENCHMARK_CASES if b.case_id == case_id), None)


class BenchmarkEvaluator:
    """Evaluates pipeline output against golden benchmark cases."""

    def evaluate(
        self,
        case: BenchmarkCase,
        output: Optional[dict[str, Any]],
    ) -> BenchmarkResult:
        """Evaluate output against a benchmark case.

        This provides automated structural checks. Human rubric scoring
        requires manual review of the actual output.
        """
        result = BenchmarkResult(case_id=case.case_id)

        if not output:
            result.passed = False
            result.notes.append("No output produced")
            return result

        # Check expected output keys
        for key in case.expected_output_keys:
            if key not in output:
                result.missing_outputs.append(key)

        # Auto-score structural dimensions
        if case.rubric:
            for dim in case.rubric:
                score = self._auto_score_dimension(dim, output, case)
                result.scores[dim.name] = score

            # Weighted average
            total_weight = sum(d.weight for d in case.rubric)
            if total_weight > 0:
                result.weighted_average = sum(
                    result.scores.get(d.name, 0) * d.weight
                    for d in case.rubric
                ) / total_weight

        result.passed = (
            len(result.missing_outputs) == 0
            and result.weighted_average >= case.pass_threshold
        )

        return result

    def _auto_score_dimension(
        self,
        dim: RubricDimension,
        output: dict,
        case: BenchmarkCase,
    ) -> float:
        """Auto-score a rubric dimension (structural proxy, not semantic).

        Returns 1-5 scale. This is a rough structural proxy;
        full rubric scoring requires human evaluation.
        """
        # Structural completeness proxy
        expected = case.expected_output_keys
        present = sum(1 for k in expected if k in output)
        completeness = present / len(expected) if expected else 1.0

        # Non-placeholder ratio
        from ..review.domain_evaluator import _all_leaf_values, _PLACEHOLDER_VALUES
        all_vals = list(_all_leaf_values(output))
        if all_vals:
            non_placeholder = sum(
                1 for v in all_vals
                if str(v).strip().lower() not in _PLACEHOLDER_VALUES
                and v is not None and v != [] and v != {}
            )
            substance = non_placeholder / len(all_vals)
        else:
            substance = 0.0

        # Map to 1-5 scale
        combined = 0.5 * completeness + 0.5 * substance
        return max(1.0, min(5.0, combined * 5.0))
