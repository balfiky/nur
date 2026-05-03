"""Explicit character genesis reset CLI."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from runtime.config import RuntimeConfig
from runtime.life_history import life_history_db_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nur-genesis-reset")
    parser.add_argument("--config", default=os.environ.get("NUR_RUNTIME_CONFIG", "runtime_config.yaml"))
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args(argv)

    config = RuntimeConfig.from_yaml(args.config)
    db_path = Path(life_history_db_path(config))
    if not args.yes:
        expected = f"RESET {db_path}"
        typed = input(f"Type '{expected}' to delete runtime character state: ")
        if typed != expected:
            print("Confirmation did not match; aborting.")
            return 1
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    print(f"Deleted character state: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
