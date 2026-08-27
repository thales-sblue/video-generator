"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from video_generator.config import ConfigurationError, load_config
from video_generator.doctor import format_report, run_doctor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-generator",
        description="Local-only audiovisual production foundation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="inspect local dependencies without changing the system")
    doctor.add_argument("--json", action="store_true", help="print a machine-readable report")
    doctor.add_argument("--config", help="path to a TOML configuration file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        try:
            config = load_config(args.config)
        except ConfigurationError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
        report = run_doctor(config)
        print(report.to_json() if args.json else format_report(report), end="")
        return 0
    return 2
