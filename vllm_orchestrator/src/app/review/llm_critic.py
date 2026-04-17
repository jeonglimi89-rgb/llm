"""llm_critic.py — Self-critique layer.

LLM이 자기 출력을 스스로 비평해서 이슈를 찾아내는 레이어. repair 루프와
짝지어 "생성 → 비판 → 개선"이라는 단일 사고/실행/반성 사이클을 만든다.

입력:
  - task_type: e.g. "minecraft.scene_graph"
  - user_input: 원본 사용자 요청
  - slots: 생성된 output (scene_graph의 경우 nodes + concept_notes)
  - intent_report: IntentAnalyzer가 판별한 컨셉/modifiers

출력 (CritiqueReport):
  - overall_quality: 0.0~1.0
  - issues: [{severity, aspect, description, suggestion}]
  - repair_needed: bool — critical/major 이슈가 있을 때 True
  - repair_hint: LLM이 내린 한 줄 수정 방향 (repair prompt에 주입)

토큰 예산 (ctx=2048 제약):
  - critic prompt: ~600 tokens (compact)
  - scene_graph JSON summary: ~300 tokens (nodes 요약만 전달, raw JSON 아님)
  - output: 500 tokens
  = ~1400 tokens, 여유 충분.

14B 14B-AWQ에서 15-20s per critic call.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ── Critic prompts per task type ────────────────────────────────────────────

_SCENE_GRAPH_CRITIC_PROMPT = """# Scene Graph Critic

You are a strict reviewer of Minecraft scene_graph outputs. Read the SUMMARY below and identify issues.

**Output ONE JSON object. Start with `{`. No prose, no fences.**

