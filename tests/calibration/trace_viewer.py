"""Trace viewer for calibration scenarios.

Generates matplotlib plots of modulator traces for visual inspection
and tuning of emotional dynamics.

Usage:
    python -m tests.calibration.trace_viewer [scenario_name]
    python -m tests.calibration.trace_viewer --all
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from tests.calibration.scenarios import (
    ScenarioResult,
    ALL_SCENARIOS,
    run_all_scenarios,
)


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

MODULATOR_COLORS = {
    "arousal": "#e74c3c",     # red
    "valence": "#2ecc71",     # green
    "certainty": "#3498db",   # blue
    "bonding": "#e67e22",     # orange
    "energy": "#9b59b6",      # purple
}


def plot_scenario(result: ScenarioResult, save_path: str | None = None) -> None:
    """Plot all modulator traces for a scenario."""
    points = result.all_points
    if not points:
        print(f"No trace points for scenario: {result.name}")
        return

    steps = list(range(len(points)))

    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(f"Scenario: {result.name}", fontsize=14, fontweight="bold")
    gs = gridspec.GridSpec(3, 2, hspace=0.4, wspace=0.3)

    # --- All modulators combined ---
    ax_all = fig.add_subplot(gs[0, :])
    for mod in ["arousal", "valence", "certainty", "bonding", "energy"]:
        values = result.modulators_over_time(mod)
        ax_all.plot(steps, values, label=mod, color=MODULATOR_COLORS[mod], linewidth=1.5)
    ax_all.set_ylabel("Value")
    ax_all.set_title("All Modulators")
    ax_all.legend(loc="upper right", fontsize=8)
    ax_all.set_ylim(-0.05, 1.05)
    ax_all.grid(True, alpha=0.3)
    _add_session_markers(ax_all, result)

    # --- Individual modulators ---
    mod_axes = [
        ("arousal", gs[1, 0]),
        ("valence", gs[1, 1]),
        ("certainty", gs[2, 0]),
        ("bonding", gs[2, 1]),
    ]
    for mod, pos in mod_axes:
        ax = fig.add_subplot(pos)
        values = result.modulators_over_time(mod)
        ax.plot(steps, values, color=MODULATOR_COLORS[mod], linewidth=1.5)
        ax.fill_between(steps, values, alpha=0.15, color=MODULATOR_COLORS[mod])
        ax.set_ylabel(mod.capitalize())
        ax.set_title(mod.capitalize())
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0.5, color="gray", linestyle="--", alpha=0.4)
        _add_session_markers(ax, result)

        # Mark spikes
        for i, p in enumerate(points):
            if p.is_spike:
                ax.axvline(x=i, color="red", linestyle=":", alpha=0.5)

    # Add step labels on last row
    for ax in [fig.axes[-1], fig.axes[-2]]:
        ax.set_xlabel("Step")

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {save_path}")
    else:
        plt.show()


def plot_energy_detail(result: ScenarioResult, save_path: str | None = None) -> None:
    """Plot energy with emotion labels annotated."""
    points = result.all_points
    if not points:
        return

    steps = list(range(len(points)))
    energies = result.energy_over_time()

    fig, (ax_energy, ax_events) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    fig.suptitle(f"Energy & Events: {result.name}", fontsize=14, fontweight="bold")

    # Energy
    ax_energy.plot(steps, energies, color=MODULATOR_COLORS["energy"], linewidth=2)
    ax_energy.fill_between(steps, energies, alpha=0.2, color=MODULATOR_COLORS["energy"])
    ax_energy.set_ylabel("Energy")
    ax_energy.set_ylim(-0.05, 1.05)
    ax_energy.grid(True, alpha=0.3)
    ax_energy.axhline(y=0.2, color="red", linestyle="--", alpha=0.5, label="Low energy")
    ax_energy.legend(fontsize=8)
    _add_session_markers(ax_energy, result)

    # Annotate emotion labels at interesting points
    prev_label = ""
    for i, p in enumerate(points):
        if p.emotion_label != prev_label:
            ax_energy.annotate(
                p.emotion_label,
                (i, p.energy),
                fontsize=7,
                rotation=45,
                alpha=0.7,
            )
            prev_label = p.emotion_label

    # Event intensities
    intensities = [p.event_intensity for p in points]
    colors = ["red" if p.is_spike else "steelblue" for p in points]
    ax_events.bar(steps, intensities, color=colors, alpha=0.7, width=0.8)
    ax_events.set_ylabel("Event Intensity")
    ax_events.set_xlabel("Step")
    ax_events.set_ylim(0, 1.05)
    ax_events.grid(True, alpha=0.3)
    _add_session_markers(ax_events, result)

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {save_path}")
    else:
        plt.show()


def _add_session_markers(ax: plt.Axes, result: ScenarioResult) -> None:
    """Add vertical lines at session boundaries."""
    offset = 0
    for session in result.sessions:
        if offset > 0:
            ax.axvline(x=offset, color="black", linestyle="-", alpha=0.2, linewidth=1)
        offset += len(session.points)


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def print_scenario_summary(result: ScenarioResult) -> None:
    """Print a text summary of a scenario's trace."""
    points = result.all_points
    if not points:
        print(f"No data for {result.name}")
        return

    print(f"\n{'='*60}")
    print(f" Scenario: {result.name}")
    print(f"{'='*60}")
    print(f" Sessions: {len(result.sessions)}")
    print(f" Total steps: {len(points)}")
    print()

    for mod in ["arousal", "valence", "certainty", "bonding", "energy"]:
        values = result.modulators_over_time(mod)
        print(f" {mod:12s}  min={min(values):.3f}  max={max(values):.3f}  "
              f"start={values[0]:.3f}  end={values[-1]:.3f}")

    spikes = sum(1 for p in points if p.is_spike)
    print(f"\n Spikes: {spikes}")

    # Session digestions
    for i, session in enumerate(result.sessions):
        if session.digestion:
            d = session.digestion
            print(f"\n Session {i}: trust_delta={d.trust_delta:+.4f}  "
                  f"arc={d.emotional_arc_label}  memories={d.memories_written}")

    # Emotion label transitions
    labels = []
    prev = ""
    for p in points:
        if p.emotion_label != prev:
            labels.append((p.step, p.emotion_label))
            prev = p.emotion_label
    print(f"\n Emotion transitions: {' -> '.join(l for _, l in labels)}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if not args or "--all" in args:
        # Run all scenarios
        print("Running all calibration scenarios...")
        results = run_all_scenarios()
        for name, result in results.items():
            print_scenario_summary(result)
        # Plot if matplotlib display is available
        if "--plot" in args or "--save" in args:
            output_dir = Path("tests/calibration/output")
            output_dir.mkdir(exist_ok=True)
            for name, result in results.items():
                plot_scenario(result, save_path=str(output_dir / f"{name}_modulators.png"))
                plot_energy_detail(result, save_path=str(output_dir / f"{name}_energy.png"))
            print(f"\nPlots saved to {output_dir}/")
    else:
        # Run specific scenario
        scenario_name = args[0]
        scenario_map = dict(ALL_SCENARIOS)
        if scenario_name not in scenario_map:
            print(f"Unknown scenario: {scenario_name}")
            print(f"Available: {', '.join(scenario_map.keys())}")
            sys.exit(1)
        result = scenario_map[scenario_name]()
        print_scenario_summary(result)
        if "--plot" in args:
            plot_scenario(result)
            plot_energy_detail(result)
        elif "--save" in args:
            output_dir = Path("tests/calibration/output")
            output_dir.mkdir(exist_ok=True)
            plot_scenario(result, save_path=str(output_dir / f"{scenario_name}_modulators.png"))
            plot_energy_detail(result, save_path=str(output_dir / f"{scenario_name}_energy.png"))


if __name__ == "__main__":
    main()
