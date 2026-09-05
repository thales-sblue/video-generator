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
    AlignerError,
    AudioArtifact,
    FFmpegError,
    KokoroError,
    MediaProbe,
    NarrationArtifact,
    ProbeError,
    SegmentArtifact,
    detect_silences,
    extract_audio,
    extract_segment,
    probe_media,
    synthesize_narration,
    transcribe_words,
)
from video_generator.config import ConfigurationError, load_config
from video_generator.narration import NarrationError, render_prosodic_narration
from video_generator.subtitles import (
    SubtitleParseError,
    cues_from_word_timings,
    parse_subtitle_cues,
    render_srt,
)
from video_generator.doctor import format_report, run_doctor
from video_generator.domain import (
    DARK_DOCUMENTARY_V1,
    DEFAULT_HOOK_POLICY,
    ContractError,
    DirectionError,
    EditOperation,
    EditPlan,
    HookPolicy,
    NarrativeScript,
    PlanningError,
    RenderManifest,
    RhythmPolicy,
    ScenePlan,
    TargetFormat,
    VideoBrief,
    VideoRequest,
    VisualDirectionPolicy,
    apply_overrides,
    plan_scenes,
    plan_shot_motion_typography,
    plan_shot_text_events,
    plan_shot_visual_direction,
    plan_shots,
    shot_plan_to_edit_plan,
    text_events_operation,
    visual_direction_operation,
)
from video_generator.domain.typography import (
    DEFAULT_TYPOGRAPHY_POLICY,
    MotionTypographyPolicy,
    TypographyError,
    motion_typography_operation,
)
from video_generator.manifests import (
    ManifestError,
    build_segment_render_manifest,
    build_sequence_render_manifest,
    default_manifest_path,
    fingerprint_file,
    publish_render_manifest,
    validate_manifest_target,
)
from video_generator.validation import (
    AudioValidationReport,
    ManifestValidationReport,
    PreflightReport,
    ProjectValidationReport,
    SegmentValidationReport,
    preflight_edit_plan,
    validate_audio_artifact,
    validate_render_manifest,
    validate_project_chain,
    validate_segment_artifact,
)
from video_generator.workflows import (
    SegmentWorkflowError,
    SegmentWorkflowReport,
    SequenceWorkflowError,
    SequenceWorkflowReport,
    run_final_sequence_workflow,
    run_segment_workflow,
    run_sequence_workflow,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-generator",
        description="Local-first audiovisual production engine",
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
        help="extract one time range from local media into a new artifact",
    )
    extract.add_argument("source", help="path to an immutable local media source")
    extract.add_argument("output", help="new media artifact path; existing files are refused")
    extract.add_argument("--start-seconds", type=float, required=True, help="segment start in seconds")
    extract.add_argument("--end-seconds", type=float, required=True, help="segment end in seconds")
    extract.add_argument(
        "--mode",
        choices=("copy", "precise"),
        default="copy",
        help="stream copy or precise H.264/AAC reencode (default: copy)",
    )
    extract.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    extract.add_argument("--json", action="store_true", help="print artifact metadata as JSON")
    audio = subparsers.add_parser(
        "extract-audio",
        help="extract the first audio stream into a new 48 kHz stereo PCM WAV artifact",
    )
    audio.add_argument("source", help="path to an immutable local media source")
    audio.add_argument("output", help="new .wav artifact path; existing files are refused")
    audio.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    audio.add_argument("--json", action="store_true", help="print artifact metadata as JSON")
    narrate = subparsers.add_parser(
        "narrate",
        help="synthesise local narration from text into a 48 kHz stereo PCM WAV (optional Kokoro)",
    )
    narrate.add_argument("output", help="new .wav artifact path; existing files are refused")
    narrate_text = narrate.add_mutually_exclusive_group(required=True)
    narrate_text.add_argument("--text", help="narration text to synthesise")
    narrate_text.add_argument("--text-file", help="path to a UTF-8 file with the narration text")
    narrate.add_argument("--voice", default="af_heart", help="Kokoro voice token (default: af_heart)")
    narrate.add_argument("--speed", type=float, default=1.0, help="speech rate 0.5-2.0 (default: 1.0)")
    narrate.add_argument("--lang", default="en-us", help="language code (default: en-us)")
    narrate.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum normalisation time (default: 300)",
    )
    narrate.add_argument(
        "--prosody",
        action="store_true",
        help="speak the script unit by unit with planned pauses instead of one flat block",
    )
    narrate.add_argument(
        "--lead-in-seconds",
        type=float,
        default=0.0,
        help="silence before the first word, with --prosody (default: 0)",
    )
    narrate.add_argument(
        "--units-out",
        help="with --prosody, also write the measured per-unit layout as JSON here",
    )
    narrate.add_argument("--json", action="store_true", help="print artifact metadata as JSON")
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
    execute_sequence = subparsers.add_parser(
        "execute-sequence-plan",
        help="compose clips with optional captions, narration, and music into one MP4",
    )
    execute_sequence.add_argument("plan", help="path to a persisted sequence EditPlan JSON file")
    execute_sequence.add_argument("--manifest", help="new RenderManifest JSON path")
    execute_sequence.add_argument("--config", help="path to a fail-closed TOML configuration file")
    execute_sequence.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    execute_sequence.add_argument(
        "--duration-tolerance-seconds",
        type=float,
        default=0.15,
        help="maximum accepted artifact duration difference (default: 0.15)",
    )
    execute_sequence.add_argument("--json", action="store_true", help="print a machine-readable report")
    execute_final_sequence = subparsers.add_parser(
        "execute-final-sequence-plan",
        help="stage, validate, and exclusively publish a sequence as final.mp4",
    )
    execute_final_sequence.add_argument(
        "plan", help="persisted sequence EditPlan whose output is named final.mp4"
    )
    execute_final_sequence.add_argument("--manifest", help="new RenderManifest JSON path")
    execute_final_sequence.add_argument(
        "--config", help="path to a fail-closed TOML configuration file"
    )
    execute_final_sequence.add_argument(
        "--timeout-seconds",
        type=float,
        default=300,
        help="maximum FFmpeg execution time (default: 300)",
    )
    execute_final_sequence.add_argument(
        "--duration-tolerance-seconds",
        type=float,
        default=0.15,
        help="maximum accepted artifact duration difference (default: 0.15)",
    )
    execute_final_sequence.add_argument(
        "--json", action="store_true", help="print a machine-readable report"
    )
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
    validate_audio = subparsers.add_parser(
        "validate-audio",
        help="verify one extracted PCM WAV artifact with ffprobe",
    )
    validate_audio.add_argument("output", help="path to the extracted WAV artifact")
    validate_audio.add_argument(
        "--source",
        required=True,
        help="immutable source path used for extraction",
    )
    validate_audio.add_argument(
        "--file-size-bytes",
        type=int,
        required=True,
        help="artifact size recorded immediately after extraction",
    )
    validate_audio.add_argument("--json", action="store_true", help="print a machine-readable report")
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
    validate_project = subparsers.add_parser(
        "validate-project",
        help="verify the complete persisted request-to-render chain",
    )
    validate_project.add_argument("--request", required=True, help="VideoRequest JSON path")
    validate_project.add_argument("--brief", required=True, help="VideoBrief JSON path")
    validate_project.add_argument("--plan", required=True, help="EditPlan JSON path")
    validate_project.add_argument("--manifest", required=True, help="RenderManifest JSON path")
    validate_project.add_argument("--json", action="store_true", help="print a machine-readable report")

    plan_scenes_cmd = subparsers.add_parser(
        "plan-scenes",
        help="turn a narrated script into a dense scene/shot/asset plan (pure, deterministic)",
    )
    plan_scenes_source = plan_scenes_cmd.add_mutually_exclusive_group(required=True)
    plan_scenes_source.add_argument(
        "--from-text", help="UTF-8 roteiro; paragraphs (blank-line separated) become blocks"
    )
    plan_scenes_source.add_argument("--script", help="a persisted NarrativeScript JSON path")
    plan_scenes_cmd.add_argument(
        "--total-duration", type=float, help="measured narration length in seconds (with --from-text)"
    )
    plan_scenes_cmd.add_argument("--policy", help="a partial RhythmPolicy JSON path (merged over defaults)")
    plan_scenes_cmd.add_argument("--overrides", help="an editorial overrides JSON path")
    plan_scenes_cmd.add_argument("--seed", type=int, default=0, help="deterministic seed (default: 0)")
    plan_scenes_cmd.add_argument(
        "--target", required=True, help="delivery canvas WxH[:fit], e.g. 1920x1080:cover"
    )
    plan_scenes_cmd.add_argument("--out-dir", required=True, help="directory for the three JSON artifacts")
    plan_scenes_cmd.add_argument(
        "--emit-edit-plan", help="also convert the shot plan to an EditPlan at this path (needs --assets)"
    )
    plan_scenes_cmd.add_argument(
        "--assets", help="a JSON object mapping every asset_id to a local file path"
    )
    plan_scenes_cmd.add_argument(
        "--semantic",
        action="store_true",
        help="read every narration slice as an editorial beat and let it drive "
        "the visual query, the fallback queries and the shot type",
    )
    plan_scenes_cmd.add_argument(
        "--visual-relevance",
        action="store_true",
        help="Semantic Visual Relevance v1: also read each beat's visual intent "
        "class and visual role, refine its English query with both, and carry "
        "them to the asset requirements (needs --semantic)",
    )
    plan_scenes_cmd.add_argument(
        "--editorial-translation",
        action="store_true",
        help="Editorial Visual Translation v1: ask every beat what could be "
        "filmed to communicate it and search for that scene in English, "
        "instead of for whichever noun the slice contained; also read each "
        "candidate positively (needs --visual-relevance)",
    )
    plan_scenes_cmd.add_argument(
        "--hook-seconds",
        type=float,
        help="give the opening its own tighter rhythm and no asset reuse "
        "(needs --semantic)",
    )
    plan_scenes_cmd.add_argument(
        "--text-events",
        help="also write the editorial emphasis layer as JSON at this path "
        "(needs --semantic)",
    )
    plan_scenes_cmd.add_argument(
        "--motion-typography",
        help="Editorial Motion Typography v1: compose the on-screen type as "
        "layouts of several weighted blocks instead of an emphasis line, write "
        "the plan as JSON at this path, and carry it into the EditPlan in "
        "place of text_events (needs --semantic)",
    )
    plan_scenes_cmd.add_argument(
        "--typography-policy",
        help="a MotionTypographyPolicy JSON path, optionally with a 'style' "
        "object carrying display_font / support_font / muted",
    )
    plan_scenes_cmd.add_argument(
        "--narration-captions",
        help="a force-aligned .srt/.vtt of the same script, so each "
        "typographic event lands on the word it is about rather than on the "
        "shot that contains it",
    )
    plan_scenes_cmd.add_argument(
        "--visual-direction",
        help="Visual Direction v1: a VisualDirectionPolicy JSON path. Decides "
        "how each chosen asset appears — composition, editorial motion, grade "
        "intensity and motif — writes visual-direction.json, and carries the "
        "decisions into the EditPlan (needs --semantic)",
    )
    plan_scenes_cmd.add_argument(
        "--no-luma",
        action="store_true",
        help="do not measure asset brightness with FFmpeg; every shot then "
        "gets the policy's default grade intensity",
    )
    plan_scenes_cmd.add_argument(
        "--force", action="store_true", help="overwrite existing output files"
    )
    plan_scenes_cmd.add_argument("--json", action="store_true", help="print the summary as JSON")

    align_captions_cmd = subparsers.add_parser(
        "align-captions",
        help="time a script as captions against a rendered narration WAV (optional Whisper)",
    )
    align_captions_cmd.add_argument("audio", help="the rendered narration .wav to measure")
    align_captions_text = align_captions_cmd.add_mutually_exclusive_group(required=True)
    align_captions_text.add_argument("--text", help="the spoken script")
    align_captions_text.add_argument("--text-file", help="path to a UTF-8 file with the script")
    align_captions_cmd.add_argument("--out", required=True, help="new .srt path; existing files are refused")
    align_captions_cmd.add_argument("--language", default="pt", help="spoken language code (default: pt)")
    align_captions_cmd.add_argument("--model", help="Whisper model directory name under .local-tools/whisper")
    align_captions_cmd.add_argument(
        "--words-out", help="also write the raw measured word timings as JSON here"
    )
    align_captions_cmd.add_argument("--json", action="store_true", help="print the summary as JSON")

    resolve_assets_cmd = subparsers.add_parser(
        "resolve-assets",
        help="resolve every AssetRequirement to a concrete local file with provenance",
    )
    resolve_assets_cmd.add_argument("--shot-plan", required=True, help="a persisted ShotPlan JSON path")
    resolve_assets_cmd.add_argument(
        "--asset-requirements", required=True, help="a persisted AssetRequirements JSON path"
    )
    resolve_assets_cmd.add_argument(
        "--library", required=True, help="local asset library root (indexed offline)"
    )
    resolve_assets_cmd.add_argument("--out-dir", required=True, help="directory for the resolution artifacts")
    resolve_assets_cmd.add_argument(
        "--providers",
        default="local",
        help="comma list of providers to consult (local, pexels, pixabay); default: local",
    )
    resolve_assets_cmd.add_argument(
        "--scoring-policy", help="a partial AssetScoringPolicy JSON path (merged over defaults)"
    )
    resolve_assets_cmd.add_argument(
        "--bindings-out", help="where to also write asset-bindings.json (default: <out-dir>/asset-bindings.json)"
    )
    resolve_assets_cmd.add_argument("--seed", type=int, default=0, help="accepted for reproducibility bookkeeping")
    resolve_assets_cmd.add_argument(
        "--no-reuse-review", action="store_true", help="skip the semantic reuse-compatibility check"
    )
    resolve_assets_cmd.add_argument(
        "--no-probe", action="store_true", help="do not probe acquired files with ffprobe"
    )
    resolve_assets_cmd.add_argument(
        "--require-complete", action="store_true", help="exit 3 if any requirement is unresolved"
    )
    resolve_assets_cmd.add_argument("--force", action="store_true", help="overwrite existing artifacts")
    resolve_assets_cmd.add_argument("--json", action="store_true", help="print the summary as JSON")
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
            f"Mode: {artifact.mode}",
            f"Size: {artifact.file_size_bytes} bytes",
        ]
    ) + "\n"


