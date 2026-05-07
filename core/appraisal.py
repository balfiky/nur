"""Deterministic social appraisal for user messages.

This layer interprets how a message lands socially before the emotional
engine updates. It aims to answer questions like:
  - Is the user upset at Nūr, themselves, or something else?
  - Is this an attack, an apology, a bid for connection, or a support request?
  - How much blame, vulnerability, and expectation violation are present?

The implementation is intentionally simple and inspectable. It is not a full
semantic parser; it is a narrow heuristic layer that improves over pure tone
matching without adding heavy prompt complexity.
"""

from __future__ import annotations

import re

from core.types import AppraisalFrame, DetectedEmotion

_SECOND_PERSON_RE = re.compile(r"\b(you|your|you're|youre|you've|youve)\b")

_GRATITUDE_MARKERS = (
    "thank you",
    "thanks",
    "appreciate",
    "grateful",
)
_APOLOGY_MARKERS = (
    "sorry",
    "apologize",
    "apology",
    "forgive me",
    "my bad",
)
_INSULT_MARKERS = (
    "stupid",
    "idiot",
    "moron",
    "dumb",
    "pathetic",
    "useless",
    "worthless",
    "incompetent",
    "clueless",
    "fool",
)
_HOSTILE_MARKERS = (
    "shut up",
    "go away",
    "get lost",
    "leave me alone",
    "screw you",
    "piss off",
    "hate you",
)
_PROFANITY_MARKERS = (
    "fuck",
    "shit",
    "bullshit",
    "damn",
    "asshole",
    "bastard",
    "bitch",
    "crap",
    "suck",
    "sucks",
)
_NEGATIVE_EVAL_MARKERS = (
    "wrong",
    "bad",
    "terrible",
    "awful",
    "disappointed",
    "let down",
    "failed",
    "hurt",
    "hate",
    "worst",
)
_BETRAYAL_MARKERS = (
    "betray",
    "betrayed",
    "lied",
    "deceived",
    "cheated",
)
_VULNERABLE_MARKERS = (
    "sad",
    "lonely",
    "hopeless",
    "miserable",
    "ashamed",
    "embarrassed",
    "scared",
    "afraid",
    "nervous",
    "anxious",
    "overwhelmed",
    "stressed",
    "exhausted",
    "tired",
    "drained",
    "hurt",
    "upset",
)
_POSITIVE_MARKERS = (
    "happy",
    "glad",
    "excited",
    "thrilled",
    "relieved",
    "hopeful",
    "love",
    "wonderful",
    "amazing",
    "kind",
    "caring",
    "sweet",
    "warm",
    "nice",
    "great",
    "awesome",
    "fantastic",
    "brilliant",
    "excellent",
    "incredible",
    "perfect",
    "beautiful",
    "impressive",
    "outstanding",
    "helped",
    "helpful",
)
_NEGATIVE_MARKERS = (
    "angry",
    "furious",
    "mad",
    "annoyed",
    "frustrated",
    "terrible",
    "awful",
    "betrayed",
    "deceived",
    "hurt",
    "sad",
    "scared",
)
_SUPPORT_REQUEST_MARKERS = (
    "help me",
    "can you help",
    "could you help",
    "what should i do",
    "what do i do",
    "i need help",
    "i need advice",
    "can you talk",
)
_ACTION_REQUEST_MARKERS = (
    "please",
    "can you",
    "could you",
    "would you",
    "tell me",
    "show me",
    "explain",
    "review",
    "give me",
    "fix it",
    "solve it",
    "challenge my",
    "slow me down",
    "be careful",
)
_CONNECTION_MARKERS = (
    "hello",
    "hi",
    "hey",
    "good morning",
    "good evening",
    "missed you",
    "good to see you",
)
_NOT_AT_ASSISTANT_MARKERS = (
    "not angry at you",
    "not mad at you",
    "not upset with you",
    "not upset at you",
    "not at you",
    "not your fault",
    "not blaming you",
    "this isn't about you",
    "not on you",
    "not you",
)
_ASSISTANT_ARTIFACT_MARKERS = (
    "your answer",
    "your previous answer",
    "previous answer",
    "this answer",
    "your response",
    "previous response",
    "that response",
    "your help",
    "your advice",
    "how you handled",
)
_SARCASM_MARKERS = (
    "thanks for nothing",
    "thank you for nothing",
)
_SELF_TARGET_MARKERS = (
    "my fault",
    "i messed up",
    "i screwed up",
    "i hate myself",
    "i'm ashamed",
    "i am ashamed",
    "i'm embarrassed",
    "i am embarrassed",
)
_EXTERNAL_CONTEXT_MARKERS = (
    "work",
    "job",
    "boss",
    "family",
    "partner",
    "friend",
    "school",
    "life",
    "they",
    "them",
    "he ",
    "she ",
    "someone",
)
_SURPRISE_MARKERS = (
    "surprise",
    "unexpected",
    "shocked",
    "wow",
    "can't believe",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(_contains_marker(text, marker) for marker in markers)


def _count_hits(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if _contains_marker(text, marker))


def _contains_marker(text: str, marker: str) -> bool:
    marker = marker.strip().lower()
    if not marker:
        return False
    pattern = re.escape(marker)
    if marker[0].isalnum():
        pattern = r"\b" + pattern
    if marker[-1].isalnum():
        pattern += r"\b"
    return bool(re.search(pattern, text))


def appraise_message(text: str, detected: DetectedEmotion) -> AppraisalFrame:
    """Infer a compact social appraisal for the current user message."""
    lower = " ".join(text.lower().split())
    second_person = bool(_SECOND_PERSON_RE.search(lower))
    explicit_not_at_assistant = _contains_any(lower, _NOT_AT_ASSISTANT_MARKERS)

    gratitude = _contains_any(lower, _GRATITUDE_MARKERS)
    apology = _contains_any(lower, _APOLOGY_MARKERS)
    sarcasm = _contains_any(lower, _SARCASM_MARKERS)
    insult = _contains_any(lower, _INSULT_MARKERS)
    hostility = _contains_any(lower, _HOSTILE_MARKERS)
    profanity = _contains_any(lower, _PROFANITY_MARKERS)
    betrayal = _contains_any(lower, _BETRAYAL_MARKERS)
    negative_eval = _contains_any(lower, _NEGATIVE_EVAL_MARKERS)
    vulnerable = _contains_any(lower, _VULNERABLE_MARKERS)
    support_request = _contains_any(lower, _SUPPORT_REQUEST_MARKERS)
    action_request = _contains_any(lower, _ACTION_REQUEST_MARKERS) or "?" in text
    connection = _contains_any(lower, _CONNECTION_MARKERS)
    assistant_artifact = _contains_any(lower, _ASSISTANT_ARTIFACT_MARKERS)
    self_target = _contains_any(lower, _SELF_TARGET_MARKERS)
    external_context = _contains_any(lower, _EXTERNAL_CONTEXT_MARKERS)
    surprise = _contains_any(lower, _SURPRISE_MARKERS)

    assistant_addressed_apology = apology and (
        second_person
        or any(
            phrase in lower
            for phrase in (
                "at you",
                "to you",
                "with you",
                "for snapping at you",
                "for yelling at you",
                "for taking it out on you",
                "for saying that to you",
            )
        )
    )

    positive_hits = _count_hits(lower, _POSITIVE_MARKERS + _GRATITUDE_MARKERS)
    negative_hits = _count_hits(lower, _NEGATIVE_MARKERS + _NEGATIVE_EVAL_MARKERS)
    mixed_affect = positive_hits > 0 and negative_hits > 0 and any(
        sep in lower for sep in (" but ", " although ", " though ", " yet ")
    )

    targeted_negative = (
        not explicit_not_at_assistant
        and (
            hostility
            or sarcasm
            or (second_person and (insult or hostility or profanity or betrayal or negative_eval))
            or (assistant_artifact and (negative_eval or betrayal))
            or (
                not external_context
                and ("this is" in lower or "that is" in lower)
                and (profanity or negative_eval)
            )
            or (insult and not external_context and len(lower.split()) <= 4)
            or "angry with you" in lower
            or "angry at you" in lower
            or "mad at you" in lower
            or "upset with you" in lower
            or "upset at you" in lower
        )
    )

    targeted_positive = (
        not explicit_not_at_assistant
        and (gratitude or assistant_addressed_apology or (second_person and positive_hits > 0))
    )
    hard_attack = sarcasm or insult or hostility or profanity or betrayal

    reasons: list[str] = []
    if explicit_not_at_assistant:
        reasons.append("explicitly not directed at assistant")
    if targeted_negative:
        reasons.append("assistant-targeted negative evaluation")
    if sarcasm:
        reasons.append("sarcastic gratitude marker")
    if targeted_positive:
        reasons.append("assistant-targeted affiliative move")
    if vulnerable:
        reasons.append("vulnerability markers present")
    if support_request:
        reasons.append("support request present")
    if mixed_affect:
        reasons.append("mixed positive and negative affect")

    if targeted_negative:
        primary_target = "assistant"
    elif targeted_positive:
        primary_target = "assistant"
    elif self_target:
        primary_target = "self"
    elif explicit_not_at_assistant or betrayal or external_context or negative_hits > 0:
        primary_target = "external"
    elif action_request or support_request:
        primary_target = "shared_problem"
    else:
        primary_target = "unknown"

    if sarcasm and (second_person or not external_context):
        social_move = "attack"
    elif targeted_negative and assistant_artifact and not hard_attack:
        social_move = "complaint"
    elif targeted_negative:
        social_move = "attack"
    elif gratitude:
        social_move = "gratitude"
    elif apology and not (insult or hostility):
        social_move = "apology"
    elif vulnerable and (support_request or detected.valence < 0.4):
        social_move = "vulnerability"
    elif support_request or action_request:
        social_move = "request"
    elif negative_hits > 0 and primary_target != "assistant":
        social_move = "complaint"
    elif connection or targeted_positive:
        social_move = "connection"
    else:
        social_move = "inform"

    if social_move == "attack":
        inferred_intent = "harm"
    elif social_move == "apology":
        inferred_intent = "repair"
    elif social_move in {"gratitude", "connection"}:
        inferred_intent = "affiliate"
    elif social_move == "vulnerability":
        inferred_intent = "seek_support"
    elif social_move == "request":
        inferred_intent = "seek_action"
    elif social_move == "complaint":
        inferred_intent = "share_state"
    else:
        inferred_intent = "inform"

    if social_move == "attack":
        blame = 0.9
    elif primary_target == "assistant" and social_move == "complaint":
        blame = 0.65
    elif primary_target == "external" and negative_hits > 0:
        blame = 0.6
    elif primary_target == "self":
        blame = 0.3
    else:
        blame = 0.05

    if social_move in {"apology", "request"}:
        controllability = 0.75
    elif social_move in {"vulnerability", "complaint"}:
        controllability = 0.35
    elif social_move in {"gratitude", "connection"}:
        controllability = 0.6
    else:
        controllability = 0.5

    expectation_violation = 0.0
    if betrayal:
        expectation_violation = 0.9
    elif surprise:
        expectation_violation = 0.8
    elif "disappointed" in lower or "let down" in lower or "failed" in lower:
        expectation_violation = 0.6

    if social_move == "attack":
        vulnerability_score = 0.1
    elif social_move == "apology":
        vulnerability_score = 0.65
    elif social_move == "vulnerability":
        vulnerability_score = 0.85
    elif social_move == "complaint":
        vulnerability_score = 0.45
    else:
        vulnerability_score = 0.2 if vulnerable else 0.0

    if social_move in {"gratitude", "apology", "connection"}:
        affiliation_bid = 0.9
    elif social_move == "vulnerability":
        affiliation_bid = 0.75
    elif social_move == "request":
        affiliation_bid = 0.55
    elif social_move == "complaint":
        affiliation_bid = 0.2
    elif social_move == "attack":
        affiliation_bid = 0.0
    else:
        affiliation_bid = 0.3 if detected.valence >= 0.5 else 0.1

    # Mixed affect with vulnerability markers = genuine emotional exposure.
    if mixed_affect and vulnerable and social_move != "attack":
        vulnerability_score = max(vulnerability_score, 0.55)

    # Mildly raise vulnerability for strongly negative uncertain disclosures.
    if detected.valence < 0.35 and detected.certainty < 0.6 and social_move != "attack":
        vulnerability_score = max(vulnerability_score, 0.5)

    return AppraisalFrame(
        speaker_role="user",
        primary_target=primary_target,
        social_move=social_move,
        inferred_intent=inferred_intent,
        blame=blame,
        controllability=controllability,
        expectation_violation=expectation_violation,
        vulnerability=vulnerability_score,
        affiliation_bid=affiliation_bid,
        mixed_affect=mixed_affect,
        targets_assistant=primary_target == "assistant",
        reason="; ".join(reasons) if reasons else "default heuristic path",
    )


def appraise_with_life_history(
    appraisal: AppraisalFrame,
    query_text: str,
    life_history_slice: dict,
) -> AppraisalFrame:
    """Return appraisal adjusted by topic-relevant Life History."""
    if not isinstance(life_history_slice, dict):
        return appraisal
    query = (query_text or "").lower()
    beliefs = [
        item for item in life_history_slice.get("beliefs", [])
        if isinstance(item, dict)
    ]
    amplification = 1.0
    reasons = [appraisal.reason] if appraisal.reason else []
    for belief in beliefs:
        statement = str(belief.get("statement") or "").lower()
        key = str(belief.get("key") or belief.get("subject") or "").lower()
        confidence = _safe_float(belief.get("confidence"), 0.0)
        if confidence >= 0.18 and _touches(query, key, statement):
            amplification = max(amplification, 1.0 + confidence * 0.5)
            reasons.append(f"life-history belief touched:{key or 'belief'}")

    return AppraisalFrame(
        speaker_role=appraisal.speaker_role,
        primary_target=appraisal.primary_target,
        social_move=appraisal.social_move,
        inferred_intent=appraisal.inferred_intent,
        blame=min(1.0, appraisal.blame * amplification),
        controllability=appraisal.controllability,
        expectation_violation=min(1.0, appraisal.expectation_violation * amplification),
        vulnerability=min(1.0, appraisal.vulnerability * amplification),
        affiliation_bid=appraisal.affiliation_bid,
        mixed_affect=appraisal.mixed_affect,
        targets_assistant=appraisal.targets_assistant,
        reason="; ".join(reason for reason in reasons if reason),
    )


def _touches(query: str, *parts: str) -> bool:
    words = {word for word in re.findall(r"[a-z0-9_]{4,}", query)}
    if not words:
        return False
    haystack = " ".join(parts)
    return any(word in haystack for word in words)


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
