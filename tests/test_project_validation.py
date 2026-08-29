import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from video_generator.cli import main
from video_generator.domain import (
    EditOperation,
    EditPlan,
    RenderManifest,
    ToolRecord,
    VideoBrief,
    VideoRequest,
)
from video_generator.manifests import fingerprint_file, fingerprint_plan
from video_generator.validation import validate_project_chain


def project_state(directory: str, *, technical_valid: bool = True):
    root = Path(directory)
    source = root / "source.mp4"
    output = root / "segment.mp4"
    source.write_bytes(b"immutable source")
    output.write_bytes(b"rendered segment")
    request = VideoRequest(
        "request-1",
        "Create a concise teaser",
        (str(source),),
        platform="instagram",
        workflow="music-teaser",
    )
    brief = VideoBrief(
        "brief-1",
        request.request_id,
        "Highlight the opening beat",
        "instagram",
        "music-teaser",
    )
    plan = EditPlan(
        "plan-1",
        brief.brief_id,
        (str(source),),
        str(output),
        (EditOperation("extract-1", "extract_segment", str(source), 0, 1),),
    )
    manifest = RenderManifest(
        manifest_id="manifest-plan-1",
        plan_id=plan.plan_id,
        brief_id=plan.brief_id,
        workflow="segment-extract",
        plan_sha256=fingerprint_plan(plan),
        sources=(fingerprint_file(source),),
        outputs=(fingerprint_file(output),),
        tools=(
            ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg version 7.1"),
            ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe version 7.1"),
        ),
        technical_validation_valid=technical_valid,
        technical_validation_issues=() if technical_valid else ("duration_mismatch",),
    )
    return request, brief, plan, manifest, source, output


class ProjectValidationTests(unittest.TestCase):
    def test_accepts_a_complete_unchanged_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            request, brief, plan, manifest, _, _ = project_state(directory)
            report = validate_project_chain(request, brief, plan, manifest)

        self.assertTrue(report.trace_valid)
        self.assertTrue(report.manifest_validation.integrity_valid)
        self.assertTrue(report.technically_ready)
        self.assertEqual(report.editorial_review, "not_performed")
        self.assertTrue(json.loads(report.to_json())["technically_ready"])

    def test_detects_broken_links_and_undeclared_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            request, brief, plan, manifest, _, _ = project_state(directory)
            other = Path(directory) / "other.mp4"
            changed_request = VideoRequest(
                "other-request",
                request.intent,
                (str(other),),
                platform="youtube",
                workflow="short",
            )
            changed_brief = VideoBrief(
                "other-brief",
                request.request_id,
                brief.objective,
                brief.platform,
                brief.workflow,
            )

            report = validate_project_chain(changed_request, changed_brief, plan, manifest)

        self.assertFalse(report.trace_valid)
        self.assertFalse(report.technically_ready)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {
                "request_id_mismatch",
                "brief_id_mismatch",
                "manifest_brief_id_mismatch",
                "platform_mismatch",
                "editorial_workflow_mismatch",
                "undeclared_plan_source",
            },
        )

    def test_keeps_trace_integrity_and_recorded_technical_status_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            request, brief, plan, manifest, _, output = project_state(
                directory,
                technical_valid=False,
            )
            technical_report = validate_project_chain(request, brief, plan, manifest)
            output.write_bytes(b"tampered output")
            tampered_report = validate_project_chain(request, brief, plan, manifest)

        self.assertTrue(technical_report.trace_valid)
        self.assertTrue(technical_report.manifest_validation.integrity_valid)
        self.assertFalse(technical_report.manifest_validation.technical_validation_valid)
        self.assertFalse(technical_report.technically_ready)
        self.assertTrue(tampered_report.trace_valid)
        self.assertFalse(tampered_report.manifest_validation.integrity_valid)


class ProjectValidationCliTests(unittest.TestCase):
    def _persist(self, root: Path, request, brief, plan, manifest):
        paths = {
            "request": root / "video-request.json",
            "brief": root / "video-brief.json",
            "plan": root / "edit-plan.json",
            "manifest": root / "render-manifest.json",
        }
        paths["request"].write_text(request.to_json(), encoding="utf-8")
        paths["brief"].write_text(brief.to_json(), encoding="utf-8")
        paths["plan"].write_text(plan.to_json(), encoding="utf-8")
        paths["manifest"].write_text(manifest.to_json(), encoding="utf-8")
        return paths

    def test_loads_the_persisted_chain_and_returns_semantic_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request, brief, plan, manifest, _, _ = project_state(directory)
            paths = self._persist(root, request, brief, plan, manifest)
            argv = [
                "validate-project",
                "--request", str(paths["request"]),
                "--brief", str(paths["brief"]),
                "--plan", str(paths["plan"]),
                "--manifest", str(paths["manifest"]),
                "--json",
            ]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                valid_exit = main(argv)

            mismatched_brief = VideoBrief(
                brief.brief_id,
                "other-request",
                brief.objective,
                brief.platform,
                brief.workflow,
            )
            paths["brief"].write_text(mismatched_brief.to_json(), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                invalid_exit = main(argv)

        self.assertEqual(valid_exit, 0)
        self.assertTrue(json.loads(stdout.getvalue())["trace_valid"])
        self.assertEqual(invalid_exit, 1)

    def test_returns_two_for_an_invalid_persisted_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request, brief, plan, manifest, _, _ = project_state(directory)
            paths = self._persist(root, request, brief, plan, manifest)
            paths["request"].write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "validate-project",
                        "--request", str(paths["request"]),
                        "--brief", str(paths["brief"]),
                        "--plan", str(paths["plan"]),
                        "--manifest", str(paths["manifest"]),
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("Project validation error", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