def _format_audio_artifact(artifact: AudioArtifact) -> str:
    return "\n".join(
        [
            f"Source: {artifact.source_path}",
            f"Artifact: {artifact.output_path}",
            "Audio: PCM 16-bit; "
            f"{artifact.sample_rate_hz} Hz; {artifact.channels} channels",
            f"Size: {artifact.file_size_bytes} bytes",
        ]
    ) + "\n"


def _format_narration_artifact(artifact: NarrationArtifact) -> str:
    return "\n".join(
        [
            f"Artifact: {artifact.output_path}",
            f"Voice: {artifact.voice}; speed {artifact.speed}; lang {artifact.lang}",
            "Audio: PCM 16-bit; "
            f"{artifact.sample_rate_hz} Hz; {artifact.channels} channels",
            f"Text SHA-256: {artifact.text_sha256}",
            f"Size: {artifact.file_size_bytes} bytes",
            "Auditory review: not_performed",
        ]
    ) + "\n"


def _format_audio_validation(report: AudioValidationReport) -> str:
    lines = [
        f"Artifact: {report.artifact.output_path}",
        f"Audio validation: {'valid' if report.valid else 'INVALID'}",
        f"Expected audio: pcm_s16le; {report.artifact.sample_rate_hz} Hz; "
        f"{report.artifact.channels} channels",
    ]
    for issue in report.issues:
        lines.append(f"  [{issue.code}] {issue.message}")
    return "\n".join(lines) + "\n"


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


