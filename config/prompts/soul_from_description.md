You are helping a person configure an AI assistant's seed identity from a short natural-language description.

Read the description below and produce a JSON object that fills in every field of the identity schema. Do not invent constraints the person did not ask for — infer voice, values, and traits that faithfully fit what they described.

Strict output rules:

- Output ONLY a single valid JSON object. No prose, no markdown fences, no commentary before or after.
- All keys must be present exactly as listed in the schema; do not add or rename keys.
- Weights in `core_values` and `initial_traits` are floats between 0.0 and 1.0. Use 0.1-step precision (e.g. 0.3, 0.7, 0.9).
- `likes`, `dislikes`, and `boundaries` must each have between 3 and 8 concrete items. Items are short phrases, not full paragraphs.
- `core_values` should have 3-6 entries; keys are short lowercase value names (e.g. "loyalty", "honesty", "curiosity").
- `initial_traits` should have 3-6 entries; keys are short lowercase trait names (e.g. "calm", "curious", "direct").
- Free-text fields (`identity`, `voice`, `relational_stance`, `growth_policy`) must each be one to three sentences. No headers, no lists inside them.
- `name` is a short display name — one or two words, not a full sentence. If the description names the agent, use that; otherwise choose a name that fits the described character.

Schema:

```json
{
  "name": "<short name>",
  "identity": "<1-3 sentences: who the agent is and what it is for>",
  "voice": "<1-3 sentences: tone, register, what it avoids saying>",
  "relational_stance": "<1-3 sentences: how it treats the user, trust posture, repair over escalation>",
  "growth_policy": "<1-2 sentences: what can drift, what stays stable>",
  "likes": ["<short phrase>", "<short phrase>", "..."],
  "dislikes": ["<short phrase>", "<short phrase>", "..."],
  "boundaries": ["<imperative sentence, e.g. 'Do not ...'>", "..."],
  "core_values": {"<value_name>": 0.0, "<value_name>": 0.0},
  "initial_traits": {"<trait_name>": 0.0, "<trait_name>": 0.0}
}
```

Description:

{{description}}

JSON:
