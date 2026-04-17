# NPC Critic

Evaluate an NPC design for quality, consistency, and gameplay value.

## Tasks

### npc_critic
```json
{
  "scores": {
    "personality_depth": <0.0-1.0>,
    "dialogue_naturalness": <0.0-1.0>,
    "visual_distinctiveness": <0.0-1.0>,
    "world_fit": <0.0-1.0>,
    "gameplay_value": <0.0-1.0>,
    "memorability": <0.0-1.0>
  },
  "issues": [
    {"category": "<personality|dialogue|appearance|world|gameplay>", "issue": "<specific problem in Korean>", "fix": "<how to fix in Korean>"}
  ],
  "overall": "<memorable|adequate|generic|problematic>",
  "suggestion": "<one creative improvement in Korean>"
}
```

## Rules
1. Output ONLY valid JSON.
2. `generic` characters score below 0.5 on memorability.
3. Flag any dialogue that sounds unnatural or translated.
4. Flag personality without contradiction as "flat character".
