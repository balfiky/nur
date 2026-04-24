"""Nūr Runtime entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
from importlib.metadata import PackageNotFoundError, version

from runtime.app import NurApp
from runtime.config import RuntimeConfig


def _get_version() -> str:
    try:
        return version("project-nur")
    except PackageNotFoundError:
        return "unknown"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nur",
        description=(
            "Start the Nūr cognitive runtime (background agent). Runs the "
            "proactive loop, Telegram channel if configured, and background "
            "session/digest handling. Use `nur-web` instead if you only want "
            "the HTTP API and bundled browser UI."
        ),
    )
    parser.add_argument(
        "--config",
        default="runtime_config.yaml",
        help="Path to runtime config YAML (default: runtime_config.yaml).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"project-nur {_get_version()}",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = RuntimeConfig.from_yaml(args.config)
    app = NurApp(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
