"""Tests for the Remotion motion-graphics layer.

Covers the Python -> Remotion contract (domain/motion_graphics.py), the
fail-closed render adapter (adapters/remotion.py), and the sequence-workflow
wiring: the new operation kind, its exclusivity with the libass path and the
burned captions, the alpha overlay reaching the FFmpeg adapter, and the fact
that a plan without the operation behaves exactly as before.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from video_generator.adapters import remotion as remotion_adapter
from video_generator.adapters.ffmpeg import (
    FFmpegError,
    SequenceImage,
    compose_video_sequence,
)
from video_generator.domain import EditOperation, EditPlan, TargetFormat
from video_generator.domain.motion_graphics import (
    MotionGraphicsError,
    build_motion_graphics_scene,
    scene_from_typography_operation,
    scene_to_json,
)
from video_generator.domain.typography import MotionTextEvent, TextBlock
from video_generator.workflows.sequence import (
    SequenceWorkflowError,
    _operations_from_plan,
    motion_graphics_scene_from_plan,
)


def _event(**over) -> MotionTextEvent:
    base = dict(
        event_id="type_001",
        blocks=(TextBlock("INTELIGENTE", "small"), TextBlock("ACREDITA", "massive")),
        role="hook",
        layout="small_plus_massive",
        motion="stagger_rise",
        start_seconds=2.839,
        end_seconds=5.839,
    )
    base.update(over)
    return MotionTextEvent(**base)


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
class ContractTests(unittest.TestCase):
    def test_builds_the_documented_shape(self) -> None:
        scene = build_motion_graphics_scene(
            [_event()], width=1920, height=1080, fps=30, duration_seconds=209.0
        )
        self.assertEqual(scene["schema_version"], 1)
        self.assertEqual(
            scene["composition"],
            {"width": 1920, "height": 1080, "fps": 30.0, "durationInSeconds": 209.0},
        )
        self.assertEqual(set(scene["theme"]), {"foreground", "accent", "muted", "background"})
        self.assertIsNone(scene["theme"]["background"])
        (event,) = scene["events"]
        self.assertEqual(
            set(event),
            {"id", "start", "duration", "role", "layout", "variant", "motion", "blocks"},
        )
        self.assertEqual(event["layout"], "small-plus-massive")
        self.assertEqual(event["duration"], 3.0)
        self.assertEqual(
            [b["importance"] for b in event["blocks"]], ["support", "dominant"]
        )

    def test_every_domain_layout_maps_to_a_remotion_layout(self) -> None:
        pairs = {
            "dominant_word": ("dominant-word", None),
            "small_plus_massive": ("small-plus-massive", None),
            "stacked_hierarchy": ("stacked-editorial", None),
            "edge_aligned": ("stacked-editorial", "edge"),
            "split_statement": ("split-contrast", None),
            "contrast_pair": ("split-contrast", "pair"),
            "centered_poster": ("poster-statement", None),
        }
        for domain_layout, (remotion_layout, variant) in pairs.items():
            blocks = (
                (TextBlock("A", "massive"),)
                if domain_layout == "dominant_word"
                else (TextBlock("A", "small"), TextBlock("B", "massive"))
            )
            ev = _event(layout=domain_layout, blocks=blocks, role="statement")
            scene = build_motion_graphics_scene(
                [ev], width=1920, height=1080, fps=30, duration_seconds=60
            )
            self.assertEqual(scene["events"][0]["layout"], remotion_layout, domain_layout)
            self.assertEqual(scene["events"][0]["variant"], variant, domain_layout)

    def test_weight_becomes_importance(self) -> None:
        ev = _event(
            layout="stacked_hierarchy",
            blocks=(
                TextBlock("A", "micro"),
                TextBlock("B", "large"),
                TextBlock("C", "massive"),
            ),
        )
        scene = build_motion_graphics_scene(
            [ev], width=1920, height=1080, fps=30, duration_seconds=60
        )
        self.assertEqual(
            [b["importance"] for b in scene["events"][0]["blocks"]],
            ["support", "secondary", "dominant"],
        )

    def test_wire_json_is_deterministic_and_sorted(self) -> None:
        scene = build_motion_graphics_scene(
            [_event()], width=1920, height=1080, fps=30, duration_seconds=209.0
        )
        first = scene_to_json(scene)
        second = scene_to_json(json.loads(first))
        self.assertEqual(first, second)
        self.assertIn('"composition":{"durationInSeconds"', first)  # keys sorted

    def test_event_end_is_clamped_to_the_timeline(self) -> None:
        ev = _event(start_seconds=200.0, end_seconds=260.0)
        scene = build_motion_graphics_scene(
            [ev], width=1920, height=1080, fps=30, duration_seconds=209.261
        )
        self.assertEqual(scene["events"][0]["start"], 200.0)
        self.assertEqual(scene["events"][0]["duration"], 9.261)

    def test_theme_reads_the_visual_style_palette(self) -> None:
        scene = build_motion_graphics_scene(
            [_event()],
            width=1920,
            height=1080,
            fps=30,
            duration_seconds=60,
            foreground="#101112",
            accent="#ff8800",
        )
        self.assertEqual(scene["theme"]["foreground"], "#101112")
        self.assertEqual(scene["theme"]["accent"], "#FF8800")

    def test_rejects_a_bad_colour(self) -> None:
        with self.assertRaises(MotionGraphicsError):
            build_motion_graphics_scene(
                [_event()], width=1920, height=1080, fps=30,
                duration_seconds=60, accent="blue",
            )

    def test_rejects_a_zero_length_event_after_clamping(self) -> None:
        ev = _event(start_seconds=209.5, end_seconds=210.0)
        with self.assertRaises(MotionGraphicsError):
            build_motion_graphics_scene(
                [ev], width=1920, height=1080, fps=30, duration_seconds=209.0
            )

    def test_rejects_a_non_positive_canvas(self) -> None:
        for kwargs in ({"width": 0, "height": 1080}, {"width": 1920, "height": -2}):
            with self.assertRaises(MotionGraphicsError):
                build_motion_graphics_scene(
                    [_event()], fps=30, duration_seconds=60, **kwargs
                )

    def test_from_typography_operation_parameters(self) -> None:
        params = {
            "items": [
                {
                    "blocks": [
                        {"text": "QUANDO SE REPETE", "weight": "small", "accent": False},
                        {"text": "FAMILIAR", "weight": "massive", "accent": True},
                    ],
                    "layout": "small_plus_massive",
                    "motion": "stagger_rise",
                    "start_seconds": 1.0,
                    "end_seconds": 4.0,
                }
            ],
            "style": {"foreground": "&H00F5F3EE", "accent": "&H00E5A33C"},
        }
        scene = scene_from_typography_operation(
            params, width=1920, height=1080, fps=30, duration_seconds=60
        )
        self.assertEqual(scene["events"][0]["layout"], "small-plus-massive")
        self.assertTrue(scene["events"][0]["blocks"][1]["accent"])
        self.assertEqual(scene["theme"]["foreground"], "#EEF3F5")
        self.assertEqual(scene["theme"]["accent"], "#3CA3E5")


# --------------------------------------------------------------------------- #
# The render adapter — fail-closed, correct argv, no silent blank
# --------------------------------------------------------------------------- #
class RenderAdapterTests(unittest.TestCase):
    def _scene(self) -> dict:
        return build_motion_graphics_scene(
            [_event()], width=1920, height=1080, fps=30, duration_seconds=12.0
        )

    def test_fails_closed_without_node(self) -> None:
        with self.assertRaises(remotion_adapter.RemotionError):
            remotion_adapter.render_motion_overlay(
                self._scene(),
                Path("does-not-matter.mov"),
                node_bin="",
                project_dir=Path(__file__).resolve().parents[1] / "remotion",
            )

    def test_rejects_a_scene_with_no_events(self) -> None:
        scene = self._scene()
        scene["events"] = []
        with self.assertRaises(remotion_adapter.RemotionError):
            remotion_adapter.render_motion_overlay(scene, Path("x.mov"))

    def test_builds_a_deterministic_prores_command(self) -> None:
        seen: dict[str, object] = {}

        def fake_runner(command, **kwargs):
            seen["command"] = command
            seen["cwd"] = kwargs.get("cwd")
            props = [a for a in command if str(a).startswith("--props=")][0]
            payload = Path(props.split("=", 1)[1]).read_text(encoding="utf-8")
            seen["payload"] = payload
            Path(command[command.index("render") + 3]).write_bytes(b"MOOV")

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()

        project = Path(__file__).resolve().parents[1] / "remotion"
        if not (project / "node_modules" / "@remotion" / "cli" / "remotion-cli.js").is_file():
            self.skipTest("remotion project is not installed")
        out = project / ".pytest-overlay.mov"
        if out.exists():
            out.unlink()
        try:
            artifact = remotion_adapter.render_motion_overlay(
                self._scene(), out, node_bin="/usr/bin/node",
                project_dir=project, runner=fake_runner,
            )
        finally:
            if out.exists():
                out.unlink()
        command = seen["command"]
        self.assertEqual(command[0], "/usr/bin/node")
        self.assertIn("render", command)
        self.assertIn("MotionGraphics", command)
        self.assertIn("--codec=prores", command)
        self.assertIn("--prores-profile=4444", command)
        self.assertEqual(json.loads(seen["payload"]), json.loads(scene_to_json(self._scene())))
        self.assertEqual(artifact.event_count, 1)


# --------------------------------------------------------------------------- #
# The plan operation and its guards
# --------------------------------------------------------------------------- #
def _scene_operation(events=None) -> EditOperation:
    scene = build_motion_graphics_scene(
        events or [_event()], width=1920, height=1080, fps=30, duration_seconds=6.0
    )
    return EditOperation(operation_id="mg", kind="motion_graphics", parameters=scene)


def _image(source: str) -> EditOperation:
    return EditOperation(
        operation_id=source,
        kind="image_clip",
        source=source,
        parameters={"duration_seconds": 3.0, "fit": "cover"},
    )


def _plan(*operations: EditOperation) -> EditPlan:
    return EditPlan(
        plan_id="p",
        brief_id="b",
        sources=tuple(
            op.source for op in operations if op.source is not None
        ),
        output_path="output/x/render.mp4",
        operations=operations,
        target_format=TargetFormat(1920, 1080, "cover"),
    )


class PlanGuardTests(unittest.TestCase):
    def test_scene_round_trips_through_the_plan(self) -> None:
        plan = _plan(_image("a.mp4"), _image("b.mp4"), _scene_operation())
        _operations_from_plan(plan)  # no raise
        scene = motion_graphics_scene_from_plan(plan)
        self.assertEqual(scene["events"][0]["layout"], "small-plus-massive")

    def test_absent_operation_yields_no_scene(self) -> None:
        plan = _plan(_image("a.mp4"), _image("b.mp4"))
        self.assertIsNone(motion_graphics_scene_from_plan(plan))

    def test_mutually_exclusive_with_motion_typography(self) -> None:
        typo = EditOperation(
            operation_id="typo",
            kind="motion_typography",
            parameters={
                "items": [
                    {
                        "blocks": [{"text": "A", "weight": "massive"}],
                        "layout": "dominant_word",
                        "motion": "scale_in",
                        "start_seconds": 1.0,
                        "end_seconds": 3.0,
                    }
                ]
            },
        )
        plan = _plan(_image("a.mp4"), _image("b.mp4"), _scene_operation(), typo)
        with self.assertRaises(SequenceWorkflowError):
            _operations_from_plan(plan)

    def test_mutually_exclusive_with_burned_captions(self) -> None:
        captions = EditOperation(
            operation_id="cap",
            kind="captions",
            parameters={
                "style": "bottom_box",
                "items": [{"text": "hi", "start_seconds": 0.0, "end_seconds": 1.0}],
            },
        )
        plan = _plan(_image("a.mp4"), _image("b.mp4"), _scene_operation(), captions)
        with self.assertRaises(SequenceWorkflowError):
            _operations_from_plan(plan)

    def test_rejects_an_operation_with_no_events(self) -> None:
        op = EditOperation(
            operation_id="mg",
            kind="motion_graphics",
            parameters={
                "schema_version": 1,
                "composition": {
                    "width": 1920, "height": 1080, "fps": 30, "durationInSeconds": 6
                },
                "theme": {"foreground": "#eef3f5", "accent": "#3ca3e5", "muted": "#cec8c4"},
                "events": [],
            },
        )
        plan = _plan(_image("a.mp4"), _image("b.mp4"), op)
        with self.assertRaises(SequenceWorkflowError):
            _operations_from_plan(plan)


# --------------------------------------------------------------------------- #
# FFmpeg adapter — the overlay path, and everything else unchanged
# --------------------------------------------------------------------------- #
class FFmpegOverlayTests(unittest.TestCase):
    def _segments(self):
        return (
            SequenceImage("a.png", 2.0, "cover", None, None, "center", None, None),
            SequenceImage("b.png", 2.0, "cover", None, None, "center", None, None),
        )

    def test_motion_overlay_and_motion_text_are_mutually_exclusive(self) -> None:
        from video_generator.adapters.ffmpeg import MotionTextBlockCue, MotionTextCue

        cue = MotionTextCue(
            (MotionTextBlockCue("A", "massive", False),), 0.0, 1.0,
            "dominant_word", "scale_in",
        )
        with self.assertRaises(FFmpegError):
            compose_video_sequence(
                self._segments(),
                "out.mp4",
                motion_text=(cue,),
                motion_overlay="whatever.mov",
                canvas=(1920, 1080),
            )

    def test_missing_overlay_file_is_rejected(self) -> None:
        with self.assertRaises(FFmpegError):
            compose_video_sequence(
                self._segments(),
                "out.mp4",
                motion_overlay="no-such-overlay.mov",
                canvas=(1920, 1080),
            )

    def test_non_alpha_suffix_is_rejected(self) -> None:
        # this .py file exists, so the is_file() check passes; the suffix does not
        with self.assertRaises(FFmpegError):
            compose_video_sequence(
                self._segments(),
                "out.mp4",
                motion_overlay=__file__,
                canvas=(1920, 1080),
            )


if __name__ == "__main__":
    unittest.main()
