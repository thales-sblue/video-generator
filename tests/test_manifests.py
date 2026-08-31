import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from video_generator.adapters import SegmentArtifact, SequenceArtifact
from video_generator.doctor import DoctorReport, ToolStatus
from video_generator.domain import EditOperation, EditPlan, RenderManifest
from video_generator.manifests import (
    ManifestError,
    build_sequence_render_manifest,
    build_segment_render_manifest,
    default_manifest_path,
    fingerprint_file,
    publish_render_manifest,
    validate_manifest_target,
)
from video_generator.validation import (
    PreflightReport,
    SegmentValidationIssue,
    SegmentValidationReport,
    SequenceValidationIssue,
    SequenceValidationReport,
)
from video_generator.workflows import SegmentWorkflowReport, SequenceWorkflowReport


def doctor_report(*, local_only: bool = True) -> DoctorReport:
    return DoctorReport(
        operating_system="Windows",
        platform_release="11",
        machine="AMD64",
        local_only=local_only,
        external_services_allowed=False,
        preserve_sources=True,
        config_path="config/default.toml",
        tools=(
            ToolStatus("FFmpeg", True, "ffmpeg version 7.1", "C:/tools/ffmpeg.exe"),
            ToolStatus("ffprobe", True, "ffprobe version 7.1", "C:/tools/ffprobe.exe"),
        ),
    )


def execution(directory: str, *, valid: bool = True):
    root = Path(directory)
    source = root / "source.mp4"
    output = root / "segment.mp4"
    source.write_bytes(b"immutable source")
    output.write_bytes(b"rendered segment")
    plan = EditPlan(
        "plan-1",
        "brief-1",
        (str(source),),
        str(output),
        (EditOperation("extract-1", "extract_segment", str(source), 0, 1),),
    )
    artifact = SegmentArtifact(str(source.resolve()), str(output.resolve()), 0, 1, output.stat().st_size)
    validation = SegmentValidationReport(
        valid,
        artifact,
        1,
        1 if valid else 1.5,
        0.1,
        () if valid else (SegmentValidationIssue("duration_mismatch", "duration differs"),),
        None,
    )
    report = SegmentWorkflowReport(plan.plan_id, "extract-1", PreflightReport(plan.plan_id, True, (), ()), artifact, validation)
    return plan, report


