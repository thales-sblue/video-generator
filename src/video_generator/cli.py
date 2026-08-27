"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from video_generator.adapters import MediaProbe, ProbeError, probe_media
from video_generator.config import ConfigurationError, load_config
from video_generator.doctor import format_report, run_doctor
from video_generator.domain import ContractError, EditPlan
from video_generator.validation import PreflightReport, preflight_edit_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-generator",
        description="Local-only audiovisual production foundation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="inspect local dependencies without changing the system")
    doctor.add_argument("--json", action="store_true", help="print a machine-readable report")
    doctor.add_argument("--config", help="path to a TOML configuration file")
    inspect = subparsers.add_parser("inspect", help="inspect one local media file with ffprobe")
    inspect.add_argument("source", help="path to an immutable local media source")
    inspect.add_argument("--json", action="store_true", help="print a machine-readable report")
    preflight = subparsers.add_parser("preflight", help="validate a persisted edit plan against its media")
    preflight.add_argument("plan", help="path to an EditPlan JSON file")
    preflight.add_argument("--json", action="store_true", help="print a machine-readable report")
    return parser


def _format_probe(probe: MediaProbe) -> str:
    lines = [
        f"Source: {probe.source_path}",
        f"Size: {probe.file_size_bytes} bytes",
        f"Format: {probe.format_name or 'unknown'}",
        f"Duration: {probe.duration_seconds if probe.duration_seconds is not None else 'unknown'} seconds",
        f"Bit rate: {probe.bit_rate_bps if probe.bit_rate_bps is not None else 'unknown'} bps",
        f"Streams: {len(probe.streams)}",
    ]
    for stream in probe.streams:
        details = [stream.codec_type or "unknown", stream.codec_name or "unknown"]
        if stream.width is not None and stream.height is not None:
            details.append(f"{stream.width}x{stream.height}")
        if stream.sample_rate_hz is not None:
            details.append(f"{stream.sample_rate_hz} Hz")
        if stream.channels is not None:
            details.append(f"{stream.channels} channels")
        lines.append(f"  [{stream.index}] {'; '.join(details)}")
    return "\n".join(lines) + "\n"


def _format_preflight(report: PreflightReport) -> str:
    lines = [
        f"Plan: {report.plan_id}",
        f"Preflight: {'valid' if report.valid else 'INVALID'}",
        f"Sources inspected: {len(report.sources)}",
    ]
    for issue in report.issues:
        context = [item for item in (issue.source, issue.operation_id) if item]
        suffix = f" ({', '.join(context)})" if context else ""
        lines.append(f"  [{issue.code}] {issue.message}{suffix}")
    return "\n".join(lines) + "\n"


def _load_edit_plan(path: str) -> EditPlan:
    plan_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read edit plan: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"edit plan is not valid JSON: {plan_path}") from exc
    return EditPlan.from_dict(payload)


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
    if args.command == "inspect":
        try:
            probe = probe_media(args.source)
        except ProbeError as exc:
            print(f"Inspection error: {exc}", file=sys.stderr)
            return 2
        print(probe.to_json() if args.json else _format_probe(probe), end="")
        return 0
    if args.command == "preflight":
        try:
            plan = _load_edit_plan(args.plan)
            report = preflight_edit_plan(plan)
        except ContractError as exc:
            print(f"Plan error: {exc}", file=sys.stderr)
            return 2
        print(report.to_json() if args.json else _format_preflight(report), end="")
        return 0 if report.valid else 1
    return 2
