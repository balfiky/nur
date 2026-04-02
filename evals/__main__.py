"""Run the evaluation suite from the command line.

Usage:
    python -m evals              # run all scenarios
    python -m evals --tag tool   # run only scenarios tagged 'tool'
    python -m evals --json       # output JSON instead of text
    python -m evals --list       # list available scenarios
"""

from __future__ import annotations

import argparse
import sys

from evals.reporting import json_report, text_report
from evals.runner import run_by_tag, run_scenarios
from evals.scenarios import ALL_TAGS, all_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Nūr evaluation runner")
    parser.add_argument("--tag", help="Run only scenarios with this tag")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    args = parser.parse_args()

    scenarios = all_scenarios()

    if args.list:
        for s in scenarios:
            print(f"  {s.id:40s} tags={s.tags}")
        print(f"\n{len(scenarios)} scenarios, tags: {ALL_TAGS}")
        return

    if args.tag:
        report = run_by_tag(scenarios, args.tag)
    else:
        report = run_scenarios(scenarios)

    if args.json:
        print(json_report(report))
    else:
        print(text_report(report))

    if report.failed_scenarios > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
