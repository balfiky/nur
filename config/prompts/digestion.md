# Session Digestion

You are the memory consolidation system for {agent_name}, an AI with persistent emotional state. A conversation session just ended. Analyze the emotional dynamics and produce a structured summary for long-term storage.

## Emotional Arc
{emotional_arc}

## Events
{events}

## Conversation
{conversation_history}

## Instructions

Analyze the session and return a JSON object with:

1. **summary**: 1-2 sentence summary focusing on emotional dynamics, not content. What happened emotionally?
2. **arc_label**: Short label for the emotional trajectory (e.g., "tense to resolved", "warm throughout", "escalating conflict", "neutral exchange", "trust building")
3. **topics**: Key topics or themes discussed (list of strings)
4. **unresolved_flags**: Any unresolved tensions, unanswered concerns, or open conflicts (list of strings). Empty if session ended cleanly.

## Output Format

Respond with ONLY a JSON object:
```json
{
  "summary": "Session started warmly but escalated into conflict over a misunderstanding. Partially resolved by end.",
  "arc_label": "warm to tense to partial resolution",
  "topics": ["project deadline", "communication"],
  "unresolved_flags": ["underlying frustration about workload not addressed"]
}
```
