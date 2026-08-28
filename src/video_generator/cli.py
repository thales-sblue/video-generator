"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from video_generator.adapters import MediaProbe, ProbeError, SegmentArtifact, probe_media
from video_generator.config import ConfigurationError, load_config
from video_generator.doctor import format_report, run_doctor
from video_generator.domain import ContractError, EditPlan
from video_generator.validation import (
    PreflightReport,
    SegmentValidationReport,
    preflight_edit_plan,
    validate_segment_artifact,
)


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
    validate_segment = subparsers.add_parser(
        "validate-segment",
        help="verify one extracted segment artifact with ffprobe",
    )
    validate_segment.add_argument("output", help="path to the extracted segment artifact")
    validate_segment.add_argument("--source", required=True, help="immutable source path used for extraction")
    validate_segment.add_argument("--start-seconds", type=float, required=True, help="requested segment start")
    validate_segment.add_argument("--end-seconds", type=float, required=True, help="requested segment end")
    validate_segment.add_argument(
        "--file-size-bytes",
        type=int,
        required=True,
        help="artifact size recorded immediately after extraction",
    )
    validate_segment.add_argument(
        "--duration-tolerance-seconds",
        type=float,
        default=0.1,
        help="maximum accepted duration difference (default: 0.1)",
    )
    validate_segment.add_argument("--json", action="store_true", help="print a machine-readable report")
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


def _format_segment_validation(report: SegmentValidationReport) -> str:
    lines = [
        f"Artifact: {report.artifact.output_path}",
        f"Segment validation: {'valid' if report.valid else 'INVALID'}",
        f"Expected duration: {report.expected_duration_seconds} seconds",
        (
            "Actual duration: "
            f"{report.actual_duration_seconds if report.actual_duration_seconds is not None else 'unknown'} seconds"
        ),
    ]
    for issue in report.issues:
        lines.append(f"  [{issue.code}] {issue.message}")
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


def _segment_artifact_from_args(args: argparse.Namespace) -> SegmentArtifact:
    numeric_values = (args.start_seconds, args.end_seconds)
    if any(not math.isfinite(value) or value < 0 for value in numeric_values):
        raise ValueError("start_seconds and end_seconds must be finite non-negative numbers")
    if args.end_seconds <= args.start_seconds:
        raise ValueError("end_seconds must be greater than start_seconds")
    if args.file_size_bytes < 0:
        raise ValueError("file_size_bytes must be non-negative")
    source_path = str(Path(args.source).expanduser().resolve())
    output_path = str(Path(args.output).expanduser().resolve())
    if os.path.normcase(source_path) == os.path.normcase(output_path):
        raise ValueError("output must not be the source path")
    return SegmentArtifact(
        source_path=source_path,
        output_path=output_path,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
        file_size_bytes=args.file_size_bytes,
    )


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
    if args.command == "validate-segment":
        try:
            artifact = _segment_artifact_from_args(args)
            report = validate_segment_artifact(
                artifact,
                duration_tolerance_seconds=args.duration_tolerance_seconds,
            )
        except (TypeError, ValueError) as exc:
            print(f"Segment validation error: {exc}", file=sys.stderr)
            return 2
        print(report.to_json() if args.json else _format_segment_validation(report), end="")
        return 0 if report.valid else 1
    return 2
