import json
import unittest

from video_generator.domain import (
    ContractError,
    EditOperation,
    EditPlan,
    FileFingerprint,
    RenderManifest,
    ToolRecord,
    VideoBrief,
    VideoRequest,
)


class VideoRequestTests(unittest.TestCase):
    def test_round_trip_is_json_safe_and_deterministic(self):
        request = VideoRequest(
            request_id="request-1",
            intent="Crie um teaser desta música",
            sources=("inputs/song.wav", "assets/cover.png"),
            platform="instagram",
            workflow="music-teaser",
        )

        restored = VideoRequest.from_dict(json.loads(request.to_json()))

        self.assertEqual(restored, request)
        self.assertEqual(request.to_json(), request.to_json())
        self.assertTrue(request.to_json().endswith("\n"))

    def test_rejects_empty_or_duplicate_sources(self):
        with self.assertRaises(ContractError):
            VideoRequest("request-1", "Make a Short", ())
        with self.assertRaises(ContractError):
            VideoRequest("request-1", "Make a Short", ("clip.mp4", "clip.mp4"))

    def test_rejects_unknown_contract_version_and_fields(self):
        with self.assertRaises(ContractError):
            VideoRequest.from_dict(
                {
                    "schema_version": 2,
                    "request_id": "request-1",
                    "intent": "Make a Short",
                    "sources": ["clip.mp4"],
                }
            )
        with self.assertRaises(ContractError):
            VideoRequest.from_dict(
                {
                    "schema_version": 1,
                    "request_id": "request-1",
                    "intent": "Make a Short",
                    "sources": ["clip.mp4"],
                    "remote_provider": "paid-service",
                }
            )


class VideoBriefTests(unittest.TestCase):
    def test_round_trip_preserves_editorial_direction(self):
        brief = VideoBrief(
            brief_id="brief-1",
            request_id="request-1",
            objective="Apresentar o refrão com clareza",
            platform="instagram",
            workflow="music-teaser",
            audience="ouvintes novos",
            aspect_ratio="9:16",
            target_duration_seconds=20,
            editorial_notes=("Abrir no hook", "Motion seletivo"),
        )

        restored = VideoBrief.from_dict(json.loads(brief.to_json()))

        self.assertEqual(restored, brief)
        self.assertEqual(restored.target_duration_seconds, 20.0)

    def test_rejects_non_positive_duration(self):
        with self.assertRaises(ContractError):
            VideoBrief("brief-1", "request-1", "Teaser", "instagram", "music-teaser", target_duration_seconds=0)
        with self.assertRaises(ContractError):
            VideoBrief(
                "brief-1",
                "request-1",
                "Teaser",
                "instagram",
                "music-teaser",
                target_duration_seconds=float("nan"),
            )


class EditPlanTests(unittest.TestCase):
    def make_operation(self):
        return EditOperation(
            operation_id="op-1",
            kind="trim",
            source="inputs/source.mp4",
            start_seconds=2,
            end_seconds=12,
            parameters={"preserve_audio": True},
        )

    def test_round_trip_and_immutable_parameters(self):
        operation = self.make_operation()
        plan = EditPlan(
            plan_id="plan-1",
            brief_id="brief-1",
            sources=("inputs/source.mp4",),
            output_path="output/teaser.mp4",
            operations=(operation,),
        )

        restored = EditPlan.from_dict(json.loads(plan.to_json()))

        self.assertEqual(restored.to_dict(), plan.to_dict())
        with self.assertRaises(TypeError):
            operation.parameters["preserve_audio"] = False

        nested = EditOperation("op-2", "overlay", parameters={"layers": [{"opacity": 0.5}]})
        with self.assertRaises(TypeError):
            nested.parameters["layers"][0]["opacity"] = 1

    def test_rejects_source_overwrite(self):
        with self.assertRaisesRegex(ContractError, "must not overwrite"):
            EditPlan(
                plan_id="plan-1",
                brief_id="brief-1",
                sources=("inputs/source.mp4",),
                output_path="inputs/source.mp4",
                operations=(self.make_operation(),),
            )

    def test_rejects_undeclared_operation_source(self):
        operation = EditOperation("op-1", "trim", source="inputs/other.mp4")
        with self.assertRaisesRegex(ContractError, "must be declared"):
            EditPlan(
                plan_id="plan-1",
                brief_id="brief-1",
                sources=("inputs/source.mp4",),
                output_path="output/result.mp4",
                operations=(operation,),
            )

    def test_rejects_invalid_time_range_and_non_json_parameters(self):
        with self.assertRaises(ContractError):
            EditOperation("op-1", "trim", start_seconds=5, end_seconds=5)
        with self.assertRaises(ContractError):
            EditOperation("op-1", "trim", start_seconds=float("inf"))
        with self.assertRaises(ContractError):
            EditOperation("op-1", "custom", parameters={"invalid": object()})


class RenderManifestTests(unittest.TestCase):
    def make_manifest(self, **overrides):
        values = {
            "manifest_id": "manifest-plan-1",
            "plan_id": "plan-1",
            "brief_id": "brief-1",
            "workflow": "segment-extract",
            "plan_sha256": "a" * 64,
            "sources": (FileFingerprint("inputs/source.mp4", "b" * 64, 1000),),
            "outputs": (FileFingerprint("output/segment.mp4", "c" * 64, 500),),
            "tools": (
                ToolRecord("FFmpeg", "C:/tools/ffmpeg.exe", "ffmpeg version 7.1"),
                ToolRecord("ffprobe", "C:/tools/ffprobe.exe", "ffprobe version 7.1"),
            ),
            "technical_validation_valid": True,
        }
        values.update(overrides)
        return RenderManifest(**values)

    def test_round_trip_preserves_reproducibility_and_review_boundary(self):
        manifest = self.make_manifest()

        restored = RenderManifest.from_dict(json.loads(manifest.to_json()))

        self.assertEqual(restored, manifest)
        self.assertTrue(restored.local_only)
        self.assertEqual(restored.editorial_review, "not_performed")

    def test_rejects_unsafe_or_internally_inconsistent_state(self):
        with self.assertRaisesRegex(ContractError, "local_only"):
            self.make_manifest(local_only=False)
        with self.assertRaisesRegex(ContractError, "editorial_review"):
            self.make_manifest(editorial_review="approved")
        with self.assertRaisesRegex(ContractError, "must not contain issues"):
            self.make_manifest(technical_validation_issues=("duration_mismatch",))
        with self.assertRaisesRegex(ContractError, "must contain issues"):
            self.make_manifest(technical_validation_valid=False)

    def test_rejects_invalid_hashes_and_source_overwrite(self):
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            FileFingerprint("inputs/source.mp4", "not-a-hash", 100)
        with self.assertRaisesRegex(ContractError, "lowercase"):
            FileFingerprint("inputs/source.mp4", "A" * 64, 100)
        source = FileFingerprint("inputs/source.mp4", "b" * 64, 1000)
        output = FileFingerprint("inputs/source.mp4", "b" * 64, 1000)
        with self.assertRaisesRegex(ContractError, "must not overwrite"):
            self.make_manifest(sources=(source,), outputs=(output,))


if __name__ == "__main__":
    unittest.main()