def _format_sequence_workflow(report: SequenceWorkflowReport, manifest_path: Path) -> str:
    return "\n".join(
        [
            f"Plan: {report.plan_id}",
            "Workflow: video-sequence",
            f"Timeline operations: {len(report.operation_ids)}",
            f"Images: {report.artifact.image_count}",
            "Narration: "
            + (
                "from text"
                if report.artifact.narration_text_sha256
                else "included"
                if report.artifact.narration_source_path
                else "not included"
            )
            + (
                f" after {report.artifact.narration_lead_in_seconds}s"
                if report.artifact.narration_lead_in_seconds
                else ""
            ),
            f"Captions: {report.artifact.caption_count}",
            "Music: "
            + ("included" if report.artifact.music_source_path else "not included")
            + (
                f", ducked {report.artifact.music_duck_db}dB under the voice"
                if report.artifact.music_duck_db is not None
                else ""
            ),
            "Fades: "
            + (
                f"from black {report.artifact.video_fade_in_seconds}s / "
                f"to black {report.artifact.video_fade_out_seconds}s"
                if report.artifact.video_fade_in_seconds
                or report.artifact.video_fade_out_seconds
                else "not applied"
            ),
            f"Publication: {report.publication}",
            f"Artifact: {report.artifact.output_path}",
            f"Duration: {report.artifact.duration_seconds} seconds",
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


def _format_project_validation(report: ProjectValidationReport) -> str:
    lines = [
        f"Request: {report.request_id}",
        f"Brief: {report.brief_id}",
        f"Plan: {report.plan_id}",
        f"RenderManifest: {report.manifest_id}",
        f"Traceability: {'valid' if report.trace_valid else 'INVALID'}",
        (
            "Manifest integrity: "
            f"{'valid' if report.manifest_validation.integrity_valid else 'INVALID'}"
        ),
        f"Technically ready: {'yes' if report.technically_ready else 'NO'}",
        f"Editorial review: {report.editorial_review}",
    ]
    for issue in report.issues:
        suffix = f" ({issue.path})" if issue.path else ""
        lines.append(f"  [{issue.code}] {issue.message}{suffix}")
    for issue in report.manifest_validation.issues:
        suffix = f" ({issue.path})" if issue.path else ""
        lines.append(f"  [manifest:{issue.code}] {issue.message}{suffix}")
    for code in report.manifest_validation.recorded_technical_issues:
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


def _load_video_request(path: str) -> VideoRequest:
    request_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read video request: {request_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"video request is not valid JSON: {request_path}") from exc
    return VideoRequest.from_dict(payload)


def _load_video_brief(path: str) -> VideoBrief:
    brief_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(brief_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read video brief: {brief_path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"video brief is not valid JSON: {brief_path}") from exc
    return VideoBrief.from_dict(payload)


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


def _audio_artifact_from_args(args: argparse.Namespace) -> AudioArtifact:
    if args.file_size_bytes <= 0:
        raise ValueError("file_size_bytes must be positive")
    source_path = str(Path(args.source).expanduser().resolve())
    output_path = str(Path(args.output).expanduser().resolve())
    if os.path.normcase(source_path) == os.path.normcase(output_path):
        raise ValueError("output must not be the source path")
    if Path(output_path).suffix.lower() != ".wav":
        raise ValueError("output must use the .wav extension")
    return AudioArtifact(
        source_path=source_path,
        output_path=output_path,
        sample_rate_hz=48000,
        channels=2,
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
                mode=args.mode,
            )
        except FFmpegError as exc:
            print(f"Extraction error: {exc}", file=sys.stderr)
            return 2
        output = json.dumps(asdict(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        print(output if args.json else _format_segment_artifact(artifact), end="")
        return 0
    if args.command == "extract-audio":
        try:
            artifact = extract_audio(
                args.source,
                args.output,
                timeout_seconds=args.timeout_seconds,
            )
        except FFmpegError as exc:
            print(f"Audio extraction error: {exc}", file=sys.stderr)
            return 2
        output = json.dumps(asdict(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        print(output if args.json else _format_audio_artifact(artifact), end="")
        return 0
    if args.command == "narrate":
        if args.text_file is not None:
            try:
                text = Path(args.text_file).expanduser().read_text(encoding="utf-8")
            except OSError as exc:
                print(f"Narration error: cannot read text file: {exc}", file=sys.stderr)
                return 2
            except UnicodeDecodeError:
                print("Narration error: text file must be UTF-8", file=sys.stderr)
                return 2
        else:
            text = args.text
        if args.prosody:
            try:
                narration = render_prosodic_narration(
                    text,
                    args.output,
                    voice=args.voice,
                    lang=args.lang,
                    base_speed=args.speed,
                    lead_in_seconds=args.lead_in_seconds,
                    timeout_seconds=args.timeout_seconds,
                )
            except NarrationError as exc:
                print(f"Narration error: {exc}", file=sys.stderr)
                return 2
            payload = narration.to_dict()
            if args.units_out:
                try:
                    Path(args.units_out).expanduser().write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except OSError as exc:
                    print(f"Narration error: cannot write units: {exc}", file=sys.stderr)
                    return 2
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"Narration: {narration.output_path}")
                print(f"Units: {len(narration.units)} ({narration.voice}, {narration.lang})")
                print(
                    f"Duration: {narration.total_seconds:.3f} s "
                    f"({narration.spoken_seconds:.3f} s spoken, "
                    f"{narration.pause_seconds:.3f} s planned pause)"
                )
            return 0
        try:
            artifact = synthesize_narration(
                text,
                args.output,
                voice=args.voice,
                speed=args.speed,
                lang=args.lang,
                timeout_seconds=args.timeout_seconds,
            )
        except KokoroError as exc:
            print(f"Narration error: {exc}", file=sys.stderr)
            return 2
        output = json.dumps(asdict(artifact), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        print(output if args.json else _format_narration_artifact(artifact), end="")
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
    if args.command in {"execute-sequence-plan", "execute-final-sequence-plan"}:
        final_execution = args.command == "execute-final-sequence-plan"
        report = None
        try:
            plan = _load_edit_plan(args.plan)
            if not final_execution and Path(plan.output_path).name.lower() == "final.mp4":
                raise SequenceWorkflowError(
                    "final.mp4 must be published with execute-final-sequence-plan"
                )
            manifest_path = validate_manifest_target(
                args.manifest or default_manifest_path(plan.output_path),
                forbidden_paths=(*plan.sources, plan.output_path),
            )
            config = load_config(args.config)
            doctor = run_doctor(config)
            source_fingerprints = []

            def capture_sequence_sources(execution_plan: EditPlan) -> None:
                source_fingerprints.extend(
                    fingerprint_file(source) for source in execution_plan.sources
                )

            execute_workflow = (
                run_final_sequence_workflow if final_execution else run_sequence_workflow
            )
            report = execute_workflow(
                plan,
                timeout_seconds=args.timeout_seconds,
                duration_tolerance_seconds=args.duration_tolerance_seconds,
                before_compose=capture_sequence_sources,
            )
            manifest = build_sequence_render_manifest(
                plan,
                report,
                doctor,
                tuple(source_fingerprints),
            )
            published_manifest = publish_render_manifest(manifest, manifest_path)
        except (ConfigurationError, ContractError, ManifestError, SequenceWorkflowError) as exc:
            if final_execution and report is not None and report.publication == "final":
                try:
                    Path(report.artifact.output_path).unlink(missing_ok=True)
                except OSError as cleanup_error:
                    print(
                        f"Sequence workflow error: {exc}; could not remove unmanifested "
                        f"final output: {cleanup_error}",
                        file=sys.stderr,
                    )
                    return 2
            print(f"Sequence workflow error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            payload = report.to_dict()
            payload["manifest_path"] = str(published_manifest)
            payload["manifest"] = manifest.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(_format_sequence_workflow(report, published_manifest), end="")
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
    if args.command == "validate-audio":
        try:
            artifact = _audio_artifact_from_args(args)
            report = validate_audio_artifact(artifact)
        except (TypeError, ValueError) as exc:
            print(f"Audio validation error: {exc}", file=sys.stderr)
            return 2
        print(report.to_json() if args.json else _format_audio_validation(report), end="")
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
    if args.command == "validate-project":
        try:
            request = _load_video_request(args.request)
            brief = _load_video_brief(args.brief)
            plan = _load_edit_plan(args.plan)
            manifest = _load_render_manifest(args.manifest)
            report = validate_project_chain(request, brief, plan, manifest)
        except ContractError as exc:
            print(f"Project validation error: {exc}", file=sys.stderr)
            return 2
        print(report.to_json() if args.json else _format_project_validation(report), end="")
        return 0 if report.technically_ready else 1
    if args.command == "align-captions":
        return _run_align_captions(args)
    if args.command == "plan-scenes":
        return _run_plan_scenes(args)
    if args.command == "resolve-assets":
        return _run_resolve_assets(args)
    return 2


def _run_align_captions(args: argparse.Namespace) -> int:
    if args.text_file is not None:
        try:
            text = Path(args.text_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Alignment error: cannot read text file: {exc}", file=sys.stderr)
            return 2
        except UnicodeDecodeError:
            print("Alignment error: text file must be UTF-8", file=sys.stderr)
            return 2
    else:
        text = args.text

    destination = Path(args.out).expanduser()
    if destination.suffix.lower() != ".srt":
        print("Alignment error: --out must be a .srt path", file=sys.stderr)
        return 2
    if destination.exists():
        print(f"Alignment error: output already exists: {destination}", file=sys.stderr)
        return 2

    try:
        probe = probe_media(args.audio)
    except ProbeError as exc:
        print(f"Alignment error: cannot inspect the narration: {exc}", file=sys.stderr)
        return 2
    if probe.duration_seconds is None or probe.duration_seconds <= 0:
        print("Alignment error: the narration has no usable duration", file=sys.stderr)
        return 2

    try:
        words = transcribe_words(args.audio, language=args.language, model_name=args.model)
    except AlignerError as exc:
        print(f"Alignment error: {exc}", file=sys.stderr)
        return 2
    # A decoder routinely reports its first word as starting at 0.0, even when
    # the take opens on silence: that would put the first caption on screen
    # before anyone speaks. Silence detection measures where the voice actually
    # starts, so trust it over the transcript for that one edge.
    voice_start = 0.0
    try:
        silences = detect_silences(args.audio)
    except FFmpegError:
        silences = ()
    for silence_start, silence_end in silences:
        if silence_start <= 0.01:
            voice_start = max(voice_start, silence_end)
        break
    # clamping every word (not just the first) keeps the sequence ordered; only
    # the words the decoder placed inside the opening silence actually move
    triples = tuple(
        (word.text, max(word.start_seconds, voice_start), max(word.end_seconds, voice_start))
        for word in words
    )
    try:
        cues = cues_from_word_timings(text, triples, total_seconds=float(probe.duration_seconds))
        srt = render_srt(cues)
    except SubtitleParseError as exc:
        print(f"Alignment error: {exc}", file=sys.stderr)
        return 2

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(srt, encoding="utf-8")
        if args.words_out:
            Path(args.words_out).expanduser().write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "audio_path": str(Path(args.audio).expanduser().resolve()),
                        "language": args.language,
                        "audio_seconds": round(float(probe.duration_seconds), 3),
                        "words": [word.to_dict() for word in words],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    except OSError as exc:
        print(f"Alignment error: cannot write output: {exc}", file=sys.stderr)
        return 2

    summary = {
        "captions_path": str(destination.resolve()),
        "cue_count": len(cues),
        "measured_words": len(words),
        "audio_seconds": round(float(probe.duration_seconds), 3),
        "voice_start_seconds": round(voice_start, 3),
        "first_cue_start_seconds": round(cues[0][1], 3),
        "last_cue_end_seconds": round(cues[-1][2], 3),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Captions: {summary['captions_path']}")
        print(f"Cues: {summary['cue_count']} from {summary['measured_words']} measured words")
        print(
            f"Span: {summary['first_cue_start_seconds']:.3f} s -> "
            f"{summary['last_cue_end_seconds']:.3f} s of {summary['audio_seconds']:.3f} s"
        )
    return 0


def _parse_target_format(value: str) -> TargetFormat:
    canvas, _, fit = value.partition(":")
    width, _, height = canvas.lower().partition("x")
    try:
        return TargetFormat(int(width), int(height), fit or "contain")
    except (ValueError, ContractError) as exc:
        raise PlanningError(f"invalid --target {value!r}: {exc}") from exc


def _orientation_for(target: TargetFormat) -> str:
    if target.width > target.height:
        return "landscape"
    if target.height > target.width:
        return "portrait"
    return "square"


def _run_plan_scenes(args: argparse.Namespace) -> int:
    try:
        target = _parse_target_format(args.target)
        orientation = _orientation_for(target)

        if args.from_text is not None:
            text = Path(args.from_text).expanduser().read_text(encoding="utf-8")
            kwargs = {}
            if args.total_duration is not None:
                kwargs["total_duration_seconds"] = args.total_duration
            script = NarrativeScript.from_text(
                Path(args.from_text).stem, text, **kwargs
            )
        else:
            script = NarrativeScript.from_dict(
                json.loads(Path(args.script).expanduser().read_text(encoding="utf-8"))
            )
            if args.total_duration is not None:
                raise PlanningError("--total-duration only applies with --from-text")

        policy = RhythmPolicy()
        if args.policy is not None:
            policy = RhythmPolicy.from_dict(
                json.loads(Path(args.policy).expanduser().read_text(encoding="utf-8"))
            )

        semantic = bool(getattr(args, "semantic", False))
        visual_relevance = bool(getattr(args, "visual_relevance", False))
        hook_seconds = getattr(args, "hook_seconds", None)
        text_events_path = getattr(args, "text_events", None)
        direction_path = getattr(args, "visual_direction", None)
        typography_path = getattr(args, "motion_typography", None)
        typography_policy_path = getattr(args, "typography_policy", None)
        narration_captions_path = getattr(args, "narration_captions", None)
        if not semantic and (hook_seconds is not None or text_events_path is not None):
            raise PlanningError("--hook-seconds and --text-events require --semantic")
        if not semantic and typography_path is not None:
            raise PlanningError("--motion-typography requires --semantic")
        if typography_path is None and (
            typography_policy_path is not None or narration_captions_path is not None
        ):
            raise PlanningError(
                "--typography-policy and --narration-captions require "
                "--motion-typography"
            )
        editorial_translation = bool(getattr(args, "editorial_translation", False))
        if visual_relevance and not semantic:
            raise PlanningError("--visual-relevance requires --semantic")
        if editorial_translation and not visual_relevance:
            raise PlanningError(
                "--editorial-translation requires --visual-relevance"
            )
        if direction_path is not None and not semantic:
            raise PlanningError("--visual-direction requires --semantic")
        hook_policy = HookPolicy(hook_seconds=hook_seconds) if hook_seconds else None
        direction_policy = None
        if direction_path is not None:
            direction_policy = VisualDirectionPolicy.from_dict(
                json.loads(
                    Path(direction_path).expanduser().read_text(encoding="utf-8")
                )
            )

        typography_policy = DEFAULT_TYPOGRAPHY_POLICY
        typography_style: dict[str, str] = {}
        if typography_policy_path is not None:
            values = json.loads(
                Path(typography_policy_path).expanduser().read_text(encoding="utf-8")
            )
            if not isinstance(values, dict):
                raise PlanningError("--typography-policy must be a JSON object")
            # Same convention the overrides file uses: a leading "_" key is a
            # note to the next reader, not an instruction.
            values = {k: v for k, v in values.items() if not k.startswith("_")}
            # The fonts and the muted colour travel in the same file as the
            # rhythm: one document per channel decision, not three.
            raw_style = values.pop("style", {})
            if not isinstance(raw_style, dict):
                raise PlanningError("typography policy style must be an object")
            unknown = set(raw_style) - {"display_font", "support_font", "muted"}
            if unknown:
                raise PlanningError(
                    f"typography style does not accept: {', '.join(sorted(unknown))}"
                )
            typography_style = {
                key: value for key, value in raw_style.items() if value is not None
            }
            typography_policy = MotionTypographyPolicy.from_dict(values)

        caption_cues: tuple[tuple[str, float, float], ...] | None = None
        if narration_captions_path is not None:
            path = Path(narration_captions_path).expanduser()
            suffix = path.suffix.lower()
            if suffix not in (".srt", ".vtt"):
                raise PlanningError("--narration-captions must be a .srt or .vtt file")
            caption_cues = parse_subtitle_cues(
                path.read_text(encoding="utf-8"),
                source_format="srt" if suffix == ".srt" else "vtt",
            )

        scene_plan = plan_scenes(script, policy=policy, seed=args.seed)
        shot_plan, assets = plan_shots(
            scene_plan,
            policy=policy,
            seed=args.seed,
            orientation=orientation,
            semantic=semantic,
            visual_relevance=visual_relevance,
            editorial_translation=editorial_translation,
            hook_policy=hook_policy,
        )

        if args.overrides is not None:
            overrides = json.loads(
                Path(args.overrides).expanduser().read_text(encoding="utf-8")
            )
            if isinstance(overrides, dict):
                # An overrides file is an editorial document a person keeps by
                # hand, so a leading "_" key is a note to the next reader
                # rather than an instruction; the contract itself stays strict.
                overrides = {
                    key: value
                    for key, value in overrides.items()
                    if not key.startswith("_")
                }
            scene_plan, shot_plan, assets = apply_overrides(
                scene_plan, shot_plan, assets, overrides, script=script
            )

        scene_plan.validate_against(script)
        shot_plan.validate_against(scene_plan)
        assets.validate_against(shot_plan)

        out_dir = Path(args.out_dir).expanduser()
        targets = {
            out_dir / "scene-plan.json": scene_plan.to_json(),
            out_dir / "shot-plan.json": shot_plan.to_json(),
            out_dir / "asset-requirements.json": assets.to_json(),
        }
        events = ()
        if semantic:
            # The opening always biases the *emphasis* layer, whether or not
            # --hook-seconds also tightened the cut: a viewer who has not
            # decided to stay is the one case where a word on screen earns its
            # place at a lower bar.
            events = plan_shot_text_events(
                scene_plan, shot_plan, hook_policy=hook_policy or DEFAULT_HOOK_POLICY
            )
            if text_events_path is not None:
                targets[Path(text_events_path).expanduser()] = (
                    json.dumps(
                        {
                            "schema_version": 1,
                            "script_id": script.script_id,
                            "style": DARK_DOCUMENTARY_V1.to_dict(),
                            "events": [e.to_dict() for e in events],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n"
                )

        typography = ()
        if semantic and typography_path is not None:
            typography = plan_shot_motion_typography(
                scene_plan,
                shot_plan,
                typography_policy=typography_policy,
                caption_cues=caption_cues,
            )
            targets[Path(typography_path).expanduser()] = (
                json.dumps(
                    {
                        "schema_version": 1,
                        "script_id": script.script_id,
                        "policy": typography_policy.to_dict(),
                        "style": typography_style,
                        "events": [event.to_dict() for event in typography],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )

        bindings: dict[str, str] | None = None
        if args.assets is not None:
            bindings = json.loads(
                Path(args.assets).expanduser().read_text(encoding="utf-8")
            )
            if not isinstance(bindings, dict):
                raise PlanningError("--assets must be a JSON object of asset_id -> path")

        direction_plan = None
        if direction_policy is not None:
            # Grade intensity is chosen per asset from how bright the file
            # already is, so the treatment neither crushes a black photograph
            # nor leaves a bright one outside the piece. Without bindings — or
            # with --no-luma — every shot falls back to the policy default.
            luma_by_asset: dict[str, float] = {}
            if bindings and not getattr(args, "no_luma", False):
                from video_generator.adapters.ffmpeg import measure_luma

                for asset_id, path in sorted(bindings.items()):
                    value = measure_luma(path)
                    if value is not None:
                        luma_by_asset[asset_id] = value
            direction_plan = plan_shot_visual_direction(
                shot_plan,
                policy=direction_policy,
                events=events,
                luma_by_asset=luma_by_asset,
            )
            targets[out_dir / "visual-direction.json"] = direction_plan.to_json()

        edit_plan = None
        if args.emit_edit_plan is not None or args.assets is not None:
            if args.emit_edit_plan is None or args.assets is None:
                raise PlanningError("--emit-edit-plan and --assets must be given together")
            extra: tuple[EditOperation, ...] = ()
            if direction_policy is not None:
                extra += (visual_direction_operation(direction_policy),)
            if typography:
                # The two text layers are alternatives, never a stack: burning
                # an emphasis line under a composed typographic frame is the
                # caption look this layer exists to replace.
                extra += (
                    motion_typography_operation(
                        typography,
                        visual_style=DARK_DOCUMENTARY_V1,
                        display_font=typography_style.get("display_font"),
                        support_font=typography_style.get("support_font"),
                        muted=typography_style.get("muted"),
                    ),
                )
            elif events:
                extra += (
                    text_events_operation(events, visual_style=DARK_DOCUMENTARY_V1),
                )
            edit_plan = shot_plan_to_edit_plan(
                shot_plan,
                bindings,
                plan_id=f"{script.script_id}-edit-plan",
                brief_id=f"{script.script_id}-brief",
                output_path=str((out_dir / "video.mp4").resolve()),
                target_format=target,
                extra_operations=extra,
                directions=direction_plan.by_shot() if direction_plan else None,
            )
            targets[Path(args.emit_edit_plan).expanduser()] = edit_plan.to_json()

        if not args.force:
            existing = sorted(str(p) for p in targets if p.exists())
            if existing:
                raise PlanningError(
                    "refusing to overwrite existing output (use --force): "
                    + ", ".join(existing)
                )

        out_dir.mkdir(parents=True, exist_ok=True)
        for path, payload in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
    except (PlanningError, ContractError, DirectionError, TypographyError) as exc:
        print(f"Planning error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Planning error: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Planning error: could not read input: {exc}", file=sys.stderr)
        return 2

    summary = {
        "script_id": script.script_id,
        "scenes": len(scene_plan.scenes),
        "shots": len(shot_plan.shots),
        "distinct_assets": len(assets.requirements),
        "reuses": sum(1 for s in shot_plan.shots if s.reuse_of is not None),
        "orientation": orientation,
        "out_dir": str(out_dir),
        "edit_plan": str(Path(args.emit_edit_plan)) if edit_plan is not None else None,
    }
    if events and not typography:
        summary["text_events"] = len(events)
    if typography:
        summary["motion_typography"] = {
            "events": len(typography),
            "roles": sorted({event.role for event in typography}),
            "layouts": sorted({event.layout for event in typography}),
            "motions": sorted({event.motion for event in typography}),
            "blocks": sum(len(event.blocks) for event in typography),
            "timed_against_narration": caption_cues is not None,
        }
    if direction_plan is not None:
        compositions: dict[str, int] = {}
        motions: dict[str, int] = {}
        grades: dict[str, int] = {}
        motifs: dict[str, int] = {}
        for item in direction_plan.directions:
            compositions[item.composition] = compositions.get(item.composition, 0) + 1
            motions[item.motion] = motions.get(item.motion, 0) + 1
            grades[item.grade] = grades.get(item.grade, 0) + 1
            if item.visual_motif:
                motifs[item.visual_motif] = motifs.get(item.visual_motif, 0) + 1
        summary["visual_direction"] = {
            "style_id": direction_policy.style_id,
            "compositions": dict(sorted(compositions.items())),
            "motions": dict(sorted(motions.items())),
            "grades": dict(sorted(grades.items())),
            "motifs": dict(sorted(motifs.items())),
        }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", end="")
    else:
        print(
            "plan-scenes\n"
            f"  script: {summary['script_id']}\n"
            f"  scenes: {summary['scenes']}\n"
            f"  shots: {summary['shots']}\n"
            f"  distinct assets: {summary['distinct_assets']} ({summary['reuses']} reuses)\n"
            f"  orientation: {orientation}\n"
            + (
                f"  visual direction: {summary['visual_direction']['compositions']}\n"
                f"                    {summary['visual_direction']['motions']}\n"
                if direction_plan is not None
                else ""
            )
            + f"  written to: {out_dir}\n"
        )
    return 0


def _run_resolve_assets(args: argparse.Namespace) -> int:
    from video_generator.adapters.asset_providers import ProviderError, build_providers
    from video_generator.domain.assets import (
        AssetResolutionError,
        AssetScoringPolicy,
    )
    from video_generator.domain.planning import AssetRequirements, ShotPlan
    from video_generator.resolve import ResolveError, resolve_assets

    try:
        shot_plan = ShotPlan.from_dict(
            json.loads(Path(args.shot_plan).expanduser().read_text(encoding="utf-8"))
        )
        requirements = AssetRequirements.from_dict(
            json.loads(
                Path(args.asset_requirements).expanduser().read_text(encoding="utf-8")
            )
        )
        requirements.validate_against(shot_plan)

        scoring_policy = AssetScoringPolicy()
        if args.scoring_policy is not None:
            scoring_policy = AssetScoringPolicy.from_dict(
                json.loads(
                    Path(args.scoring_policy).expanduser().read_text(encoding="utf-8")
                )
            )

        provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
        providers = build_providers(
            provider_names, library_root=Path(args.library).expanduser()
        )
        if not providers:
            raise ResolveError("no usable providers (is --library correct?)")

        shot_context = {
            shot.shot_id: {
                "visual_query": shot.visual_query,
                "purpose": shot.purpose,
                "visual_intent": shot.purpose,
            }
            for shot in shot_plan.shots
        }

        probe = None
        luma = None
        if not args.no_probe:
            from video_generator.adapters.asset_providers import _default_probe
            from video_generator.adapters.ffmpeg import measure_luma

            probe = _default_probe()
            # Only consulted when the scoring policy states a ceiling, so a
            # project that has not opted in pays nothing for it.
            luma = measure_luma

        out_dir = Path(args.out_dir).expanduser()
        bindings_out = (
            Path(args.bindings_out).expanduser()
            if args.bindings_out
            else out_dir / "asset-bindings.json"
        )
        artifacts = {
            out_dir / "asset-resolution-plan.json": None,
            out_dir / "asset-provenance.json": None,
            out_dir / "revised-asset-requirements.json": None,
            bindings_out: None,
        }
        if not args.force:
            existing = sorted(str(p) for p in artifacts if p.exists())
            if existing:
                raise ResolveError(
                    "refusing to overwrite existing output (use --force): "
                    + ", ".join(existing)
                )

        result = resolve_assets(
            shot_plan,
            requirements,
            providers,
            out_dir=out_dir,
            scoring_policy=scoring_policy,
            shot_context=shot_context,
            do_review_reuse=not args.no_reuse_review,
            probe=probe,
            luma=luma,
        )
    except (ResolveError, ProviderError, AssetResolutionError, PlanningError, ContractError) as exc:
        print(f"Resolve error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Resolve error: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Resolve error: could not read input: {exc}", file=sys.stderr)
        return 2

    plan = result.plan
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "asset-resolution-plan.json").write_text(plan.to_json(), encoding="utf-8")
    provenance_payload = (
        json.dumps(
            [r.provenance.to_dict() for r in plan.resolved],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (out_dir / "asset-provenance.json").write_text(provenance_payload, encoding="utf-8")
    (out_dir / "revised-asset-requirements.json").write_text(
        result.revised_requirements.to_json(), encoding="utf-8"
    )
    bindings_payload = (
        json.dumps(plan.to_bindings(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    bindings_out.parent.mkdir(parents=True, exist_ok=True)
    bindings_out.write_text(bindings_payload, encoding="utf-8")

    images = sum(1 for r in plan.resolved if r.requirement.type == "image")
    videos = sum(1 for r in plan.resolved if r.requirement.type == "video")
    by_reason: dict[str, int] = {}
    for item in plan.unresolved:
        by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
    summary = {
        "script_id": plan.script_id,
        "requirements_original": len(requirements.requirements),
        "requirements_after_reuse_review": len(result.revised_requirements.requirements),
        "reuse_splits": result.review.split_count if result.review is not None else 0,
        "resolved": len(plan.resolved),
        "unresolved": len(plan.unresolved),
        "unresolved_by_reason": by_reason,
        "images": images,
        "videos": videos,
        "providers": dict(sorted(result.provider_stats.items())),
        "relevance_rejections": dict(result.rejection_counts),
        "with_full_provenance": len(plan.resolved),
        "out_dir": str(out_dir),
        "bindings": str(bindings_out),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", end="")
    else:
        lines = [
            "resolve-assets",
            f"  script: {summary['script_id']}",
            f"  requirements: {summary['requirements_original']} -> "
            f"{summary['requirements_after_reuse_review']} after reuse review "
            f"({summary['reuse_splits']} split)",
            f"  resolved: {summary['resolved']} ({images} image / {videos} video)",
            f"  unresolved: {summary['unresolved']} {by_reason or ''}".rstrip(),
            f"  providers: {summary['providers'] or '{}'}",
            f"  relevance rejections: {summary['relevance_rejections'] or '{}'}",
            f"  written to: {out_dir}",
        ]
        print("\n".join(lines) + "\n")

    if args.require_complete and plan.unresolved:
        return 3
    return 0
