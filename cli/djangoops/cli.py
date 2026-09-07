"""Command-line entry point for DjangoOps."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from djangoops.compose import COMPOSE_FILENAME, generate_compose
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
        help="dotted Django project module, for example 'config' or 'myapp'",
    )

    compose_parser = subparsers.add_parser(
        "compose",
        help="generate the Phase 0 Docker Compose stack",
    )
    compose_parser.add_argument(
        "--config",
        default=CONFIG_FILENAME,
        help=f"configuration path (default: {CONFIG_FILENAME})",
    )
    compose_parser.add_argument(
        "--output",
        default=COMPOSE_FILENAME,
        help=f"output path (default: {COMPOSE_FILENAME})",
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

    if args.command == "compose":
        config_path = Path(args.config)
        output_path = Path(args.output)
        try:
            generate_compose(config_path, output_path)
        except FileExistsError:
            print(
                f"error: {output_path} already exists; refusing to overwrite it",
                file=sys.stderr,
            )
            return 2
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Created {output_path}")
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
