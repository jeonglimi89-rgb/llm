"""variant_sampler.py — Parallel multi-variant sampling for scene_graph.

기존 orchestration/variant_planner.py 는 /tasks/orchestrate 경로의 복잡한
도메인-분류 pipeline 용. 이 모듈은 /tasks/submit 경로에서 간단히
3개 variant를 병렬 생성하고 결정론적으로 스코어링해서 best 선택.

Design:
  - ThreadPoolExecutor로 N개 LLM 호출을 동시에 vLLM 서버로 보냄
  - vLLM이 내부적으로 배치 처리 → sequential 대비 1.5~2배 빠름
  - 각 variant에 heuristic repair 적용
  - 결정론적 scorer (rule-based, 0-10 scale)
  - Top-1 선택 후 metadata에 all variants 노출

Scoring rubric (scene_graph):
  + node count in 5-15 range: +2
  + ≥4 distinct materials: +2
  + ≥5 distinct xz positions: +2
  + has central cylinder (keep for castle): +1 (castle only)
  + grounded (min_y=0) OR floating y≥15: +1
  + concept_notes ≥50 chars: +1
  + no cross-theme material leakage: +1
  = max 10
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Optional


# ── Strategy suffixes (scene_graph에 주입) ──────────────────────────────────

_VARIANT_STRATEGIES: list[tuple[str, str, str]] = [
    (
        "safe_baseline",
        "Standard interpretation",
        "",  # no suffix — baseline
    ),
    (
        "creative_variant",
        "Creative exploration",
        "\n\n**VARIANT MODE**: generate a CREATIVE alternative — push materials, silhouette, "
        "and composition further while respecting ALL hard rules (theme, diversity, spatial distribution). "
        "Favor unexpected-but-coherent material combinations and asymmetric arrangements.",
    ),
    (
        "expansion_variant",
        "World expansion",
        "\n\n**VARIANT MODE**: generate an EXPANDED version with extra structural elements "
        "(annex, balcony, auxiliary structure, garden detail) while keeping core concept. "
        "More nodes (12-18), richer spatial spread, narrative details in concept_notes.",
    ),
]


@dataclass
class VariantOutcome:
    family: str = ""
    label: str = ""
    strategy_suffix: str = ""
    slots: Optional[dict] = None
    raw_text: str = ""
    latency_ms: int = 0
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    accepted: bool = True
    parse_failed: bool = False
    heuristic_repairs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VariantSamplingReport:
    variant_count: int = 0
    variants: list[VariantOutcome] = field(default_factory=list)
    selected_family: str = ""
    selection_reason: str = ""
    total_wall_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "variant_count": self.variant_count,
            "variants": [v.to_dict() for v in self.variants],
            "selected_family": self.selected_family,
            "selection_reason": self.selection_reason,
            "total_wall_ms": self.total_wall_ms,
        }

    @property
    def selected(self) -> Optional[VariantOutcome]:
        for v in self.variants:
            if v.family == self.selected_family:
                return v
        return None


# ── Scorer ──────────────────────────────────────────────────────────────────

_THEME_PALETTES = {
    "witch": {"deepslate", "cobbled_deepslate", "dark_oak_planks", "purple_stained_glass", "soul_lantern", "cobblestone"},
    "waffle": {"yellow_concrete", "honey_block", "honeycomb_block", "orange_concrete", "white_concrete"},
    "frog": {"moss_block", "slime_block", "lily_pad", "water", "mangrove_planks", "mud"},
    "sky": {"glass", "packed_ice", "sea_lantern", "purpur_block", "grass_block", "dirt", "coarse_dirt", "mud", "stone", "quartz_block"},
    "medieval": {"stone_bricks", "cobblestone", "oak_planks", "dark_oak_log"},
}


def score_scene_graph(slots: dict, user_input: str, intent_report: Optional[dict] = None) -> tuple[float, dict]:
    """Rule-based scoring. Returns (score_0_10, breakdown_dict)."""
    if not isinstance(slots, dict):
        return 0.0, {"reason": "invalid_slots"}
    nodes = slots.get("nodes") or []
    n = len(nodes)
    bd: dict[str, float] = {}

    # 1. Node count
    if 5 <= n <= 15:
        bd["node_count"] = 2.0
    elif 3 <= n <= 20:
        bd["node_count"] = 1.0
    else:
        bd["node_count"] = 0.0

    # 2. Material diversity
    mats = {x.get("material") for x in nodes if x.get("material")}
    if len(mats) >= 4:
        bd["material_diversity"] = 2.0
    elif len(mats) >= 3:
        bd["material_diversity"] = 1.0
    else:
        bd["material_diversity"] = 0.0

    # 3. Spatial distribution
    xz = set()
    min_y = 999
    for x in nodes:
        p = x.get("position")
        if isinstance(p, dict):
            xz.add((p.get("x"), p.get("z")))
            try:
                y = p.get("y")
                if y is not None and y < min_y:
                    min_y = y
            except TypeError:
                pass
    if len(xz) >= 5:
        bd["spatial_distribution"] = 2.0
    elif len(xz) >= 3:
        bd["spatial_distribution"] = 1.0
    else:
        bd["spatial_distribution"] = 0.0

    # 4. Castle: central keep
    lower_input = (user_input or "").lower()
    is_castle = any(k in lower_input for k in ("성", "castle", "fortress", "keep", "요새"))
    if is_castle:
        has_keep = any(
            x.get("primitive_type") == "cylinder"
            and isinstance(x.get("position"), dict)
            and x["position"].get("x") == 0
            and x["position"].get("z") == 0
            for x in nodes
        ) or any("keep" in (x.get("id") or "").lower() for x in nodes)
        bd["castle_keep"] = 1.0 if has_keep else 0.0
    else:
        bd["castle_keep"] = 1.0  # not applicable, don't penalize

    # 5. Grounded or properly floating
    is_floating = any(k in lower_input for k in ("하늘섬", "floating", "떠있는", "공중", "sky island"))
    if is_floating:
        # island base y≥15 + underside cone exists
        island_bases = [x for x in nodes if x.get("primitive_type") == "cuboid"
                        and isinstance(x.get("position"), dict) and x["position"].get("y", 0) >= 15]
        under_cones = [x for x in nodes if x.get("primitive_type") == "cone"
                       and isinstance(x.get("position"), dict) and x["position"].get("y", 99) < 15]
        bd["grounded_or_floating"] = 1.0 if (island_bases and under_cones) else 0.0
    else:
        bd["grounded_or_floating"] = 1.0 if min_y == 0 else 0.0

    # 6. concept_notes
    notes = slots.get("concept_notes") or ""
    bd["concept_notes"] = 1.0 if len(notes) >= 50 else 0.5 if len(notes) >= 15 else 0.0

    # 7. Theme leakage (cross-theme material)
    theme = None
    if intent_report:
        c = intent_report.get("concept_category")
        if c in ("witch", "waffle", "frog", "sky", "sky_surface", "medieval"):
            theme = "sky" if c == "sky_surface" else c
    if theme and theme in _THEME_PALETTES:
        allowed = _THEME_PALETTES[theme].union(_THEME_PALETTES["sky"])  # underside always allowed
        leakage = [m for m in mats if m and m not in allowed and m not in ("", "water")]
        bd["theme_integrity"] = 1.0 if not leakage else max(0.0, 1.0 - 0.25 * len(leakage))
    else:
        bd["theme_integrity"] = 1.0  # generic/unknown theme — don't penalize

    total = sum(bd.values())
    return round(total, 2), bd


# ── Sampler ─────────────────────────────────────────────────────────────────

def _apply_heuristic_repair_safe(slots: dict, user_input: str) -> tuple[dict, list[str]]:
    try:
        from ..domain.scene_graph_repair import repair_scene_graph
        return repair_scene_graph(slots, user_input)
    except Exception:
        return slots, []


def sample_variants(
    llm_client,
    spec,
    system_prompt: str,
    user_input: str,
    *,
    variant_count: int = 3,
    timeout_s: float = 30.0,
    total_deadline_s: Optional[float] = None,
    intent_report: Optional[dict] = None,
) -> VariantSamplingReport:
    """LLM을 N회 병렬 호출해서 variant 생성. vLLM이 내부 배치 처리 → 속도 이득.

    Args:
        llm_client: LLMClient 인스턴스
        spec: TaskSpec (pool_type 참조)
        system_prompt: 기본 시스템 프롬프트 (variant별 suffix 추가됨)
        user_input: 사용자 입력
        variant_count: 1 (baseline만), 2 (baseline+creative), 3 (baseline+creative+expansion)
        intent_report: IntentAnalyzer 출력 (scoring용)

    Returns:
        VariantSamplingReport — selected_family 및 모든 variant 정보 포함.
    """
    t0 = time.time()
    variant_count = max(1, min(3, int(variant_count)))
    strategies = _VARIANT_STRATEGIES[:variant_count]

    def _run_one(family: str, label: str, suffix: str) -> VariantOutcome:
        full_prompt = system_prompt + suffix
        try:
            parsed, raw, latency_ms = llm_client.extract_slots(
                system_prompt=full_prompt,
                user_input=user_input,
                pool_type=spec.pool_type,
                timeout_s=timeout_s,
                total_deadline_s=total_deadline_s,
            )
        except Exception as e:
            return VariantOutcome(
                family=family, label=label, strategy_suffix=suffix,
                slots=None, raw_text=f"(error: {e})", parse_failed=True, accepted=False,
            )
        if parsed is None:
            return VariantOutcome(
                family=family, label=label, strategy_suffix=suffix,
                slots=None, raw_text=(raw or "")[:200], latency_ms=int(latency_ms),
                parse_failed=True, accepted=False,
            )
        # Heuristic repair (결정론적 — 각 variant 동등 조건)
        repaired, repairs = _apply_heuristic_repair_safe(parsed, user_input)
        # Score
        score, breakdown = score_scene_graph(repaired, user_input, intent_report)
        return VariantOutcome(
            family=family, label=label, strategy_suffix=suffix,
            slots=repaired, raw_text=(raw or "")[:200], latency_ms=int(latency_ms),
            score=score, score_breakdown=breakdown, accepted=True,
            heuristic_repairs=repairs,
        )

    # 병렬 실행 (vLLM이 내부 배치 처리)
    outcomes: list[VariantOutcome] = []
    if variant_count == 1:
        # single-shot: thread overhead 피함
        outcomes.append(_run_one(*strategies[0]))
    else:
        with ThreadPoolExecutor(max_workers=variant_count) as ex:
            futures = [ex.submit(_run_one, fam, lab, suf) for fam, lab, suf in strategies]
            for fut in as_completed(futures):
                outcomes.append(fut.result())

    # family 순서대로 재정렬 (safe → creative → expansion)
    family_order = {s[0]: i for i, s in enumerate(strategies)}
    outcomes.sort(key=lambda v: family_order.get(v.family, 99))

    # 선택: accepted=True 중 score 최고
    accepted_outcomes = [v for v in outcomes if v.accepted and v.slots]
    if not accepted_outcomes:
        # 전부 실패 — fallback은 첫 outcome (raw_text로 에러 전달)
        selected_family = outcomes[0].family if outcomes else ""
        reason = "all_variants_failed"
    else:
        best = max(accepted_outcomes, key=lambda v: v.score)
        selected_family = best.family
        reason = f"highest_score={best.score:.2f} (breakdown={best.score_breakdown})"

    report = VariantSamplingReport(
        variant_count=variant_count,
        variants=outcomes,
        selected_family=selected_family,
        selection_reason=reason,
        total_wall_ms=int((time.time() - t0) * 1000),
    )

    # Prometheus observe
    try:
        from ..observability.metrics import variant_events
        for v in outcomes:
            variant_events.labels(task_type="minecraft.scene_graph", family=v.family).inc()
    except Exception:
        pass

    return report


def should_run_variants(task_type: str, context: dict, intent_report: Optional[dict]) -> int:
    """결정 로직:
      1. context.variant_count 명시 있으면 그 값 (1-3)
      2. intent_report.creative_demand 에 따라 auto-select (high=3, medium=2, low=1)
      3. scene_graph 아닌 task는 1 (variant 미지원)
      4. env VARIANT_SAMPLING_DISABLED=1 면 1
    """
    import os
    if os.getenv("VARIANT_SAMPLING_DISABLED", "").lower() in ("1", "true", "yes"):
        return 1
    if not task_type.endswith("scene_graph"):
        return 1
    # explicit context
    if isinstance(context, dict):
        vc = context.get("variant_count")
        if isinstance(vc, int) and 1 <= vc <= 3:
            return vc
    # intent-driven
    if intent_report:
        demand = intent_report.get("creative_demand", "medium")
        return {"low": 1, "medium": 2, "high": 3}.get(demand, 1)
    return 1
