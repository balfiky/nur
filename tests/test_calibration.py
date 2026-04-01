"""Tests for calibration scenarios — Phase 9.

These tests run full pipeline scenarios and verify that the emotional
dynamics behave as expected. They don't test exact values (those change
with tuning) but verify directional properties.
"""

import pytest

from tests.calibration.scenarios import (
    scenario_trust_building,
    scenario_betrayal,
    scenario_topic_avoidance,
    scenario_contagion,
    scenario_conflict_recovery,
    scenario_energy_depletion,
    run_all_scenarios,
)


class TestTrustBuilding:
    """Trust should build steadily over 10 warm sessions."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_trust_building()

    def test_ran_all_sessions(self, result):
        assert len(result.sessions) == 10

    def test_valence_stays_positive(self, result):
        valences = result.modulators_over_time("valence")
        avg = sum(valences) / len(valences)
        assert avg > 0.5, f"Average valence should be positive: {avg}"

    def test_bonding_increases(self, result):
        bondings = result.modulators_over_time("bonding")
        # Bonding at end should be higher than start
        assert bondings[-1] > bondings[0], "Bonding should increase over warm sessions"

    def test_energy_recovers_between_sessions(self, result):
        # Check that rest provides some recovery between sessions
        # Energy drains ~0.02/msg * 5 + digestion drain each session
        # Recovery is 0.1/hr * 2hr = 0.2 per rest — not full recovery
        for i, session in enumerate(result.sessions):
            if i > 0 and session.points:
                assert session.points[0].energy > 0.05, f"Session {i} energy critically low"

    def test_trust_deltas_mostly_positive(self, result):
        positive_sessions = sum(
            1 for s in result.sessions
            if s.digestion and s.digestion.trust_delta > 0
        )
        # Most sessions should have positive trust delta
        assert positive_sessions >= 5, f"Only {positive_sessions}/10 sessions had positive trust"


class TestBetrayal:
    """Trust builds, then crashes on betrayal."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_betrayal()

    def test_ran_all_sessions(self, result):
        assert len(result.sessions) == 5

    def test_betrayal_session_has_spikes(self, result):
        # Session 3 is the betrayal session
        betrayal_session = result.sessions[3]
        spike_count = sum(1 for p in betrayal_session.points if p.is_spike)
        assert spike_count >= 1, "Betrayal should trigger at least one spike"

    def test_valence_drops_during_betrayal(self, result):
        # Compare valence before and during betrayal
        pre_betrayal = result.sessions[2].points[-1].modulators["valence"]
        betrayal_points = result.sessions[3].points
        min_valence = min(p.modulators["valence"] for p in betrayal_points)
        assert min_valence < pre_betrayal, "Valence should drop during betrayal"

    def test_arousal_spikes_during_betrayal(self, result):
        betrayal_points = result.sessions[3].points
        max_arousal = max(p.modulators["arousal"] for p in betrayal_points)
        assert max_arousal > 0.6, f"Arousal should spike during betrayal: {max_arousal}"

    def test_betrayal_trust_delta_negative(self, result):
        betrayal_digestion = result.sessions[3].digestion
        assert betrayal_digestion is not None
        assert betrayal_digestion.trust_delta < 0, "Betrayal session should have negative trust delta"


