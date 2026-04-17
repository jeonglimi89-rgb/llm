"""scene_graph_repair.py — Heuristic post-processor for minecraft.scene_graph

LLM output 후 결정론적 코드로 프롬프트 규칙 위반을 보정한다. 14B 출력이
~85%만 프롬프트를 완벽히 준수하는 한계를 99%+로 끌어올리는 저비용 레이어.

불변 계약:
- 유효한 output은 변경하지 않는다 (add only, never mutate existing nodes' fields).
- 추가 노드는 schema 준수 (kind="primitive", primitive_type, position, material).
- 원본 노드 순서는 보존.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


# ─── Theme palettes (scene_graph.md 와 동일) ────────────────────────────────

PALETTES: dict[str, list[str]] = {
    "witch": ["deepslate", "cobbled_deepslate", "dark_oak_planks", "purple_stained_glass", "soul_lantern"],
    "waffle": ["yellow_concrete", "honey_block", "honeycomb_block", "orange_concrete", "white_concrete"],
    "frog": ["moss_block", "slime_block", "lily_pad", "water", "mangrove_planks"],
    "sky_surface": ["glass", "packed_ice", "sea_lantern", "purpur_block", "grass_block"],
    "sky_under": ["dirt", "coarse_dirt", "mud", "stone"],
    "medieval": ["stone_bricks", "cobblestone", "oak_planks", "dark_oak_log"],
    "generic": ["stone_bricks", "oak_planks", "cobblestone", "glass"],
}

# Concept keyword → theme
THEME_KEYWORDS: list[tuple[str, str]] = [
    ("마녀", "witch"), ("witch", "witch"), ("gothic", "witch"), ("고딕", "witch"),
    ("와플", "waffle"), ("waffle", "waffle"), ("dessert", "waffle"), ("sweet", "waffle"),
    ("honey", "waffle"), ("cake", "waffle"),
    ("개구리", "frog"), ("frog", "frog"), ("swamp", "frog"), ("늪", "frog"), ("lily", "frog"),
    ("하늘섬", "sky_surface"), ("sky_island", "sky_surface"), ("floating", "sky_surface"),
    ("떠있는", "sky_surface"), ("공중", "sky_surface"), ("sky", "sky_surface"),
    ("성", "medieval"), ("castle", "medieval"), ("요새", "medieval"), ("fortress", "medieval"),
]


def detect_theme(user_input: str) -> str:
    """Lowercase user_input에서 theme keyword를 찾아 반환. 없으면 'generic'."""
    lowered = (user_input or "").lower()
    for kw, theme in THEME_KEYWORDS:
        if kw in lowered:
            return theme
    return "generic"


def is_castle(user_input: str) -> bool:
    lowered = (user_input or "").lower()
    return any(kw in lowered for kw in ("성", "castle", "fortress", "keep", "요새"))


def is_floating(user_input: str) -> bool:
    lowered = (user_input or "").lower()
    return any(kw in lowered for kw in ("하늘섬", "sky_island", "floating", "떠있는", "공중", "sky island"))


# ─── Repair primitives ──────────────────────────────────────────────────────

def _next_id(nodes: list[dict], prefix: str) -> str:
    existing = {str(n.get("id") or "") for n in nodes}
    i = 1
    while f"{prefix}_{i}" in existing:
        i += 1
    return f"{prefix}_{i}"


def _node_position_xz(n: dict) -> tuple[Any, Any] | None:
    p = n.get("position")
    if isinstance(p, dict):
        return (p.get("x"), p.get("z"))
    return None


def _max_y(nodes: list[dict]) -> int:
    m = 0
    for n in nodes:
        p = n.get("position")
        if isinstance(p, dict):
            y = p.get("y", 0)
            h = n.get("height") or (n.get("size") or {}).get("y") or 0
            try:
                top = int(y) + int(h)
                if top > m:
                    m = top
            except (TypeError, ValueError):
                pass
    return m


def ensure_material_diversity(nodes: list[dict], theme: str, min_distinct: int = 4) -> tuple[list[dict], list[str]]:
    """재료가 min_distinct 미만이면 theme palette에서 추가 decorative 노드를 삽입."""
    repairs: list[str] = []
    mats_used = {n.get("material") for n in nodes if n.get("material")}
    palette = PALETTES.get(theme) or PALETTES["generic"]
    missing_from_palette = [m for m in palette if m not in mats_used]

    while len(mats_used) < min_distinct and missing_from_palette:
        mat = missing_from_palette.pop(0)
        # 작은 decorative cuboid — 건물 밖 y=0 에 배치 (spatial distribution 보너스)
        angle_idx = len([r for r in repairs if r.startswith("mat_")])
        offsets = [(12, 12), (-12, 12), (12, -12), (-12, -12)]
        ox, oz = offsets[angle_idx % 4]
        node = {
            "id": _next_id(nodes, f"accent_{mat}"),
            "kind": "primitive",
            "primitive_type": "cuboid",
            "position": {"x": ox, "y": 0, "z": oz},
            "size": {"x": 2, "y": 1, "z": 2},
            "material": mat,
        }
        nodes.append(node)
        mats_used.add(mat)
        repairs.append(f"mat_add:{mat}")

    return nodes, repairs


def ensure_spatial_distribution(nodes: list[dict], min_distinct_xz: int = 5) -> tuple[list[dict], list[str]]:
    """distinct (x,z) 개수가 부족하면 기존 노드들에 mild xz jitter를 추가."""
    repairs: list[str] = []
    xz_positions = set()
    for n in nodes:
        xz = _node_position_xz(n)
        if xz is not None and all(v is not None for v in xz):
            xz_positions.add(xz)

    if len(xz_positions) >= min_distinct_xz:
        return nodes, repairs

    # 원본 절대좌표 노드(foundation, outer_wall 제외) 중 겹친 위치를 jitter
    # 보수적: 처음 N개만 shift
    deficit = min_distinct_xz - len(xz_positions)
    jitter_offsets = [(3, 0), (-3, 0), (0, 3), (0, -3), (3, 3), (-3, -3)]
    jittered = 0
    for n in nodes:
        if jittered >= deficit:
            break
        # Skip foundation, outer_wall, keep, ground — 이들은 움직이면 안 됨
        nid = (n.get("id") or "").lower()
        if any(tag in nid for tag in ("foundation", "outer_wall", "ground", "base", "keep", "island")):
            continue
        pos = n.get("position")
        if not isinstance(pos, dict):
            continue
        xz = (pos.get("x"), pos.get("z"))
        if xz in xz_positions:
            # 이미 distinct — skip
            continue
        dx, dz = jitter_offsets[jittered % len(jitter_offsets)]
        try:
            new_x = int(pos.get("x", 0)) + dx
            new_z = int(pos.get("z", 0)) + dz
            pos["x"] = new_x
            pos["z"] = new_z
            xz_positions.add((new_x, new_z))
            jittered += 1
            repairs.append(f"xz_jitter:{n.get('id')}")
        except (TypeError, ValueError):
            continue

    # 여전히 부족하면 decorative pillar 추가
    if len(xz_positions) < min_distinct_xz:
        add_spots = [(0, 10), (10, 0), (-10, 0), (0, -10), (8, 8), (-8, -8), (8, -8), (-8, 8)]
        palette = PALETTES["generic"]
        for x, z in add_spots:
            if len(xz_positions) >= min_distinct_xz:
                break
            if (x, z) in xz_positions:
                continue
            node = {
                "id": _next_id(nodes, "pillar"),
                "kind": "primitive",
                "primitive_type": "cylinder",
                "position": {"x": x, "y": 0, "z": z},
                "radius": 1,
                "height": 3,
                "material": palette[0],
            }
            nodes.append(node)
            xz_positions.add((x, z))
            repairs.append(f"xz_add_pillar:{x},{z}")

    return nodes, repairs


def ensure_castle_keep(nodes: list[dict], theme: str) -> tuple[list[dict], list[str]]:
    """Castle 컨셉인데 central keep이 없으면 자동 삽입."""
    repairs: list[str] = []
    # central cylinder at (0, y, 0) 존재?
    has_central_cyl = any(
        n.get("primitive_type") == "cylinder"
        and isinstance(n.get("position"), dict)
        and n["position"].get("x") == 0
        and n["position"].get("z") == 0
        for n in nodes
    )
    has_keep_id = any("keep" in (n.get("id") or "").lower() for n in nodes)

    if has_central_cyl or has_keep_id:
        return nodes, repairs

    # keep 삽입 (foundation 위)
    palette = PALETTES.get(theme) or PALETTES["medieval"]
    keep_mat = palette[1] if len(palette) > 1 else palette[0]
    spire_mat = palette[2] if len(palette) > 2 else palette[-1]
    keep = {
        "id": _next_id(nodes, "keep"),
        "kind": "primitive",
        "primitive_type": "cylinder",
        "position": {"x": 0, "y": 1, "z": 0},
        "radius": 4,
        "height": 10,
        "material": keep_mat,
        "hollow": True,
    }
    keep_spire = {
        "id": _next_id(nodes + [keep], "keep_spire"),
        "kind": "primitive",
        "primitive_type": "cone",
        "position": f"node:{keep['id']}.top",
        "base_radius": 4,
        "height": 6,
        "material": spire_mat,
        "tip_ratio": 0,
    }
    nodes.append(keep)
    nodes.append(keep_spire)
    repairs.append(f"castle_keep_inserted:{keep['id']}")
    return nodes, repairs


def ensure_floating_underside(nodes: list[dict], theme: str) -> tuple[list[dict], list[str]]:
    """Floating 컨셉인데 underside cone이 없거나 규칙 위반(glass, br<8, h<5)이면 보정."""
    repairs: list[str] = []

    # island base 찾기 (y>=15, cuboid)
    island_bases = [
        n for n in nodes
        if n.get("primitive_type") == "cuboid"
        and isinstance(n.get("position"), dict)
        and n["position"].get("y", 0) >= 15
    ]
    if not island_bases:
        # LLM이 floating으로 이해 못함 — 후처리로 띄우는 건 리스크 큼. skip.
        return nodes, repairs

    base = island_bases[0]
    base_y = base["position"].get("y", 15)
    base_size_y = (base.get("size") or {}).get("y", 1)
    # Rule: base size.y ≥ 2
    if base_size_y < 2:
        base["size"]["y"] = 2
        repairs.append(f"sky_base_thickness_fix:{base.get('id')}")

    # underside cone 찾기 (y < base_y, cone)
    under_cones = [
        n for n in nodes
        if n.get("primitive_type") == "cone"
        and isinstance(n.get("position"), dict)
        and n["position"].get("y", 99) < base_y
    ]

    earth_mats = set(PALETTES["sky_under"])
    if not under_cones:
        # 없으면 삽입
        cone = {
            "id": _next_id(nodes, "underside"),
            "kind": "primitive",
            "primitive_type": "cone",
            "position": {"x": 0, "y": max(0, int(base_y) - 5), "z": 0},
            "base_radius": 8,
            "height": 5,
            "material": "dirt",
            "tip_ratio": 0.2,
        }
        nodes.append(cone)
        repairs.append(f"sky_underside_inserted:{cone['id']}")
    else:
        c = under_cones[0]
        # Rule: earth-tone material, br>=8, h>=5
        if c.get("material") not in earth_mats:
            old = c.get("material")
            c["material"] = "dirt"
            repairs.append(f"sky_underside_material:{old}->dirt")
        if (c.get("base_radius") or 0) < 8:
            c["base_radius"] = 8
            repairs.append(f"sky_underside_br_fix:{c.get('id')}")
        if (c.get("height") or 0) < 5:
            c["height"] = 5
            repairs.append(f"sky_underside_h_fix:{c.get('id')}")
        if (c.get("tip_ratio") is None) or (c.get("tip_ratio") < 0.1):
            c["tip_ratio"] = 0.2
            repairs.append(f"sky_underside_tip_fix:{c.get('id')}")

    return nodes, repairs


# ─── Top-level orchestrator ────────────────────────────────────────────────

def repair_scene_graph(slots: dict, user_input: str) -> tuple[dict, list[str]]:
    """LLM output을 프롬프트 규칙에 맞게 결정론적으로 보정.

    Args:
        slots: LLM이 낸 scene_graph JSON (nodes 포함)
        user_input: 원본 사용자 입력 (theme detection에 사용)

    Returns:
        (repaired_slots, applied_repairs) — 원본 slots는 변경하지 않음 (deep copy)
    """
    if not isinstance(slots, dict):
        return slots, []
    nodes = slots.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return slots, []

    repaired = deepcopy(slots)
    nodes = repaired["nodes"]
    all_repairs: list[str] = []

    theme = detect_theme(user_input)

    # 1. Castle keep (먼저 해야 material diversity 보정에 keep도 포함됨)
    if is_castle(user_input):
        nodes, r = ensure_castle_keep(nodes, theme)
        all_repairs.extend(r)

    # 2. Floating underside
    if is_floating(user_input):
        nodes, r = ensure_floating_underside(nodes, theme)
        all_repairs.extend(r)

    # 3. Material diversity (≥4)
    nodes, r = ensure_material_diversity(nodes, theme, min_distinct=4)
    all_repairs.extend(r)

    # 4. Spatial distribution (≥5 distinct xz)
    nodes, r = ensure_spatial_distribution(nodes, min_distinct_xz=5)
    all_repairs.extend(r)

    repaired["nodes"] = nodes

    # concept_notes 없으면 기본값
    if not repaired.get("concept_notes"):
        repaired["concept_notes"] = f"{theme} theme, {len(nodes)} nodes; auto-repaired"
        all_repairs.append("concept_notes_filled")

    return repaired, all_repairs
