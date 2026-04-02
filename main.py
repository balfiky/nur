"""Jarvis Runtime entry point."""

from __future__ import annotations

import asyncio
import logging

from runtime.app import JarvisApp
from runtime.config import RuntimeConfig


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = RuntimeConfig.from_yaml("runtime_config.yaml")
    app = JarvisApp(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