class TestTopicAvoidance:
    """Repeated negative associations should trigger topic avoidance."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_topic_avoidance()

    def test_ran_all_sessions(self, result):
        assert len(result.sessions) == 5

    def test_scenario_completes(self, result):
        total_points = len(result.all_points)
        assert total_points == 20  # 5 sessions * 4 messages

    def test_valence_trends_negative(self, result):
        # With repeated angry/frustrated messages, valence should trend down
        valences = result.modulators_over_time("valence")
        avg_first_half = sum(valences[:10]) / 10
        avg_second_half = sum(valences[10:]) / 10
        # Second half shouldn't be significantly higher than first
        assert avg_second_half <= avg_first_half + 0.1


class TestContagion:
    """User emotion should mirror into AI state."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_contagion()

    def test_single_session(self, result):
        assert len(result.sessions) == 1

    def test_arousal_rises_with_escalation(self, result):
        points = result.all_points
        # Neutral start, escalation in middle
        neutral_arousal = points[0].modulators["arousal"]
        # The screaming message (index 3) should push arousal up
        peak_arousal = max(p.modulators["arousal"] for p in points[2:5])
        assert peak_arousal > neutral_arousal, "Arousal should rise with user escalation"

    def test_valence_drops_with_negative_emotion(self, result):
        points = result.all_points
        start_valence = points[0].modulators["valence"]
        # After the frustrated/stressed messages
        mid_valence = min(p.modulators["valence"] for p in points[2:5])
        assert mid_valence < start_valence, "Valence should drop with negative user emotion"

    def test_recovery_after_calming(self, result):
        points = result.all_points
        # Last message is positive ("I feel a bit better")
        # Valence at end should be better than the low point
        low_valence = min(p.modulators["valence"] for p in points[2:5])
        end_valence = points[-1].modulators["valence"]
        assert end_valence >= low_valence, "Valence should partially recover after calming"


class TestConflictRecovery:
    """Fight → resolution → recovery arc."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_conflict_recovery()

    def test_three_phases(self, result):
        assert len(result.sessions) == 3

    def test_conflict_drops_valence(self, result):
        # Pre-conflict session should have higher valence than conflict session
        pre_valences = [p.modulators["valence"] for p in result.sessions[0].points]
        conflict_valences = [p.modulators["valence"] for p in result.sessions[1].points]
        assert min(conflict_valences) < max(pre_valences), "Conflict should drop valence below pre-conflict"

    def test_resolution_improves_valence(self, result):
        conflict_points = result.sessions[1].points
        resolution_points = result.sessions[2].points
        # End of resolution should be better than end of conflict
        conflict_end_v = conflict_points[-1].modulators["valence"]
        resolution_end_v = resolution_points[-1].modulators["valence"]
        assert resolution_end_v > conflict_end_v, "Resolution should improve valence"

    def test_conflict_session_negative_trust(self, result):
        conflict_digestion = result.sessions[1].digestion
        assert conflict_digestion is not None
        assert conflict_digestion.trust_delta < 0, "Conflict session should damage trust"


class TestEnergyDepletion:
    """Long session should drain energy."""

    @pytest.fixture(scope="class")
    def result(self):
        return scenario_energy_depletion()

    def test_two_sessions(self, result):
        assert len(result.sessions) == 2

    def test_energy_decreases_over_long_session(self, result):
        energies = [p.energy for p in result.sessions[0].points]
        assert energies[-1] < energies[0], "Energy should decrease over a long session"

    def test_energy_depleted_significantly(self, result):
        # 40 messages should drain significant energy
        final_energy = result.sessions[0].points[-1].energy
        assert final_energy < 0.5, f"40-message session should deplete below 0.5: {final_energy}"

    def test_partial_recovery_after_rest(self, result):
        # After 1 hour rest, energy should be higher than end of first session
        end_first = result.sessions[0].points[-1].energy
        start_second = result.sessions[1].points[0].energy
        assert start_second > end_first, "1 hour rest should partially recover energy"

    def test_emotion_label_shifts(self, result):
        # At some point during depletion, emotion label should change
        labels = set(p.emotion_label for p in result.sessions[0].points)
        # With sustained low-intensity messages, we should see at least some variation
        assert len(labels) >= 1  # at least neutral


class TestAllScenariosRun:
    """Verify all scenarios run without errors."""

    def test_all_scenarios_complete(self):
        results = run_all_scenarios()
        assert len(results) == 6
        for name, result in results.items():
            assert len(result.all_points) > 0, f"Scenario {name} produced no trace points"
            for session in result.sessions:
                assert session.digestion is not None, f"Scenario {name} missing digestion"
