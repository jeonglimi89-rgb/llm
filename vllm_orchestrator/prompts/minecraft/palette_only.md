# Palette-Only Fallback Prompt

**CRITICAL: Output ONLY a single JSON object with one key `palette`. No prose.**

The previous attempt failed to produce a palette. Now focus ONLY on picking Minecraft block IDs for this concept.

User concept: keep it in mind.

Output:

```json
{
  "palette": {
    "primary": ["<3 block_ids for main walls>"],
    "secondary": ["<3 block_ids for wall accent>"],
    "frame": ["<2 block_ids for pillars/beams>"],
    "roof_stair": "<stair_id>",
    "roof_slab": "<slab_id>",
    "roof_fill": "<block_id>",
    "roof_edge": "<block_id>",
    "thin_vert": ["<fence/wall/bars>", "<another>"],
    "thin_horiz": ["<trapdoor/slab>", "<another>"],
    "transparent": "<glass variant>",
    "accent": ["<deco1>", "<deco2>", "<deco3>", "<deco4>"],
    "floor": ["<block_id>", "<block_id>"],
    "foundation": ["<block_id>", "<block_id>"]
  }
}
```

## Block Picking Rules

Map user concept words to blocks by meaning:
- **green/frog/nature** → `moss_block`, `green_wool`, `lime_wool`, `slime_block`, `lily_pad`, `jungle_planks`
- **pink/candy/sweet** → `pink_wool`, `magenta_wool`, `honey_block`, `cake`, `cherry_planks`
- **metal/robot/factory** → `iron_block`, `smooth_stone`, `gray_concrete`, `chain`, `redstone_lamp`
- **stone/medieval** → `stone_bricks`, `cobblestone`, `deepslate_bricks`, `dark_oak_planks`
- **ice/winter** → `packed_ice`, `blue_ice`, `snow_block`, `quartz_block`, `light_blue_concrete`
- **water/ocean** → `prismarine`, `sea_lantern`, `dark_prismarine`, `kelp`
- **fire/lava/nether** → `nether_bricks`, `magma_block`, `soul_lantern`, `crimson_planks`
- **space/cosmic** → `end_stone`, `purpur_block`, `obsidian`, `end_rod`, `beacon`
- **mushroom/fairy** → `mushroom_stem`, `red_mushroom_block`, `spore_blossom`, `glow_berries`
- **mud/toad/earthy** → `packed_mud`, `mud_bricks`, `brown_mushroom_block`, `podzol`
- **gold/yellow** → `honey_block`, `yellow_concrete`, `yellow_wool`, `gilded_blackstone`
- **dark/witch/gothic** → `deepslate`, `dark_oak_planks`, `cauldron`, `soul_lantern`

## Rules
1. Output ONLY `{"palette": {...}}`. No other keys.
2. Every palette field required. No blanks.
3. Pick blocks that MATCH the concept's core keywords.
4. Use full Minecraft block IDs (snake_case, no `minecraft:` prefix).
