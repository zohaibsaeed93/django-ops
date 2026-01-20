"""Command-line entry point for DjangoOps."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from djangoops.backup import (
    BackupError,
    install_backup_schedule,
    list_backups,
    restore_backup,
    run_backup,
)
from djangoops.compose import COMPOSE_FILENAME, generate_compose, load_config
from djangoops.config import DjangoOpsConfig, upgrade_config, write_new_config
from djangoops.deploy import DeployError, DeploymentTarget, deploy_project
from djangoops.health import HealthTransportError, health_project

CONFIG_FILENAME = "djangoops.yaml"


def _add_ssh_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="trusted VPS DNS name or IPv4 host")
    parser.add_argument("--user", required=True, help="remote SSH user")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument(
        "--remote-base",
        required=True,
        help="absolute remote project directory",
    )
    parser.add_argument(
        "--identity-file",
        type=Path,
        help="optional local SSH private-key path; key contents are never read by DjangoOps",
    )


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=CONFIG_FILENAME,
        help=f"configuration path (default: {CONFIG_FILENAME})",
    )


def _add_compose_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--compose-file",
        default=COMPOSE_FILENAME,
        help=f"deployed project-relative Compose path (default: {COMPOSE_FILENAME})",
    )


def build_parser() -> argparse.ArgumentParser:
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
        help="dotted Django project module",
    )
    init_parser.add_argument(
        "--hostname",
        required=True,
        help="public DNS hostname routed to the VPS",
    )
    init_parser.add_argument(
        "--acme-email",
        required=True,
        help="Let's Encrypt/ACME contact email",
    )
    init_parser.add_argument(
        "--storage-endpoint-url",
        required=True,
        help="S3-compatible endpoint URL (non-secret)",
    )
    init_parser.add_argument("--storage-region", help="optional S3-compatible region")
    init_parser.add_argument(
        "--static-bucket",
        required=True,
        help="S3-compatible static asset bucket",
    )
    init_parser.add_argument(
        "--media-bucket",
        required=True,
        help="S3-compatible media asset bucket",
    )
    init_parser.add_argument(
        "--backup-bucket",
        required=True,
        help="S3-compatible backup bucket",
    )
    init_parser.add_argument(
        "--backup-schedule",
        default="17 2 * * *",
        help="five-field cron schedule",
    )

    upgrade_parser = subparsers.add_parser(
        "config-upgrade",
        help="upgrade schema-v2 config to schema v3",
    )
    _add_config_arg(upgrade_parser)
    upgrade_parser.add_argument("--backup-bucket", required=True)
    upgrade_parser.add_argument("--backup-schedule", default="17 2 * * *")

    compose_parser = subparsers.add_parser(
        "compose",
        help="generate the Phase 0 Docker Compose stack",
    )
    _add_config_arg(compose_parser)
    compose_parser.add_argument(
        "--output",
        default=COMPOSE_FILENAME,
        help=f"output path (default: {COMPOSE_FILENAME})",
    )

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="deploy the generated Compose stack to one VPS over direct SSH",
    )
    _add_ssh_target_args(deploy_parser)
    _add_config_arg(deploy_parser)
    _add_compose_file_arg(deploy_parser)

    health_parser = subparsers.add_parser(
        "health",
        help="run read-only Django-aware diagnostics over direct SSH",
    )
    _add_ssh_target_args(health_parser)
    _add_compose_file_arg(health_parser)
    health_parser.add_argument(
        "--json",
        action="store_true",
        help="emit one deterministic JSON document on stdout",
    )

    for name, help_text in (
        ("backup-schedule", "install or update the project's remote backup schedule"),
        ("backup-run", "run one backup immediately"),
        ("backup-list", "list available project backups"),
    ):
        backup_parser = subparsers.add_parser(name, help=help_text)
        _add_ssh_target_args(backup_parser)
        _add_config_arg(backup_parser)
        _add_compose_file_arg(backup_parser)

    restore_parser = subparsers.add_parser(
        "backup-restore",
        help="restore one explicitly selected backup",
    )
    _add_ssh_target_args(restore_parser)
    _add_config_arg(restore_parser)
    _add_compose_file_arg(restore_parser)
    restore_parser.add_argument(
        "backup_id",
        help="explicit backup identifier, e.g. b20260120T094000Z",
    )
    return parser


def _target_from_args(args: argparse.Namespace) -> DeploymentTarget:
    return DeploymentTarget.create(
        host=args.host,
        user=args.user,
        port=args.port,
        remote_base=args.remote_base,
        identity_file=args.identity_file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        project_name = args.project_name if args.project_name is not None else Path.cwd().name
        config_target = Path.cwd() / CONFIG_FILENAME
        try:
            config = DjangoOpsConfig.create(
                project_name=project_name,
                django_module=args.django_module,
                hostname=args.hostname,
                acme_email=args.acme_email,
                storage_endpoint_url=args.storage_endpoint_url,
                storage_region=args.storage_region,
                static_bucket=args.static_bucket,
                media_bucket=args.media_bucket,
                backup_bucket=args.backup_bucket,
                backup_schedule=args.backup_schedule,
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

    if args.command == "config-upgrade":
        path = Path(args.config)
        try:
            original = path.read_text(encoding="utf-8")
            rendered = upgrade_config(
                original,
                backup_bucket=args.backup_bucket,
                backup_schedule=args.backup_schedule,
            )
            path.write_text(rendered, encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Upgraded {path} to schema version 3")
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
            release_id = deploy_project(
                project_root=Path.cwd(),
                config_path=Path(args.config),
                compose_path=Path(args.compose_file),
                target=_target_from_args(args),
            )
        except (DeployError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Deployed release {release_id}")
        return 0

    if args.command == "health":
        try:
            report = health_project(
                _target_from_args(args),
                compose_file=args.compose_file,
            )
        except (HealthTransportError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(report.to_json())
        else:
            for check in report.checks:
                print(f"[{check.status.upper()}] {check.name}")
            print(f"Overall: {report.overall_status.upper()}")
        return 0 if report.healthy else 1

    if args.command in {"backup-schedule", "backup-run", "backup-list", "backup-restore"}:
        try:
            config = load_config(Path(args.config))
            target = _target_from_args(args)
            if args.command == "backup-schedule":
                install_backup_schedule(config, target, compose_file=args.compose_file)
                print("Backup schedule installed")
            elif args.command == "backup-run":
                print(run_backup(config, target))
            elif args.command == "backup-list":
                for record in list_backups(config, target):
                    print(record.backup_id)
            else:
                restore_backup(
                    config,
                    target,
                    args.backup_id,
                    compose_file=args.compose_file,
                )
                print(f"Restored backup {args.backup_id}")
        except (BackupError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
