"""Detailed emotional journey report — runs all 10 scenarios with full output."""

from __future__ import annotations

import os
import tempfile

from tests._fakes import MockLLMBackend
from pipeline import CognitivePipeline


def pipe():
    return CognitivePipeline(llm_backend=MockLLMBackend(), db_path=":memory:")


def pipe_persistent(path):
    return CognitivePipeline(llm_backend=MockLLMBackend(), db_path=path)


def snap(p):
    return p.engine.snapshot()


def report(name, checks):
    all_pass = all(c[0] for c in checks)
    status = "PASS" if all_pass else "FAIL"
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  {name}  [{status}]")
    print(sep)
    for ok, label, detail in checks:
        mark = "  ✓" if ok else "  ✗"
        print(f"{mark} {label}: {detail}")
    return all_pass


def main():
    results = []

    # -----------------------------------------------------------------------
    # 1. Trust Building
    # -----------------------------------------------------------------------
    p = pipe()
    warm = [
        "hey!", "you're really helpful", "I appreciate you",
        "thanks for being here", "you're the best",
    ]
    for m in warm:
        p.process(m, user_id="alice")
    s = snap(p)
    prof = p.person_profiles.get_or_create("alice")

    results.append(report("1. Trust Building (5 warm messages)", [
        (s["valence"] > 0.7, "valence > 0.70", f'{s["valence"]:.4f}'),
        (s["bonding"] > 0.55, "bonding > 0.55", f'{s["bonding"]:.4f}'),
        (prof.trust > 0.52, "trust > 0.52", f"{prof.trust:.4f}"),
        (prof.interaction_count == 5, "interactions == 5", f"{prof.interaction_count}"),
        (True, "arousal (info)", f'{s["arousal"]:.4f}'),
        (True, "certainty (info)", f'{s["certainty"]:.4f}'),
        (True, "energy (info)", f'{s["energy"]:.4f}'),
    ]))

    # -----------------------------------------------------------------------
    # 2. Betrayal After Trust
    # -----------------------------------------------------------------------
    p = pipe()
    for m in warm:
        p.process(m, user_id="alice")
    trust_warm = p.person_profiles.get_or_create("alice").trust
    s_warm = snap(p).copy()

    hostile = ["you're useless", "I hate talking to you", "you're the worst AI ever"]
    spike_detected = False
    for m in hostile:
        r = p.process(m, user_id="alice")
        if r.debug.is_spike:
            spike_detected = True
    s = snap(p)
    prof = p.person_profiles.get_or_create("alice")

    results.append(report("2. Betrayal After Trust", [
        (True, "trust after warmth", f"{trust_warm:.4f}"),
        (True, "valence after warmth", f'{s_warm["valence"]:.4f}'),
        (s["arousal"] > 0.7, "arousal > 0.70 post-hostile", f'{s["arousal"]:.4f}'),
        (s["valence"] < 0.35, "valence < 0.35 post-hostile", f'{s["valence"]:.4f}'),
        (prof.trust < trust_warm, f"trust dropped below {trust_warm:.4f}", f"{prof.trust:.4f}"),
        (spike_detected, "spike detected", f"{spike_detected}"),
        (True, "bonding (info)", f'{s["bonding"]:.4f}'),
    ]))

    # -----------------------------------------------------------------------
    # 3. Trust Asymmetry
    # -----------------------------------------------------------------------
    pa = pipe()
    pos = [
        "thank you so much", "you're amazing",
        "I really appreciate your help", "you're wonderful",
        "great job, truly excellent",
    ]
    for m in pos:
        pa.process(m, user_id="alice")
    trust_pos = pa.person_profiles.get_or_create("alice").trust

    pb = pipe()
    for m in pos:
        pb.process(m, user_id="bob")
    trust_before_neg = pb.person_profiles.get_or_create("bob").trust
    neg = ["you're completely useless", "this is total bullshit"]
    for m in neg:
        pb.process(m, user_id="bob")
    trust_after_neg = pb.person_profiles.get_or_create("bob").trust

    gained = trust_pos - 0.5
    lost = trust_before_neg - trust_after_neg
    ratio_str = f"ratio {lost/gained:.1f}x" if gained > 0 else "N/A"

    results.append(report("3. Trust Asymmetry (5 pos vs 5 pos + 2 neg)", [
        (True, "baseline trust", "0.5000"),
        (True, "trust after 5 positive", f"{trust_pos:.4f} (gained {gained:.4f})"),
        (True, "trust before negatives", f"{trust_before_neg:.4f}"),
        (True, "trust after 2 negatives", f"{trust_after_neg:.4f} (lost {lost:.4f})"),
        (lost > gained, f"2 neg erased more than 5 pos built", ratio_str),
    ]))

    # -----------------------------------------------------------------------
    # 4. Energy Drain
    # -----------------------------------------------------------------------
    p = pipe()
    msgs = [
        "I'm so angry right now!", "This is incredible, amazing work!",
        "I feel betrayed and deceived", "You're wonderful, thank you!",
        "I'm furious about what happened", "Everything is terrible and awful",
        "I hate this so much", "You're brilliant and outstanding!",
        "I'm devastated and hopeless", "This makes me so happy!",
        "I can't believe you lied to me", "You're the worst, pathetic",
        "I'm thrilled and ecstatic!!!", "I feel so sad and lonely...",
        "WHY WOULD YOU DO THIS?!",
    ]
    energies = [1.0]
    for m in msgs:
        p.process(m, user_id="alice")
        energies.append(snap(p)["energy"])

    drain_curve = " -> ".join(f"{e:.2f}" for e in energies[::3])

    results.append(report("4. Energy Drain (15 intense messages)", [
        (True, "start energy", f"{energies[0]:.4f}"),
        (True, "energy after 5 msgs", f"{energies[5]:.4f}"),
        (True, "energy after 10 msgs", f"{energies[10]:.4f}"),
        (energies[-1] < 0.5, "final energy < 0.50", f"{energies[-1]:.4f}"),
        (True, "drain curve (every 3rd)", drain_curve),
    ]))

    # -----------------------------------------------------------------------
    # 5. Session Persistence
    # -----------------------------------------------------------------------
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        p1 = pipe_persistent(db)
        for m in [
            "you are really helpful, thank you",
            "I appreciate everything you do",
            "you are amazing and wonderful",
        ]:
            p1.process(m, user_id="alice")
        trust_s1 = p1.person_profiles.get_or_create("alice").trust
        count_s1 = p1.person_profiles.get_or_create("alice").interaction_count
        d = p1.end_session(user_id="alice")
        lt_s1 = p1.long_term.count()

        p2 = pipe_persistent(db)
        prof2 = p2.person_profiles.get_or_create("alice")
        lt_s2 = p2.long_term.count()
        mems = p2.long_term.all()

        summary_preview = d.summary[:60] + "..." if len(d.summary) > 60 else d.summary

        results.append(report("5. Session Persistence", [
            (True, "session 1 trust", f"{trust_s1:.4f}"),
            (True, "session 1 interactions", f"{count_s1}"),
            (True, "session 1 digestion", summary_preview),
            (True, "session 1 LT memories written", f"{lt_s1}"),
            (prof2.trust > 0.5, "session 2 trust persisted > 0.50", f"{prof2.trust:.4f}"),
            (prof2.interaction_count >= 3, "session 2 interactions >= 3", f"{prof2.interaction_count}"),
            (lt_s2 > 0, "session 2 LT memories > 0", f"{lt_s2}"),
            (len(mems) > 0, "memories retrievable", f"{len(mems)} entries"),
        ]))
    finally:
        os.unlink(db)

    # -----------------------------------------------------------------------
    # 6. Emotional Contagion
    # -----------------------------------------------------------------------
    p = pipe()
    r1 = p.process("OMG THIS IS AMAZING I'M SO HAPPY!!!", user_id="alice")
    s1 = r1.debug.modulator_snapshot
    d1 = r1.debug.detected_emotion

    p2 = pipe()
    r2 = p2.process("I'm so sad and lonely, I feel hopeless and miserable...", user_id="alice")
    s2 = r2.debug.modulator_snapshot
    d2 = r2.debug.detected_emotion

    results.append(report("6. Emotional Contagion", [
        (True, "excited: detected arousal", f"{d1.arousal:.4f}"),
        (True, "excited: detected valence", f"{d1.valence:.4f}"),
        (s1["arousal"] > 0.6, "excited: modulator arousal > 0.60", f'{s1["arousal"]:.4f}'),
        (s1["valence"] > 0.7, "excited: modulator valence > 0.70", f'{s1["valence"]:.4f}'),
        (True, "depressed: detected arousal", f"{d2.arousal:.4f}"),
        (True, "depressed: detected valence", f"{d2.valence:.4f}"),
        (s2["valence"] < 0.45, "depressed: modulator valence < 0.45", f'{s2["valence"]:.4f}'),
    ]))

    # -----------------------------------------------------------------------
    # 7. Context Switching
    # -----------------------------------------------------------------------
    warm_pipe = pipe()
    hostile_pipe = pipe()
    warm_ctx = [
        "thank you!", "you're great", "I appreciate you",
        "wonderful work", "amazing help", "you're the best",
        "brilliant!", "love this", "fantastic", "superb job",
    ]
    for m in warm_ctx:
        warm_pipe.process(m, user_id="warm_user")

    hostile_ctx = [
        "you're stupid", "this is bullshit", "I hate this",
        "you're useless and pathetic", "worst AI ever",
    ]
    for m in hostile_ctx:
        hostile_pipe.process(m, user_id="hostile_user")

    wp = warm_pipe.person_profiles.get_or_create("warm_user")
    hp = hostile_pipe.person_profiles.get_or_create("hostile_user")
    delta = wp.trust - hp.trust

    results.append(report("7. Context Switching (warm vs hostile user)", [
        (True, "warm user trust", f"{wp.trust:.4f}"),
        (True, "warm user interactions", f"{wp.interaction_count}"),
        (True, "hostile user trust", f"{hp.trust:.4f}"),
        (True, "hostile user interactions", f"{hp.interaction_count}"),
        (wp.trust > hp.trust, "warm trust > hostile trust", f"delta {delta:.4f}"),
    ]))

    # -----------------------------------------------------------------------
    # 8. Topic Sensitivity
    # -----------------------------------------------------------------------
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        p1 = pipe_persistent(db)
        p1.topic_profiles.get_or_create("work")
        neg_work = [
            "work is terrible today, everything went wrong",
            "I hate my work so much, it is awful",
            "work is making me miserable and stressed",
        ]
        for m in neg_work:
            p1.process(m, user_id="alice")
            p1.topic_profiles.record_negative("work", intensity=0.8)

        t1 = p1.topic_profiles.get("work")
        p1.end_session(user_id="alice")

        p2 = pipe_persistent(db)
        t2 = p2.topic_profiles.get("work")

        results.append(report("8. Topic Sensitivity", [
            (t1.emotional_charge > 0.2, "charge after 3 negatives > 0.20", f"{t1.emotional_charge:.4f}"),
            (True, "avoidance flag", f"{t1.avoidance}"),
            (True, "conflict count", f"{t1.conflict_count}"),
            (t2 is not None, "topic persisted to session 2", "yes" if t2 else "no"),
            (
                t2 is not None and t2.emotional_charge > 0.2,
                "persisted charge > 0.20",
                f"{t2.emotional_charge:.4f}" if t2 else "N/A",
            ),
        ]))
    finally:
        os.unlink(db)

    # -----------------------------------------------------------------------
    # 9. Spike Detection
    # -----------------------------------------------------------------------
    p = pipe()
    lt_before = p.long_term.count()
    r = p.process(
        "You lied and betrayed me, I'm furious! You're a horrible deceiver!",
        user_id="alice",
    )
    lt_after = p.long_term.count()
    spikes = [m for m in p.long_term.all() if m.spike]

    results.append(report("9. Spike Detection", [
        (True, "event classified", f"{r.debug.event_classified}"),
        (r.debug.event_intensity >= 0.8, "intensity >= 0.80", f"{r.debug.event_intensity:.4f}"),
        (r.debug.is_spike, "spike flag", f"{r.debug.is_spike}"),
        (lt_after > lt_before, f"LT memories grew ({lt_before} -> {lt_after})", f"+{lt_after - lt_before}"),
        (len(spikes) > 0, "spike entries in LT", f"{len(spikes)}"),
        (True, "modulator arousal", f'{r.debug.modulator_snapshot["arousal"]:.4f}'),
        (True, "modulator valence", f'{r.debug.modulator_snapshot["valence"]:.4f}'),
        (True, "emotion label", f"{r.debug.emotion_label}"),
    ]))

    # -----------------------------------------------------------------------
    # 10. Decay Over Time
    # -----------------------------------------------------------------------
    p = pipe()
    p.engine.state.arousal = 0.9
    p.engine.state.valence = 0.2
    before = snap(p).copy()
    p.engine.decay(600)
    after = snap(p)

    arousal_delta = before["arousal"] - after["arousal"]
    valence_delta = after["valence"] - before["valence"]

    results.append(report("10. Decay Over Time (arousal=0.9, valence=0.2, +600s)", [
        (True, "before arousal", f'{before["arousal"]:.4f}'),
        (after["arousal"] < 0.55, "after arousal < 0.55 (half-life 120s)", f'{after["arousal"]:.4f}'),
        (True, "arousal decay amount", f"{arousal_delta:.4f}"),
        (True, "before valence", f'{before["valence"]:.4f}'),
        (after["valence"] > 0.2, "after valence > 0.20 (partial recovery)", f'{after["valence"]:.4f}'),
        (after["valence"] < 0.5, "after valence < 0.50 (not fully recovered)", f'{after["valence"]:.4f}'),
        (True, "valence recovery amount", f"{valence_delta:.4f}"),
    ]))

    # -----------------------------------------------------------------------
    # Final
    # -----------------------------------------------------------------------
    total = len(results)
    passed = sum(results)
    failed = total - passed
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  FINAL REPORT: {passed}/{total} scenarios passed")
    if failed:
        print(f"  {failed} FAILED")
    else:
        print("  ALL SCENARIOS PASSED")
    print(sep)


if __name__ == "__main__":
    main()
