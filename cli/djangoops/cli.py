"""Command-line entry point for DjangoOps."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from djangoops.compose import COMPOSE_FILENAME, generate_compose
from djangoops.config import DjangoOpsConfig, write_new_config
from djangoops.deploy import DeployError, DeploymentTarget, deploy_project

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

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="deploy the generated Compose stack to one VPS over direct SSH",
    )
    deploy_parser.add_argument("--host", required=True, help="trusted VPS DNS name or IPv4 host")
    deploy_parser.add_argument("--user", required=True, help="remote SSH user")
    deploy_parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    deploy_parser.add_argument(
        "--remote-base",
        required=True,
        help="absolute remote project directory, for example /srv/djangoops/myapp",
    )
    deploy_parser.add_argument(
        "--identity-file",
        type=Path,
        help="optional local SSH private-key path; key contents are never read by DjangoOps",
    )
    deploy_parser.add_argument(
        "--config",
        default=CONFIG_FILENAME,
        help=f"configuration path (default: {CONFIG_FILENAME})",
    )
    deploy_parser.add_argument(
        "--compose-file",
        default=COMPOSE_FILENAME,
        help=f"generated Compose path (default: {COMPOSE_FILENAME})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        project_name = args.project_name if args.project_name is not None else Path.cwd().name
        config_target = Path.cwd() / CONFIG_FILENAME
        try:
            config = DjangoOpsConfig.create(
                project_name=project_name,
                django_module=args.django_module,
            )
            write_new_config(config_target, config)
        except FileExistsError:
            print(
                f"error: {CONFIG_FILENAME} already exists; refusing to overwrite it",
                file=sys.stderr,
            )
            return 2
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Created {config_target}")
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

    if args.command == "deploy":
        try:
            deploy_target = DeploymentTarget.create(
                host=args.host,
                user=args.user,
                port=args.port,
                remote_base=args.remote_base,
                identity_file=args.identity_file,
            )
            release_id = deploy_project(
                project_root=Path.cwd(),
                config_path=Path(args.config),
                compose_path=Path(args.compose_file),
                target=deploy_target,
            )
        except (DeployError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Deployed release {release_id}")
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