## Schema

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [
    {"severity": "critical|major|minor", "aspect": "structure|material|spatial|theme|other", "description": "...", "suggestion": "..."}
  ],
  "repair_needed": true|false,
  "repair_hint": "one-sentence direction for the builder to improve"
}
```

## Checks (mark severity)

- **critical** (repair_needed=true): concept misinterpreted; missing required structure (castle needs keep+wall+towers; floating needs y>=15 base + earth underside); theme violation (glass in witch, deepslate in waffle).
- **major** (repair_needed=true): materials < 4 distinct; distinct (x,z) < 5; no `hollow:true` on outer_wall for enclosed structures; hollow shell but zero interior.
- **minor** (repair_needed=false if only minors): concept_notes too short; one odd material choice that still fits theme; slight size mismatch.

## Scoring

- 0.9~1.0: perfect; theme-bound, distinct materials, spatial spread, all rules met.
- 0.7~0.9: 1-2 minor issues.
- 0.5~0.7: 1 major issue.
- <0.5: critical issue or multiple majors.

Output JSON only. Be blunt — if the output is weak, say so."""


_BRAINSTORM_CRITIC_PROMPT = """# Brainstorm Critic

Review brainstorm output (visual_motifs / structural_elements / functional_spaces / material_accents / narrative_details / compose_strategy).

**Output ONE JSON. Start with `{`.**

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [{"severity": "critical|major|minor", "aspect": "...", "description": "...", "suggestion": "..."}],
  "repair_needed": true|false,
  "repair_hint": "..."
}
```

Checks: (a) does the brainstorm EXPAND the concept (not restate)? (b) 3-6 visual_motifs; 2-4 structural; 3-5 functional; 2-4 material; 2-4 narrative; compose_strategy is one sentence. (c) snake_case, ≤3 words per item. (d) theme-consistent, no mixing (e.g. modern minimal + baroque).

Output JSON only."""


_BUILDER_CRITIC_PROMPT = """# Builder / Architecture Critic

Review an architectural builder output (zoning, circulation, systems, constraints).

**Output ONE JSON. Start with `{`.**

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [{"severity": "critical|major|minor", "aspect": "zoning|circulation|compliance|wet_zone|structure|other", "description": "...", "suggestion": "..."}],
  "repair_needed": true|false,
  "repair_hint": "..."
}
```

Checks:
- **critical**: missing required rooms vs program (e.g. user asked 2-bed but plan has 1); wet zones (bath/kitchen) not clustered together; egress path absent; structural load path inconsistent.
- **major**: min room area violation (master < 10 sqm); corridor <1.0m width; fenestration misaligned with facade; unreasonable massing.
- **minor**: entrance emphasis weak; elevation rhythm monotonous; facade material not expressive.

Output JSON only."""


_ANIMATION_CRITIC_PROMPT = """# Animation / Cinematography Critic

Review an animation shot output (camera/framing/motion/style).

**Output ONE JSON. Start with `{`.**

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [{"severity": "critical|major|minor", "aspect": "continuity|framing|timing|style|identity|other", "description": "...", "suggestion": "..."}],
  "repair_needed": true|false,
  "repair_hint": "..."
}
```

Checks:
- **critical**: 180-degree rule violation; character identity drift (different face across shots); style-lock broken (line weight/palette change); camera teleports.
- **major**: framing awkward (head cropping); pace mismatch to emotion; inconsistent lens language; missing action continuity.
- **minor**: eyeline slightly off; subtle acting beat missing; timing nuance weak.

Output JSON only."""


_CAD_CRITIC_PROMPT = """# CAD / Product Design Critic

Review a CAD/product design output (parts, dimensions, manufacturability, assembly).

**Output ONE JSON. Start with `{`.**

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [{"severity": "critical|major|minor", "aspect": "dimension|manufacturability|assembly|fastening|material|other", "description": "...", "suggestion": "..."}],
  "repair_needed": true|false,
  "repair_hint": "..."
}
```

Checks:
- **critical**: dimensions physically impossible (negative/zero); incompatible materials welded; assembly order impossible; missing structural member.
- **major**: part interference (collision); undercut requiring multi-axis machining without note; fastening count too low for load; material wrong for function.
- **minor**: tolerance not specified; finish not called out; label missing.

Output JSON only."""


_GENERIC_CRITIC_PROMPT = """# Generic Output Critic

Review any structured JSON output for quality.

**Output ONE JSON. Start with `{`.**

```json
{
  "overall_quality": 0.0-1.0,
  "issues": [{"severity": "critical|major|minor", "aspect": "completeness|relevance|coherence|structure|other", "description": "...", "suggestion": "..."}],
  "repair_needed": true|false,
  "repair_hint": "..."
}
```

Be strict: incomplete → critical; off-topic → major; minor formatting → minor.

Output JSON only."""


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class CritiqueIssue:
    severity: str = "minor"       # critical | major | minor
    aspect: str = "other"         # structure | material | spatial | theme | other
    description: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CritiqueReport:
    overall_quality: float = 1.0
    issues: list[CritiqueIssue] = field(default_factory=list)
    repair_needed: bool = False
    repair_hint: str = ""
    critic_latency_ms: int = 0
    task_type: str = ""
    parse_success: bool = True    # False 면 critic LLM output이 malformed

    def to_dict(self) -> dict:
        return {
            "overall_quality": self.overall_quality,
            "issues": [i.to_dict() for i in self.issues],
            "repair_needed": self.repair_needed,
            "repair_hint": self.repair_hint,
            "critic_latency_ms": self.critic_latency_ms,
            "task_type": self.task_type,
            "parse_success": self.parse_success,
        }

    def is_usable(self) -> bool:
        """Critic이 제대로 돌아갔는가?"""
        return self.parse_success and 0.0 <= self.overall_quality <= 1.0


# ── Scene graph summarizer (ctx 절약) ───────────────────────────────────────

def _summarize_scene_graph(slots: dict) -> str:
    """scene_graph의 nodes를 compact 요약. Critic이 전체 JSON을 받을 필요 없음 —
    요약이면 충분히 비평할 수 있다."""
    if not isinstance(slots, dict):
        return "(invalid slots)"
    nodes = slots.get("nodes") or []
    if not nodes:
        return "(no nodes)"
    # primitive type 분포
    types: dict[str, int] = {}
    mats: dict[str, int] = {}
    xz_set = set()
    min_y = 999
    has_hollow_wall = False
    has_central_cyl = False
    concept_notes = (slots.get("concept_notes") or "")[:150]
    node_summaries: list[str] = []
    for n in nodes[:20]:
        pt = n.get("primitive_type", "?")
        types[pt] = types.get(pt, 0) + 1
        m = n.get("material", "")
        if m:
            mats[m] = mats.get(m, 0) + 1
        p = n.get("position")
        if isinstance(p, dict):
            x, y, z = p.get("x"), p.get("y"), p.get("z")
            xz_set.add((x, z))
            try:
                if y is not None and y < min_y:
                    min_y = y
            except TypeError:
                pass
            if pt == "cylinder" and x == 0 and z == 0:
                has_central_cyl = True
        if pt == "cuboid" and n.get("hollow") and "wall" in (n.get("id") or "").lower():
            has_hollow_wall = True
        # compact per-node line
        nid = (n.get("id") or "")[:16]
        extra = ""
        if pt == "cuboid":
            s = n.get("size", {})
            extra = f"{s.get('x')}x{s.get('y')}x{s.get('z')}"
        elif pt == "cylinder":
            extra = f"r={n.get('radius')} h={n.get('height')}"
        elif pt == "cone":
            extra = f"br={n.get('base_radius')} h={n.get('height')} t={n.get('tip_ratio')}"
        elif pt == "opening":
            s = n.get("size", {})
            extra = f"{s.get('x')}x{s.get('y')}x{s.get('z')} SUB"
        pos_str = f"({p.get('x')},{p.get('y')},{p.get('z')})" if isinstance(p, dict) else str(p)
        holl = "[H]" if n.get("hollow") else ""
        node_summaries.append(f"  {nid}: {pt} {pos_str} {extra} {m}{holl}")

    lines = [
        f"Nodes: {len(nodes)} total  Types: {types}  Materials: {list(mats.keys())} ({len(mats)} distinct)",
        f"Distinct (x,z): {len(xz_set)}  min_y: {min_y}  Hollow outer_wall: {has_hollow_wall}  Central cylinder@(0,z,0): {has_central_cyl}",
        f"Concept notes: {concept_notes!r}",
        "Node list:",
    ] + node_summaries[:20]
    if len(nodes) > 20:
        lines.append(f"  ... +{len(nodes) - 20} more")
    return "\n".join(lines)


def _summarize_brainstorm(slots: dict) -> str:
    """Brainstorm slot을 compact 요약."""
    if not isinstance(slots, dict):
        return "(invalid)"
    lines = []
    for k in ("visual_motifs", "structural_elements", "functional_spaces", "material_accents", "narrative_details"):
        v = slots.get(k) or []
        if isinstance(v, list):
            lines.append(f"{k}: [{len(v)}] {v[:6]}")
    cs = slots.get("compose_strategy") or ""
    lines.append(f"compose_strategy: {cs[:160]!r}")
    return "\n".join(lines)


def _summarize_generic(slots: dict, max_chars: int = 900) -> str:
    """타입 제약 없이 dict를 compact JSON. Critic 호출 비용 제한 위해 잘라냄."""
    if not isinstance(slots, dict):
        return f"(non-dict: {str(slots)[:200]})"
    try:
        s = json.dumps(slots, ensure_ascii=False, default=str)
    except Exception:
        s = str(slots)
    if len(s) > max_chars:
        s = s[:max_chars] + " ...[truncated]"
    return s


def _summarize_builder(slots: dict) -> str:
    if not isinstance(slots, dict):
        return "(invalid)"
    lines = []
    if "rooms" in slots and isinstance(slots["rooms"], list):
        lines.append(f"rooms: [{len(slots['rooms'])}]")
        for r in slots["rooms"][:8]:
            if isinstance(r, dict):
                lines.append(f"  - {r.get('name')} area={r.get('area_sqm')} zone={r.get('zone')}")
    for k in ("systems", "constraints", "circulation", "massing"):
        v = slots.get(k)
        if v:
            lines.append(f"{k}: {str(v)[:150]}")
    return "\n".join(lines) or _summarize_generic(slots)


def _summarize_animation(slots: dict) -> str:
    if not isinstance(slots, dict):
        return "(invalid)"
    lines = []
    if "shots" in slots and isinstance(slots["shots"], list):
        lines.append(f"shots: [{len(slots['shots'])}]")
        for s in slots["shots"][:6]:
            if isinstance(s, dict):
                lines.append(f"  - {s.get('shot_id','?')} lens={s.get('lens')} motion={s.get('motion')} framing={s.get('framing')}")
    for k in ("style_lock", "character_refs", "continuity_rules"):
        if slots.get(k):
            lines.append(f"{k}: {str(slots[k])[:150]}")
    return "\n".join(lines) or _summarize_generic(slots)


def _summarize_cad(slots: dict) -> str:
    if not isinstance(slots, dict):
        return "(invalid)"
    lines = []
    if "parts" in slots and isinstance(slots["parts"], list):
        lines.append(f"parts: [{len(slots['parts'])}]")
        for p in slots["parts"][:8]:
            if isinstance(p, dict):
                dims = p.get("estimated_dims_mm") or {}
                lines.append(f"  - {p.get('part_id')} {p.get('name')} mat={p.get('material')} dims={dims}")
    for k in ("assemblies", "fasteners", "constraints"):
        if slots.get(k):
            lines.append(f"{k}: {str(slots[k])[:150]}")
    return "\n".join(lines) or _summarize_generic(slots)


# ── Task → (prompt, summarizer) 라우팅 ──────────────────────────────────────

_TASK_CRITIC_MAP: list[tuple[tuple[str, ...], str, Any]] = [
    (("scene_graph",), _SCENE_GRAPH_CRITIC_PROMPT, _summarize_scene_graph),
    (("brainstorm",), _BRAINSTORM_CRITIC_PROMPT, _summarize_brainstorm),
    (("builder.plan", "builder.exterior", "builder.interior", "builder.requirement_parse"), _BUILDER_CRITIC_PROMPT, _summarize_builder),
    (("animation.shot_parse", "animation.creative_direction", "animation.camera_intent_parse"), _ANIMATION_CRITIC_PROMPT, _summarize_animation),
    (("cad.design", "cad.constraint", "cad.validate", "cad.constraint_parse"), _CAD_CRITIC_PROMPT, _summarize_cad),
    (("npc.npc_planner", "npc.character_parse", "resourcepack.rp_planner", "resourcepack.style_parse"), _GENERIC_CRITIC_PROMPT, _summarize_generic),
]


def _select_critic(task_type: str) -> tuple[Optional[str], Any]:
    """Returns (critic_prompt, summarizer_fn) or (None, None) if unsupported."""
    tt = task_type or ""
    for suffixes, prompt, summ in _TASK_CRITIC_MAP:
        for suf in suffixes:
            if tt.endswith(suf):
                return prompt, summ
    return None, None


# ── Parser ──────────────────────────────────────────────────────────────────

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_critic_output(raw: str) -> Optional[dict]:
    if not raw:
        return None
    match = _JSON_RE.search(raw)
    if not match:
        return None
    candidate = match.group(0)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        # 시도: trailing text 제거
        try:
            return json.loads(candidate.rsplit("}", 1)[0] + "}")
        except Exception:
            return None


# ── Public API ──────────────────────────────────────────────────────────────

def critique(
    llm_client,
    task_type: str,
    user_input: str,
    slots: dict,
    *,
    timeout_s: float = 30.0,
    total_deadline_s: Optional[float] = None,
) -> CritiqueReport:
    """LLM 자기비평 실행.

    Args:
        llm_client: LLMClient 인스턴스 (adapter가 available 상태여야 함)
        task_type: "minecraft.scene_graph" | "minecraft.brainstorm" | ...
        user_input: 원본 사용자 요청
        slots: 생성된 LLM output (parsed dict)
        timeout_s: critic call timeout (기본 30s)

    Returns:
        CritiqueReport — parse_success=False 이면 critic 결과 신뢰 불가.
    """
    import time
    t0 = time.time()

    # Task-specific prompt + summary (확장 가능한 라우터)
    critic_prompt, summarizer = _select_critic(task_type)
    if critic_prompt is None:
        # 알 수 없는 task는 critic skip (quality=1.0 기본값)
        return CritiqueReport(
            task_type=task_type,
            overall_quality=1.0,
            repair_needed=False,
            parse_success=True,
            repair_hint="(critic skipped: unsupported task_type)",
        )
    summary = summarizer(slots)

    user_msg = (
        f"User request: {user_input}\n\n"
        f"Generated output SUMMARY:\n{summary}\n\n"
        f"Critique this output. Output JSON only."
    )

    # LLM 호출 (fast_chat pool: 짧은 critic response에 적합)
    try:
        parsed, raw_text, latency_ms = llm_client.extract_slots(
            system_prompt=critic_prompt,
            user_input=user_msg,
            pool_type="creative_json",      # JSON mode + JSON-capable
            timeout_s=timeout_s,
            total_deadline_s=total_deadline_s,
        )
    except Exception as e:
        return CritiqueReport(
            task_type=task_type,
            overall_quality=1.0,    # critic 실패 시 원본 통과 (보수적)
            repair_needed=False,
            parse_success=False,
            repair_hint=f"(critic call failed: {e})",
            critic_latency_ms=int((time.time() - t0) * 1000),
        )

    elapsed_ms = int((time.time() - t0) * 1000)

    if parsed is None:
        # 2차: raw_text에서 JSON 재시도
        parsed = _parse_critic_output(raw_text)

    if not isinstance(parsed, dict):
        return CritiqueReport(
            task_type=task_type,
            overall_quality=1.0,
            repair_needed=False,
            parse_success=False,
            repair_hint="(critic output not valid JSON)",
            critic_latency_ms=elapsed_ms,
        )

    try:
        oq = float(parsed.get("overall_quality", 1.0))
    except (TypeError, ValueError):
        oq = 1.0
    oq = max(0.0, min(1.0, oq))

    issues_raw = parsed.get("issues") or []
    issues: list[CritiqueIssue] = []
    valid_severities = {"critical", "major", "minor"}
    if isinstance(issues_raw, list):
        for it in issues_raw[:10]:
            if not isinstance(it, dict):
                continue
            sev = str(it.get("severity", "minor")).lower()
            if sev not in valid_severities:
                sev = "minor"  # 임의의 severity 문자열 → 보수적으로 minor로 격하
            desc = str(it.get("description", ""))[:300]
            sug = str(it.get("suggestion", ""))[:300]
            # 빈 description은 no-op 이슈 → 버림
            if not desc.strip():
                continue
            issues.append(CritiqueIssue(
                severity=sev,
                aspect=str(it.get("aspect", "other")).lower()[:40],
                description=desc,
                suggestion=sug,
            ))

    # ── Self-validation (hallucination guard) ────────────────────────────
    # 1. issue.description/suggestion 이 실제 slots에 존재하지 않는 ID / material 을
    #    '반드시' 참조하는 경우, critic이 존재하지 않는 걸 비판하는 hallucination 의심.
    #    — 완전 배제는 과민하므로 quality 감점만 적용 (rebuttal).
    # 2. 만약 critical/major인데 overall_quality >= 0.8 이면 내부 모순 → 적절히 감점.
    validated_issues = issues
    hallucination_penalty = 0.0
    if issues and isinstance(slots, dict):
        known_ids, known_mats = _extract_known_tokens(slots)
        for issue in issues:
            text = (issue.description + " " + issue.suggestion).lower()
            # heuristic: backtick-quoted identifier-like tokens
            suspicious = _find_suspicious_tokens(text)
            unknown_refs = [
                tok for tok in suspicious
                if tok not in known_ids and tok not in known_mats and len(tok) > 2
            ]
            if len(unknown_refs) >= 2:
                # critic이 존재하지 않는 토큰을 2개 이상 지적 → 감점
                hallucination_penalty += 0.05 * min(3, len(unknown_refs))

    # 일관성: critical/major 있는데 quality가 높은 경우 보정
    has_critical_or_major = any(i.severity in ("critical", "major") for i in issues)
    if has_critical_or_major and oq >= 0.85:
        # critic 내부 모순 — quality를 현실적으로 낮춤
        oq = 0.65

    # Hallucination penalty 반영 (최저 0)
    oq = max(0.0, oq - hallucination_penalty)

    repair_needed_raw = parsed.get("repair_needed")
    if repair_needed_raw is not None:
        repair_needed = bool(repair_needed_raw)
    else:
        repair_needed = has_critical_or_major
    # 추가 안전장치: quality < 0.5 이면 강제 repair 필요
    if oq < 0.5 and not repair_needed:
        repair_needed = True

    return CritiqueReport(
        task_type=task_type,
        overall_quality=round(oq, 3),
        issues=validated_issues,
        repair_needed=repair_needed,
        repair_hint=str(parsed.get("repair_hint", ""))[:300],
        critic_latency_ms=elapsed_ms,
        parse_success=True,
    )


# ── Hallucination guard helpers ────────────────────────────────────────────

def _extract_known_tokens(slots: dict) -> tuple[set[str], set[str]]:
    """slots에서 실제로 등장하는 id/material/primitive_type 토큰 집합 반환."""
    ids: set[str] = set()
    mats: set[str] = set()
    nodes = slots.get("nodes") if isinstance(slots, dict) else None
    if isinstance(nodes, list):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            nid = n.get("id")
            if isinstance(nid, str):
                ids.add(nid.lower())
            mat = n.get("material")
            if isinstance(mat, str) and mat:
                mats.add(mat.lower())
    # 모든 top-level string value도 수집 (builder/anim/cad 용)
    def _walk(x):
        if isinstance(x, dict):
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)
        elif isinstance(x, str):
            # 식별자같은 짧은 단어만 추출
            for tok in re.findall(r"[a-z][a-z0-9_]{2,}", x.lower()):
                ids.add(tok)
    if isinstance(slots, dict):
        _walk(slots)
    return ids, mats


def _find_suspicious_tokens(text: str) -> list[str]:
    """Critic이 언급한 identifier-like 토큰 (backtick이나 quote로 감싸진 것 우선)."""
    suspicious: list[str] = []
    # backtick-quoted
    for m in re.findall(r"`([a-z][a-z0-9_]{2,30})`", text):
        suspicious.append(m.lower())
    # single-quoted
    for m in re.findall(r"'([a-z][a-z0-9_]{2,30})'", text):
        suspicious.append(m.lower())
    # double-quoted
    for m in re.findall(r'"([a-z][a-z0-9_]{2,30})"', text):
        suspicious.append(m.lower())
    return suspicious


def critic_enabled_for(task_type: str, user_input: str = "") -> bool:
    """Task별 critic 라우터가 매치하는지 확인. scene_graph / brainstorm /
    builder.plan / animation.shot_parse / cad.design / npc.* / resourcepack.* 등 지원."""
    prompt, _ = _select_critic(task_type)
    return prompt is not None
