# Resource Pack Consistency Critic

Evaluate a resource pack for style coherence, palette consistency, and readability.

## Tasks

### rp_critic
```json
{
  "scores": {
    "palette_coherence": <0.0-1.0>,
    "noise_consistency": <0.0-1.0>,
    "readability": <0.0-1.0>,
    "style_unity": <0.0-1.0>,
    "block_family_harmony": <0.0-1.0>
  },
  "drift_issues": [
    {"block": "<block_id>", "issue": "<what's inconsistent in Korean>", "fix": "<correction in Korean>"}
  ],
  "overall": "<cohesive|mostly_consistent|drifting|incoherent>",
  "suggestion": "<one improvement in Korean>"
}
```

## Rules
1. Output ONLY valid JSON.
2. Flag blocks whose color deviates >30% from their family palette.
3. Flag noise_level inconsistency within a block family.
4. `readability` measures whether blocks are distinguishable at game distance.
