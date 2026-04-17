# Minecraft Build Associative Brainstorm

**Output ONE JSON object matching schema. Start with `{`. No prose, no fences.**

Think like a human designer — make rich associations before committing to a shape. For the user concept (+ optional `[Context]` with `base_type`/`base_style`), generate connected associations across 6 dimensions. Don't restate the concept; **expand it** through lateral association.

## Schema

```json
{
  "visual_motifs": ["...3-6"],
  "structural_elements": ["...2-4"],
  "functional_spaces": ["...3-5"],
  "material_accents": ["...2-4"],
  "narrative_details": ["...2-4"],
  "compose_strategy": "single sentence composition plan"
}
```

## Dimensions

1. **visual_motifs** (3-6): silhouette/symbol keywords. e.g. `twisted_spire`, `crescent_moon_window`.
2. **structural_elements** (2-4): building structure pieces. e.g. `asymmetric_wing`, `hidden_cellar`.
3. **functional_spaces** (3-5): interior functions. e.g. `brewing_room`, `raven_aviary`.
4. **material_accents** (2-4): material/lighting mood (NOT specific blocks). e.g. `purple_glow`, `soul_lanterns`.
5. **narrative_details** (2-4): story touches. e.g. `overgrown_moss`, `bubbling_cauldron`.
6. **compose_strategy**: one sentence "where to put what". e.g. `"중앙 첨탑 + 좌측 양조 별채 + 뒤뜰 약초밭"`.

## Rules

- **Expand, don't restate**: "마녀의 성" → NOT `witch_castle`; DO use 솥/까마귀/약초/달빛/뒤틀림.
- **Novel concepts**: "달팽이 집" → 나선 껍질/점액/더듬이 → `spiral_shell_tower`, `glistening_trail_path`, `moss_cover`.
- **Honor context**: `base_type` = building category hint; `base_style` = tone/era hint. Reflect them but still add novel motifs.
- **Compatibility**: don't mix conflicting styles (minimal modern + baroque ornament = NO).
- **Tokens**: each list item ≤3 words, snake_case. Only `compose_strategy` is a sentence.

## Example — "마녀의 성" (base_type=castle, base_style=dark fantasy)

```json
{
  "visual_motifs": ["twisted_pointed_spire", "crescent_moon_cutout_window", "gnarled_root_buttress", "witch_hat_tower_cap"],
  "structural_elements": ["asymmetric_left_wing", "hidden_underground_brewery", "spiral_stone_staircase"],
  "functional_spaces": ["brewing_room", "raven_aviary", "herb_drying_garden", "potion_cellar"],
  "material_accents": ["purple_glass_glow", "crying_obsidian_drip", "soul_lanterns"],
  "narrative_details": ["overgrown_creeping_moss", "bubbling_cauldron_in_courtyard", "scattered_spellbook_piles"],
  "compose_strategy": "중앙 뒤틀린 높은 첨탑 + 좌측 비대칭 양조 별채 + 지하 양조실 + 뒤뜰 약초밭과 솥 마당"
}
```

Read the user concept (+ `[Context]` if present). Make associative leaps. Output ONE JSON object. JSON only.
