"""Command-line entry point for DjangoOps."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from djangoops.config import DjangoOpsConfig, write_new_config

CONFIG_FILENAME = "djangoops.yaml"


def build_parser() -> argparse.ArgumentParser:
    """Build the small Phase 0 CLI surface."""
    parser = argparse.ArgumentParser(
        prog="djangoops",
        description="Django-specific operations tooling",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="create non-secret DjangoOps project configuration",
    )
    init_parser.add_argument(
        "--project-name",
        help="project name (defaults to the current directory name)",
    )
    init_parser.add_argument(
        "--django-module",
        required=True,
        help="dotted Django project module, for example 'config' or 'myapp.settings'",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        project_name = args.project_name if args.project_name is not None else Path.cwd().name
        target = Path.cwd() / CONFIG_FILENAME
        try:
            config = DjangoOpsConfig.create(
                project_name=project_name,
                django_module=args.django_module,
            )
            write_new_config(target, config)
        except FileExistsError:
            print(
                f"error: {CONFIG_FILENAME} already exists; refusing to overwrite it",
                file=sys.stderr,
            )
            return 2
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Created {target}")
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
