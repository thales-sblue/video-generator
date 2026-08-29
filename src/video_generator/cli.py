"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from video_generator.adapters import (
    FFmpegError,
    MediaProbe,
    ProbeError,
    SegmentArtifact,
    extract_segment,
    probe_media,
)
from video_generator.config import ConfigurationError, load_config
from video_generator.doctor import format_report, run_doctor
from video_generator.domain import ContractError, EditPlan, RenderManifest
from video_generator.manifests import (
    ManifestError,
    build_segment_render_manifest,
    default_manifest_path,
    fingerprint_file,
    publish_render_manifest,
    validate_manifest_target,
)
from video_generator.validation import (
    ManifestValidationReport,
    PreflightReport,
    SegmentValidationReport,
    preflight_edit_plan,
    validate_render_manifest,
    validate_segment_artifact,
)
from video_generator.workflows import (
    SegmentWorkflowError,
    SegmentWorkflowReport,
    run_segment_workflow,
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
    extract = subparsers.add_parser(
        "extract-segment",
        help="copy one time range from local media into a new artifact",
    )
    extract.add_argument("source", help="path to an immutable local media source")
    extract.add_argument("output", help="new media artifact path; existing files are refused")
    extract.add_argument("--start-seconds", type=float, required=True, help="segment start in seconds")
    extract.add_argument("--end-seconds", type=float, required=True, help="segment end in seconds")
    extract.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    extract.add_argument("--json", action="store_true", help="print artifact metadata as JSON")
    execute_segment = subparsers.add_parser(
        "execute-segment-plan",
        help="execute one supported segment EditPlan through technical validation",
    )
    execute_segment.add_argument("plan", help="path to a persisted segment EditPlan JSON file")
    execute_segment.add_argument("--manifest", help="new RenderManifest JSON path")
    execute_segment.add_argument("--config", help="path to a fail-closed TOML configuration file")
    execute_segment.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    execute_segment.add_argument(
        "--duration-tolerance-seconds",
        type=float,
        default=0.1,
        help="maximum accepted artifact duration difference (default: 0.1)",
    )
    execute_segment.add_argument("--json", action="store_true", help="print a machine-readable report")
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
    validate_manifest = subparsers.add_parser(
        "validate-manifest",
        help="verify a RenderManifest against its plan and current local files",
    )
    validate_manifest.add_argument(
        "manifest",
        help="path to a persisted RenderManifest JSON file",
    )
    validate_manifest.add_argument(
        "--plan",
        required=True,
        help="path to the corresponding EditPlan JSON file",
    )
    validate_manifest.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable report",
    )
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


def _format_segment_artifact(artifact: SegmentArtifact) -> str:
    return "\n".join(
        [
            f"Source: {artifact.source_path}",
            f"Artifact: {artifact.output_path}",
            f"Range: {artifact.start_seconds} to {artifact.end_seconds} seconds",
            f"Size: {artifact.file_size_bytes} bytes",
        ]
    ) + "\n"


def _format_segment_workflow(report: SegmentWorkflowReport, manifest_path: Path) -> str:
    return "\n".join(
        [
            f"Plan: {report.plan_id}",
            "Workflow: segment-extract",
            f"Operation: {report.operation_id}",
            f"Artifact: {report.artifact.output_path}",
            f"Preflight: {'valid' if report.preflight.valid else 'INVALID'}",
            f"Technical validation: {'valid' if report.validation.valid else 'INVALID'}",
            f"RenderManifest: {manifest_path}",
        ]
    ) + "\n"


def _format_manifest_validation(report: ManifestValidationReport) -> str:
    lines = [
        f"RenderManifest: {report.manifest_id}",
        f"Integrity: {'valid' if report.integrity_valid else 'INVALID'}",
        (
            "Recorded technical validation: "
            f"{'valid' if report.technical_validation_valid else 'INVALID'}"
        ),
        f"Technically ready: {'yes' if report.technically_ready else 'NO'}",
        f"Editorial review: {report.editorial_review}",
    ]
    for issue in report.issues:
        suffix = f" ({issue.path})" if issue.path else ""
        lines.append(f"  [{issue.code}] {issue.message}{suffix}")
    for code in report.recorded_technical_issues:
        lines.append(f"  [recorded:{code}] technical validation issue recorded during execution")
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


def _load_render_manifest(path: str) -> RenderManifest:
    manifest_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read render manifest: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"render manifest is not valid JSON: {manifest_path}") from exc
    return RenderManifest.from_dict(payload)


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
    if args.command == "extract-segment":
        try:
            artifact = extract_segment(
                args.source,
                args.output,
                start_seconds=args.start_seconds,
                end_seconds=args.end_seconds,
                timeout_seconds=args.timeout_seconds,
            )
        except FFmpegError as exc:
            print(f"Extraction error: {exc}", file=sys.stderr)
            return 2
        output = json.dumps(asdict(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        print(output if args.json else _format_segment_artifact(artifact), end="")
        return 0
    if args.command == "execute-segment-plan":
        try:
            plan = _load_edit_plan(args.plan)
            manifest_path = validate_manifest_target(
                args.manifest or default_manifest_path(plan.output_path),
                forbidden_paths=(*plan.sources, plan.output_path),
            )
            config = load_config(args.config)
            doctor = run_doctor(config)
            source_fingerprints = []

            def capture_sources(execution_plan: EditPlan) -> None:
                source_fingerprints.extend(
                    fingerprint_file(source) for source in execution_plan.sources
                )

            report = run_segment_workflow(
                plan,
                timeout_seconds=args.timeout_seconds,
                duration_tolerance_seconds=args.duration_tolerance_seconds,
                before_extract=capture_sources,
            )
            manifest = build_segment_render_manifest(
                plan,
                report,
                doctor,
                tuple(source_fingerprints),
            )
            published_manifest = publish_render_manifest(manifest, manifest_path)
        except (ConfigurationError, ContractError, ManifestError, SegmentWorkflowError) as exc:
            print(f"Segment workflow error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            payload = report.to_dict()
            payload["manifest_path"] = str(published_manifest)
            payload["manifest"] = manifest.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(_format_segment_workflow(report, published_manifest), end="")
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
    if args.command == "validate-manifest":
        try:
            manifest = _load_render_manifest(args.manifest)
            plan = _load_edit_plan(args.plan)
            report = validate_render_manifest(manifest, plan)
        except ContractError as exc:
            print(f"Manifest validation error: {exc}", file=sys.stderr)
            return 2
        print(report.to_json() if args.json else _format_manifest_validation(report), end="")
        return 0 if report.technically_ready else 1
    return 2