class ManifestTests(unittest.TestCase):
    def test_rejects_a_final_manifest_for_an_invalid_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            output = root / "final.mp4"
            for path in (first, second, output):
                path.write_bytes(path.stem.encode("utf-8"))
            plan = EditPlan(
                "plan-final",
                "brief-dark",
                (str(first), str(second)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                ),
            )
            artifact = SequenceArtifact(
                (str(first.resolve()), str(second.resolve())),
                str(output.resolve()),
                2,
                output.stat().st_size,
            )
            validation = SequenceValidationReport(
                False,
                artifact,
                3,
                0.15,
                (SequenceValidationIssue("duration_mismatch", "duration differs"),),
                None,
            )
            report = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "clip-2"),
                PreflightReport(plan.plan_id, True, (), ()),
                artifact,
                validation,
                "final",
            )

            with self.assertRaisesRegex(ManifestError, "invalid sequence"):
                build_sequence_render_manifest(
                    plan,
                    report,
                    doctor_report(),
                    tuple(fingerprint_file(source) for source in plan.sources),
                )

    def test_sequence_manifest_accounts_for_image_clip_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "clip.mp4"
            still = root / "card.png"
            output = root / "timeline.mp4"
            for path in (clip, still, output):
                path.write_bytes(path.stem.encode("utf-8"))
            plan = EditPlan(
                "plan-image",
                "brief-dark",
                (str(clip), str(still)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(clip), 0, 1.5),
                    EditOperation(
                        "image-1", "image_clip", str(still), parameters={"duration_seconds": 2}
                    ),
                ),
            )
            artifact = SequenceArtifact(
                (str(clip.resolve()), str(still.resolve())),
                str(output.resolve()),
                3.5,
                output.stat().st_size,
                image_count=1,
            )
            validation = SequenceValidationReport(True, artifact, 3.5, 0.15, (), None)
            report = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "image-1"),
                PreflightReport(plan.plan_id, True, (), ()),
                artifact,
                validation,
            )
            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)

            manifest = build_sequence_render_manifest(plan, report, doctor_report(), fingerprints)
            self.assertEqual(manifest.workflow, "video-sequence")

            mismatched = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "image-1"),
                report.preflight,
                SequenceArtifact(
                    artifact.source_paths, artifact.output_path, 3.5, artifact.file_size_bytes
                ),
                SequenceValidationReport(
                    True,
                    SequenceArtifact(
                        artifact.source_paths, artifact.output_path, 3.5, artifact.file_size_bytes
                    ),
                    3.5,
                    0.15,
                    (),
                    None,
                ),
            )
            with self.assertRaisesRegex(ManifestError, "images do not match the plan"):
                build_sequence_render_manifest(
                    plan, mismatched, doctor_report(), fingerprints
                )

    def test_sequence_manifest_records_a_text_narration_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.mp4"
            second = root / "b.mp4"
            output = root / "timeline.mp4"
            for path in (first, second, output):
                path.write_bytes(path.stem.encode("utf-8"))
            plan = EditPlan(
                "plan-tts",
                "brief-dark",
                (str(first), str(second)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                    EditOperation(
                        "voice-1",
                        "narration",
                        parameters={"duration_policy": "match_timeline", "text": "Dark script."},
                    ),
                ),
            )
            digest = hashlib.sha256(b"Dark script.").hexdigest()
            artifact = SequenceArtifact(
                (str(first.resolve()), str(second.resolve())),
                str(output.resolve()),
                2.0,
                output.stat().st_size,
                narration_text_sha256=digest,
            )
            report = SequenceWorkflowReport(
                plan.plan_id,
                ("clip-1", "clip-2", "voice-1"),
                PreflightReport(plan.plan_id, True, (), ()),
                artifact,
                SequenceValidationReport(True, artifact, 2.0, 0.15, (), None),
            )
            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)

            manifest = build_sequence_render_manifest(plan, report, doctor_report(), fingerprints)
            self.assertEqual(manifest.workflow, "video-sequence")

            wrong_artifact = SequenceArtifact(
                artifact.source_paths,
                artifact.output_path,
                2.0,
                artifact.file_size_bytes,
                narration_text_sha256=hashlib.sha256(b"other").hexdigest(),
            )
            wrong = SequenceWorkflowReport(
                plan.plan_id,
                report.operation_ids,
                report.preflight,
                wrong_artifact,
                SequenceValidationReport(True, wrong_artifact, 2.0, 0.15, (), None),
            )
            with self.assertRaisesRegex(ManifestError, "narration text does not match"):
                build_sequence_render_manifest(plan, wrong, doctor_report(), fingerprints)

    def test_sequence_manifest_checks_persisted_music_fades(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.mp4"
            second = root / "b.mp4"
            music = root / "bed.wav"
            output = root / "timeline.mp4"
            for path in (first, second, music, output):
                path.write_bytes(path.stem.encode("utf-8"))
            plan = EditPlan(
                "plan-fade",
                "brief-dark",
                (str(first), str(second), str(music)),
                str(output),
                (
                    EditOperation("clip-1", "sequence_clip", str(first), 0, 1),
                    EditOperation("clip-2", "sequence_clip", str(second), 0, 1),
                    EditOperation(
                        "music-1",
                        "music",
                        str(music),
                        parameters={
                            "duration_policy": "loop_to_timeline",
                            "gain_db": -12,
                            "fade_out_seconds": 1.5,
                        },
                    ),
                ),
            )

            def report_for(fade_out):
                artifact = SequenceArtifact(
                    (str(first.resolve()), str(second.resolve())),
                    str(output.resolve()),
                    2.0,
                    output.stat().st_size,
                    music_source_path=str(music.resolve()),
                    music_gain_db=-12.0,
                    music_fade_out_seconds=fade_out,
                )
                return SequenceWorkflowReport(
                    plan.plan_id,
                    ("clip-1", "clip-2", "music-1"),
                    PreflightReport(plan.plan_id, True, (), ()),
                    artifact,
                    SequenceValidationReport(True, artifact, 2.0, 0.15, (), None),
                )

            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)
            build_sequence_render_manifest(plan, report_for(1.5), doctor_report(), fingerprints)
            with self.assertRaisesRegex(ManifestError, "music does not match"):
                build_sequence_render_manifest(plan, report_for(0.0), doctor_report(), fingerprints)

    def test_fingerprints_files_and_builds_round_trippable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, report = execution(directory)
            fingerprint = fingerprint_file(plan.sources[0])
            manifest = build_segment_render_manifest(
                plan,
                report,
                doctor_report(),
                (fingerprint,),
            )

        self.assertEqual(fingerprint.sha256, hashlib.sha256(b"immutable source").hexdigest())
        self.assertEqual(manifest.sources[0], fingerprint)
        self.assertEqual(manifest.outputs[0].path, report.artifact.output_path)
        self.assertEqual({tool.name for tool in manifest.tools}, {"FFmpeg", "ffprobe"})
        self.assertEqual(RenderManifest.from_dict(json.loads(manifest.to_json())), manifest)

    def test_validates_target_before_execution_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "segment.mp4"
            output.write_bytes(b"output")
            target = default_manifest_path(output)

            self.assertEqual(
                validate_manifest_target(target, forbidden_paths=(output,)),
                target.resolve(),
            )
            reserved = root / "reserved.json"
            with self.assertRaisesRegex(ManifestError, "must not overwrite"):
                validate_manifest_target(reserved, forbidden_paths=(reserved,))
            with self.assertRaisesRegex(ManifestError, r"\.json"):
                validate_manifest_target(root / "manifest.txt", forbidden_paths=(output,))
            target.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "already exists"):
                validate_manifest_target(target, forbidden_paths=(output,))

    def test_publishes_exclusively_and_preserves_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, report = execution(directory)
            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)
            manifest = build_segment_render_manifest(
                plan,
                report,
                doctor_report(),
                fingerprints,
            )
            target = Path(directory) / "state" / "render-manifest.json"

            published = publish_render_manifest(manifest, target)
            existing = target.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "already exists"):
                publish_render_manifest(manifest, target)

            self.assertEqual(published, target.resolve())
            self.assertEqual(target.read_text(encoding="utf-8"), existing)
            self.assertEqual(RenderManifest.from_dict(json.loads(existing)), manifest)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_rejects_unsafe_runtime_or_missing_tool_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, report = execution(directory)
            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)
            with self.assertRaisesRegex(ManifestError, "unsafe runtime"):
                build_segment_render_manifest(
                    plan,
                    report,
                    doctor_report(local_only=False),
                    fingerprints,
                )
            missing = doctor_report()
            missing = DoctorReport(
                missing.operating_system,
                missing.platform_release,
                missing.machine,
                missing.local_only,
                missing.external_services_allowed,
                missing.preserve_sources,
                missing.config_path,
                (ToolStatus("FFmpeg", False),),
            )
            with self.assertRaisesRegex(ManifestError, "FFmpeg"):
                build_segment_render_manifest(plan, report, missing, fingerprints)

    def test_rejects_source_changed_after_execution_started(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, report = execution(directory)
            fingerprints = tuple(fingerprint_file(source) for source in plan.sources)
            Path(plan.sources[0]).write_bytes(b"changed source")

            with self.assertRaisesRegex(ManifestError, "changed during workflow"):
                build_segment_render_manifest(
                    plan,
                    report,
                    doctor_report(),
                    fingerprints,
                )


if __name__ == "__main__":
    unittest.main()
