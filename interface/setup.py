"""One-command local setup for the Nūr browser onboarding flow."""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

from runtime.config import RuntimeConfig


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nur-setup",
        description=(
            "Initialize a local Nūr workspace and launch the browser setup wizard. "
            "This creates runtime_config.yaml when missing, creates data/workspace "
            "folders, then starts the bundled web UI."
        ),
    )
    parser.add_argument(
        "--config",
        default="runtime_config.yaml",
        help="Runtime config path to create/use (default: runtime_config.yaml).",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory for memory, skills, and uploads (default: data).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port to bind (default: 8000).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite runtime_config.yaml with starter defaults.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the setup wizard in a browser.",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Create config/data folders and exit without starting the web UI.",
    )
    return parser.parse_args(argv)


def _initialize_workspace(config_path: Path, data_dir: str, *, force: bool) -> bool:
    config_path = config_path.expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    created = force or not config_path.exists()
    if created:
        requested_data_dir = Path(data_dir).expanduser()
        resolved_data_dir = (
            requested_data_dir
            if requested_data_dir.is_absolute()
            else config_path.parent / requested_data_dir
        )
        RuntimeConfig(data_dir=str(resolved_data_dir)).write_yaml(str(config_path))
    config = RuntimeConfig.from_yaml(str(config_path))
    Path(config.data_dir).expanduser().mkdir(parents=True, exist_ok=True)
    Path(config.resolved_tools_workspace).expanduser().mkdir(parents=True, exist_ok=True)
    return created


def _browser_url(host: str, port: int) -> str:
    visible_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{visible_host}:{port}"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    created = _initialize_workspace(config_path, args.data_dir, force=args.force)
    os.environ["NUR_RUNTIME_CONFIG"] = str(config_path)

    print(("Created" if created else "Using") + f" {config_path}")
    print(f"Data directory is {RuntimeConfig.from_yaml(str(config_path)).data_dir}")
    if args.init_only:
        return

    url = _browser_url(args.host, args.port)
    print(f"Launching Nūr setup at {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run("interface.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
