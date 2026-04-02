"""Action-variable derivation from modulator state.

These are turn-level derived values — not stored modulators.
They shape tool decisions by translating emotional/cognitive state
into action tendencies: risk tolerance, urgency, clarification need,
persistence, and autonomy.

All derivation is pure math — zero LLM calls.

Section 8 shaping rules:
  - high arousal  → higher urgency, lower clarification threshold
  - low certainty → lower autonomy bias, higher clarification threshold
  - low energy    → lower persistence, lower urgency
  - high resolution → higher persistence (closing loops)
  - high bonding/trust → higher risk tolerance, higher autonomy bias
  - defense active → lower risk tolerance, lower autonomy bias
"""

from __future__ import annotations

from core.types import ActionVariables, ModulatorState


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def derive_action_variables(
    state: ModulatorState,
    trust: float = 0.5,
    defense_active: bool = False,
) -> ActionVariables:
    """Derive turn-level action variables from current cognitive state.

    Args:
        state: Current modulator snapshot (arousal, valence, certainty,
               bonding, energy, resolution).
        trust: Trust level toward the current person (0.0-1.0).
        defense_active: Whether a defense mechanism fired this turn.

    Returns:
        ActionVariables with all five derived values clamped to [0, 1].
    """
    # -- risk_tolerance --
    # Higher with certainty and trust; lower under defense
    risk_tolerance = 0.5
    risk_tolerance += (state.certainty - 0.5) * 0.3   # confident → riskier
    risk_tolerance += (trust - 0.5) * 0.2              # trusted user → riskier
    risk_tolerance += (state.bonding - 0.5) * 0.1      # bonded → slightly riskier
    if defense_active:
        risk_tolerance -= 0.15                          # defensive → cautious

    # -- action_urgency --
    # Higher with arousal and resolution; lower with low energy
    action_urgency = 0.25
    action_urgency += (state.arousal - 0.5) * 0.4      # activated → urgent
    action_urgency += (state.resolution) * 0.2          # unfinished business → urgent
    action_urgency -= (0.5 - min(state.energy, 0.5)) * 0.2  # tired → less urgent

    # -- clarification_threshold --
    # Higher when uncertain or low trust; lower when aroused (impulsive)
    clarification_threshold = 0.5
    clarification_threshold += (0.5 - state.certainty) * 0.3  # uncertain → ask more
    clarification_threshold += (0.5 - trust) * 0.2            # low trust → ask more
    clarification_threshold -= (state.arousal - 0.5) * 0.2    # aroused → ask less

    # -- persistence_drive --
    # Higher with resolution and energy; lower when tired
    persistence_drive = 0.45
    persistence_drive += (state.resolution) * 0.25     # unfinished → persistent
    persistence_drive += (state.energy - 0.5) * 0.2    # energized → persistent
    if defense_active:
        persistence_drive -= 0.1                        # defensive → gives up easier

    # -- autonomy_bias --
    # Higher with certainty and trust; lower under defense
    autonomy_bias = 0.5
    autonomy_bias += (state.certainty - 0.5) * 0.3    # confident → act alone
    autonomy_bias += (trust - 0.5) * 0.2              # trusted → act alone
    autonomy_bias += (state.bonding - 0.5) * 0.1      # bonded → act alone
    if defense_active:
        autonomy_bias -= 0.15                           # defensive → defer

    return ActionVariables(
        risk_tolerance=_clamp(risk_tolerance),
        action_urgency=_clamp(action_urgency),
        clarification_threshold=_clamp(clarification_threshold),
        persistence_drive=_clamp(persistence_drive),
        autonomy_bias=_clamp(autonomy_bias),
    )
