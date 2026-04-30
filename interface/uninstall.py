"""Local data uninstaller for Nūr workspaces."""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from runtime.config import RuntimeConfig


@dataclass(frozen=True)
class RemovalItem:
    kind: str
    path: Path
    reason: str


@dataclass(frozen=True)
class UninstallPlan:
    remove: list[RemovalItem]
    skipped: list[RemovalItem]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nur-uninstall",
        description=(
            "Remove a local Nūr workspace created by nur-setup or nur-web. "
            "This cleans runtime_config.yaml and local data; uninstall the Python "
            "package separately with pip if you also want to remove the command."
        ),
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("NUR_RUNTIME_CONFIG", "runtime_config.yaml"),
        help="Runtime config path to inspect/remove (default: runtime_config.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting anything.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--keep-config",
        action="store_true",
        help="Keep runtime_config.yaml.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the configured data directory.",
    )
    parser.add_argument(
        "--remove-external-workspace",
        action="store_true",
        help="Also remove a custom tools_workspace outside data_dir.",
    )
    parser.add_argument(
        "--remove-identity",
        action="store_true",
        help="Remove $NUR_CONFIG_DIR/soul.yaml when NUR_CONFIG_DIR is set.",
    )
    return parser.parse_args(argv)


def build_uninstall_plan(
    *,
    config_path: Path,
    keep_config: bool = False,
    keep_data: bool = False,
    remove_external_workspace: bool = False,
    remove_identity: bool = False,
    environ: dict[str, str] | None = None,
) -> UninstallPlan:
    env = environ if environ is not None else os.environ
    config_path = config_path.expanduser().resolve()
    config = RuntimeConfig.from_yaml(str(config_path))
    data_dir = _resolve_runtime_path(config.data_dir)
    workspace = _resolve_runtime_path(config.resolved_tools_workspace)

    remove: list[RemovalItem] = []
    skipped: list[RemovalItem] = []

    if config_path.exists():
        item = RemovalItem("config", config_path, "runtime configuration")
        (skipped if keep_config else remove).append(item)
    else:
        skipped.append(RemovalItem("config", config_path, "runtime config not found"))

    if data_dir.exists():
        item = RemovalItem("data", data_dir, "memory, skills, uploads, backups, and admin state")
        (skipped if keep_data else remove).append(item)
    else:
        skipped.append(RemovalItem("data", data_dir, "data directory not found"))

    if workspace.exists() and not _is_same_or_child(workspace, data_dir):
        item = RemovalItem("workspace", workspace, "custom tools workspace outside data_dir")
        if remove_external_workspace and not keep_data:
            remove.append(item)
        else:
            skipped.append(item)

    identity_dir = env.get("NUR_CONFIG_DIR", "").strip()
    if identity_dir:
        identity_path = (Path(identity_dir).expanduser() / "soul.yaml").resolve()
        if identity_path.exists():
            item = RemovalItem("identity", identity_path, "$NUR_CONFIG_DIR identity file")
            (remove if remove_identity else skipped).append(item)
        else:
            skipped.append(RemovalItem("identity", identity_path, "identity file not found"))

    return UninstallPlan(remove=remove, skipped=skipped)


def execute_plan(plan: UninstallPlan, *, dry_run: bool = False) -> list[RemovalItem]:
    removed: list[RemovalItem] = []
    if dry_run:
        return removed
    for item in plan.remove:
        _refuse_dangerous_path(item.path, item.kind)
    for item in plan.remove:
        if item.path.is_dir():
            shutil.rmtree(item.path)
        elif item.path.exists():
            item.path.unlink()
        removed.append(item)
    return removed


def _resolve_runtime_path(value: str) -> Path:
    return Path(value or "data").expanduser().resolve()


def _is_same_or_child(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _refuse_dangerous_path(path: Path, kind: str) -> None:
    resolved = path.resolve()
    home = Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home:
        raise SystemExit(f"Refusing to remove dangerous {kind} path: {resolved}")


def _format_plan(plan: UninstallPlan) -> str:
    lines = ["Nūr uninstall plan:"]
    if plan.remove:
        lines.append("Will remove:")
        for item in plan.remove:
            lines.append(f"  - {item.kind}: {item.path} ({item.reason})")
    else:
        lines.append("Will remove: nothing")
    if plan.skipped:
        lines.append("Will keep/skip:")
        for item in plan.skipped:
            lines.append(f"  - {item.kind}: {item.path} ({item.reason})")
    return "\n".join(lines)


def _confirm() -> bool:
    answer = input("Type uninstall to continue: ").strip().lower()
    return answer == "uninstall"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    plan = build_uninstall_plan(
        config_path=Path(args.config),
        keep_config=args.keep_config,
        keep_data=args.keep_data,
        remove_external_workspace=args.remove_external_workspace,
        remove_identity=args.remove_identity,
    )
    print(_format_plan(plan))
    if args.dry_run:
        print("Dry run only. Nothing removed.")
        return
    if plan.remove and not args.yes and not _confirm():
        print("Cancelled. Nothing removed.")
        return
    removed = execute_plan(plan)
    print(f"Removed {len(removed)} item(s).")
    print("To remove the installed Python package too, run: python3 -m pip uninstall project-nur")


if __name__ == "__main__":
    main()
