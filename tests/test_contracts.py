import json
import unittest

from video_generator.domain import (
    SHORTS_PORTRAIT,
    YOUTUBE_LANDSCAPE,
    ContractError,
    EditOperation,
    EditPlan,
    FileFingerprint,
    RenderManifest,
    TargetFormat,
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

    def test_target_format_is_omitted_when_absent_and_kept_when_present(self):
        legacy = VideoBrief(
            brief_id="brief-1",
            request_id="request-1",
            objective="Apresentar o refrão",
            platform="instagram",
            workflow="dark-video",
            aspect_ratio="9:16",
        )
        self.assertNotIn("target_format", legacy.to_dict())

        structured = VideoBrief(
            brief_id="brief-1",
            request_id="request-1",
            objective="Apresentar o refrão",
            platform="youtube",
            workflow="dark-video",
            aspect_ratio="16:9",
            target_format=TargetFormat(1920, 1080),
        )
        self.assertEqual(
            structured.to_dict()["target_format"],
            {"width": 1920, "height": 1080, "fit": "contain"},
        )
        restored = VideoBrief.from_dict(json.loads(structured.to_json()))
        self.assertEqual(restored, structured)
        self.assertEqual(restored.aspect_ratio, "16:9")
        with self.assertRaisesRegex(ContractError, "target_format"):
            VideoBrief(
                brief_id="brief-1",
                request_id="request-1",
                objective="x",
                platform="youtube",
                workflow="dark-video",
                target_format={"width": 1920, "height": 1080},
            )

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


class TargetFormatTests(unittest.TestCase):
    def test_valid_creation_and_reduced_aspect_ratio(self):
        landscape = TargetFormat(1920, 1080)
        self.assertEqual(landscape.fit, "contain")
        self.assertEqual(landscape.aspect_ratio, "16:9")
        self.assertEqual(TargetFormat(1080, 1920).aspect_ratio, "9:16")
        self.assertEqual(TargetFormat(1000, 1000).aspect_ratio, "1:1")
        self.assertEqual(TargetFormat(1280, 720, "cover").fit, "cover")

    def test_presets(self):
        self.assertEqual(YOUTUBE_LANDSCAPE, TargetFormat(1920, 1080, "contain"))
        self.assertEqual(SHORTS_PORTRAIT, TargetFormat(1080, 1920, "contain"))

    def test_rejects_odd_zero_negative_oversized_and_bool_dimensions(self):
        with self.assertRaisesRegex(ContractError, "even"):
            TargetFormat(1921, 1080)
        with self.assertRaisesRegex(ContractError, "even"):
            TargetFormat(1920, 1081)
        with self.assertRaisesRegex(ContractError, "greater than zero"):
            TargetFormat(0, 1080)
        with self.assertRaisesRegex(ContractError, "greater than zero"):
            TargetFormat(1920, -1080)
        with self.assertRaisesRegex(ContractError, "7680"):
            TargetFormat(7682, 1080)
        with self.assertRaisesRegex(ContractError, "integer"):
            TargetFormat(True, 1080)
        with self.assertRaisesRegex(ContractError, "integer"):
            TargetFormat(1920, 1080.0)

    def test_rejects_unknown_fit(self):
        with self.assertRaisesRegex(ContractError, "fit"):
            TargetFormat(1920, 1080, "stretch")

    def test_to_dict_and_from_dict_round_trip_and_fail_closed(self):
        fmt = TargetFormat(1080, 1920, "cover")
        self.assertEqual(fmt.to_dict(), {"width": 1080, "height": 1920, "fit": "cover"})
        self.assertEqual(TargetFormat.from_dict(fmt.to_dict()), fmt)
        self.assertEqual(
            TargetFormat.from_dict({"width": 1920, "height": 1080}),
            TargetFormat(1920, 1080, "contain"),
        )
        with self.assertRaises(ContractError):
            TargetFormat.from_dict({"width": 1920})
        with self.assertRaises(ContractError):
            TargetFormat.from_dict({"width": 1920, "height": 1080, "extra": 1})


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

    def test_legacy_plan_serialisation_and_fingerprint_are_unchanged(self):
        plan = EditPlan(
            plan_id="plan-1",
            brief_id="brief-1",
            sources=("inputs/source.mp4",),
            output_path="output/teaser.mp4",
            operations=(self.make_operation(),),
        )
        self.assertNotIn("target_format", plan.to_dict())
        expected_json = (
            '{\n'
            '  "brief_id": "brief-1",\n'
            '  "operations": [\n'
            '    {\n'
            '      "end_seconds": 12.0,\n'
            '      "kind": "trim",\n'
            '      "operation_id": "op-1",\n'
            '      "parameters": {\n'
            '        "preserve_audio": true\n'
            '      },\n'
            '      "source": "inputs/source.mp4",\n'
            '      "start_seconds": 2.0\n'
            '    }\n'
            '  ],\n'
            '  "output_path": "output/teaser.mp4",\n'
            '  "plan_id": "plan-1",\n'
            '  "schema_version": 1,\n'
            '  "sources": [\n'
            '    "inputs/source.mp4"\n'
            '  ]\n'
            '}\n'
        )
        self.assertEqual(plan.to_json(), expected_json)
        import hashlib

        # Frozen baseline: a legacy plan without target_format keeps the exact
        # compact serialisation and plan_sha256 it had before the field existed.
        self.assertEqual(
            hashlib.sha256(plan.to_json(indent=None).encode("utf-8")).hexdigest(),
            "95e7ef3316579eadd95459ce5ddc395d1f347372effbbeda7c2f4f6d155528b1",
        )

    def test_target_format_is_optional_and_round_trips_when_present(self):
        operation = self.make_operation()
        plan = EditPlan(
            plan_id="plan-1",
            brief_id="brief-1",
            sources=("inputs/source.mp4",),
            output_path="output/teaser.mp4",
            operations=(operation,),
            target_format=TargetFormat(1080, 1920, "cover"),
        )
        self.assertEqual(
            plan.to_dict()["target_format"],
            {"width": 1080, "height": 1920, "fit": "cover"},
        )
        restored = EditPlan.from_dict(json.loads(plan.to_json()))
        self.assertEqual(restored, plan)
        self.assertEqual(restored.target_format, TargetFormat(1080, 1920, "cover"))

    def test_constructor_rejects_a_mapping_target_format(self):
        with self.assertRaisesRegex(ContractError, "target_format"):
            EditPlan(
                plan_id="plan-1",
                brief_id="brief-1",
                sources=("inputs/source.mp4",),
                output_path="output/teaser.mp4",
                operations=(self.make_operation(),),
                target_format={"width": 1080, "height": 1920},
            )

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
