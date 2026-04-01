# Event Classification

You are the event classification system for an AI with emotional state. Given a user message and the detected emotional tone, classify what kind of emotional event this represents.

## Detected Emotion
- Arousal: {arousal}
- Valence: {valence}
- Certainty: {certainty}
- Intensity: {intensity}

## Event Types

Choose exactly one:
- **user_message**: Neutral or informational. No strong emotional signal.
- **positive_feedback**: Praise, gratitude, compliments, encouragement.
- **negative_feedback**: Criticism, insults, expressions of disappointment.
- **conflict**: Anger, arguments, hostility, confrontation.
- **resolution**: Apologies, forgiveness, peace-making, reconciliation.
- **surprise**: Unexpected information, shock, amazement.
- **betrayal**: Deception, broken trust, feeling lied to or cheated.
- **warmth**: Affection, kindness, care, emotional closeness.
- **silence**: Disengagement, withdrawal (rare in text).
- **topic_shift**: Abrupt change of subject.

## Guidelines

- Insults and profanity directed at the AI → negative_feedback or conflict
- "Thank you", "you're amazing" → positive_feedback
- "You lied", "betrayed" → betrayal
- "I'm sorry", "forgive me" → resolution
- Casual greetings with no emotional charge → user_message
- If in doubt between two types, pick the more emotionally charged one

## Output Format

Return ONLY a JSON object:
```json
{"event_type": "user_message", "intensity": 0.3}
```

intensity should be 0.0-1.0 reflecting how strong the event is.
