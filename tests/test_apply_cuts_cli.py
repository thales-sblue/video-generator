"""`apply-cuts`: cut a real video down to its approved KEEP ranges."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters import CutPreviewArtifact, MediaProbe
from video_generator.cli import main


def _probe(path="v.mp4", seconds=288.0):
    return MediaProbe(
        source_path=path,
        file_size_bytes=1234,
        format_name="mov,mp4",
        duration_seconds=seconds,
        bit_rate_bps=None,
        streams=(),
    )


class ApplyCutsCliTests(unittest.TestCase):
    def _run(self, args, *, source_seconds=288.0, final_seconds=237.0, capture_calls=None):
        recorded = {}

        def fake_render(source, output, keeps, **kwargs):
            recorded["source"] = str(source)
            recorded["output"] = str(output)
            recorded["keeps"] = list(keeps)
            Path(output).write_bytes(b"fake mp4")
            return CutPreviewArtifact(
                source_path=str(source),
                output_path=str(output),
                segment_count=len(keeps),
                kept_seconds=final_seconds,
                file_size_bytes=8,
                frame_rate="30000/1001",
            )

        def fake_probe(path, **kwargs):
            p = str(path)
            if p.endswith("_edited_preview.mp4") or p.endswith(".mp4") and "preview" in p:
                return _probe(p, final_seconds)
            return _probe(p, source_seconds)

        out = io.StringIO()
        err = io.StringIO()
        with patch("video_generator.cli.render_cut_preview", side_effect=fake_render), patch(
            "video_generator.cli.probe_media", side_effect=fake_probe
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        if capture_calls is not None:
            capture_calls.update(recorded)
        return code, out.getvalue(), err.getvalue()

    def _video(self, directory):
        video = Path(directory) / "original.mp4"
        video.write_bytes(b"not really a video")
        return video

    def test_applies_an_approved_cuts_file_and_writes_the_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            video = self._video(directory)
            cuts = Path(directory) / "approved.json"
            cuts.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cuts": [
                            {"start": "00:22.640", "end": "00:31.400"},
                            {"start": "02:37.840", "end": "03:20.000"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = Path(directory) / "out"
            calls = {}
            code, output, err = self._run(
                [
                    "apply-cuts", str(video),
                    "--cuts", str(cuts),
                    "--out-dir", str(out_dir),
                    "--json",
                ],
                capture_calls=calls,
            )
            self.assertEqual(code, 0, err)
            summary = json.loads(output)
            self.assertTrue(summary["preview_path"].endswith("original_edited_preview.mp4"))
            self.assertEqual(summary["cuts_applied"], 2)
            self.assertEqual(summary["segments_kept"], len(calls["keeps"]))
            plan = json.loads((out_dir / "edit-preview.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["schema_version"], 1)
            self.assertEqual(len(plan["cuts"]), 2)
            self.assertEqual(len(plan["keep"]), 3)  # head, middle, tail
            # default 50 ms margin applied
            self.assertAlmostEqual(plan["padding_seconds"]["before"], 0.05)
            self.assertAlmostEqual(plan["cuts"][0]["start"] and 1, 1)

    def test_reads_cut_ranges_from_a_cut_review_json_ignoring_review(self):
        with tempfile.TemporaryDirectory() as directory:
            video = self._video(directory)
            review = Path(directory) / "cut-review.json"
            review.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provenance": "agent",
                        "source_path": str(video),
                        "language": "pt",
                        "duration": "04:48",
                        "segments": [
                            {"start": "00:10.000", "end": "00:20.000", "text": "a",
                             "suggestion": "CUT", "category": "TANGENT", "reason": "x", "confidence": 0.8},
                            {"start": "00:25.000", "end": "00:30.000", "text": "b",
                             "suggestion": "REVIEW", "category": "FILLER", "reason": "y", "confidence": 0.5},
                            {"start": "01:00.000", "end": "01:02.700", "text": "[silêncio 2.7 s]",
                             "suggestion": "CUT", "category": "LONG_PAUSE", "reason": "z", "confidence": 0.6},
                        ],
                        "blocks": [],
                        "summary": {
                            "original_duration": "04:48", "estimated_duration_after_cuts": "04:30",
                            "removed_seconds_estimate": 18.0, "top_removable": [], "rhythm_note": "n",
                        },
                    }
                ),
                encoding="utf-8",
            )
            calls = {}
            code, output, err = self._run(
                ["apply-cuts", str(video), "--cuts", str(review),
                 "--out-dir", str(Path(directory) / "o"), "--json",
                 "--pad-before-ms", "0", "--pad-after-ms", "0"],
                capture_calls=calls,
            )
            self.assertEqual(code, 0, err)
            summary = json.loads(output)
            self.assertEqual(summary["cuts_applied"], 2)  # the two CUT rows, not the REVIEW
            # keeps: 0-10, 20-60, 62.7-288  -> 3 segments
            self.assertEqual(len(calls["keeps"]), 3)

    def test_empty_cut_list_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            video = self._video(directory)
            cuts = Path(directory) / "empty.json"
            cuts.write_text(json.dumps({"schema_version": 1, "cuts": []}), encoding="utf-8")
            code, _out, err = self._run(
                ["apply-cuts", str(video), "--cuts", str(cuts), "--out-dir", str(Path(directory) / "o")]
            )
            self.assertEqual(code, 2)
            self.assertIn("no approved CUT ranges", err)

    def test_refuses_to_overwrite_an_existing_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            video = self._video(directory)
            cuts = Path(directory) / "c.json"
            cuts.write_text(
                json.dumps({"schema_version": 1, "cuts": [{"start": "00:10", "end": "00:20"}]}),
                encoding="utf-8",
            )
            out_dir = Path(directory) / "o"
            out_dir.mkdir()
            (out_dir / "original_edited_preview.mp4").write_bytes(b"x")
            code, _out, err = self._run(
                ["apply-cuts", str(video), "--cuts", str(cuts), "--out-dir", str(out_dir)]
            )
            self.assertEqual(code, 2)
            self.assertIn("already exists", err)

    def test_rejects_a_missing_video_and_a_bad_out_name(self):
        with tempfile.TemporaryDirectory() as directory:
            code, _out, err = self._run(
                ["apply-cuts", "nope.mp4", "--cuts", "x.json", "--out-dir", directory]
            )
            self.assertEqual(code, 2)
            self.assertIn("video does not exist", err)

            video = self._video(directory)
            cuts = Path(directory) / "c.json"
            cuts.write_text(
                json.dumps({"schema_version": 1, "cuts": [{"start": "00:10", "end": "00:20"}]}),
                encoding="utf-8",
            )
            code, _out, err = self._run(
                ["apply-cuts", str(video), "--cuts", str(cuts),
                 "--out-dir", str(Path(directory) / "o"), "--out-name", "sub/dir/x.mp4"]
            )
            self.assertEqual(code, 2)
            self.assertIn("bare .mp4 file name", err)


if __name__ == "__main__":
    unittest.main()
