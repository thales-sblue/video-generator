"""Screen cards: what a card may state, where it lands, and how it is drawn."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator.adapters.screens import (
    ScreenRenderError,
    build_command,
    build_filter_chain,
    render_screen_card,
)
from video_generator.cli import main
from video_generator.domain.screens import (
    MAX_BODY_LINES,
    SCREEN_KINDS,
    ScreenCard,
    ScreenCardError,
    ScreenLayoutError,
    ScreenTheme,
    card_from_mapping,
    layout_card,
)


def _code_card(**overrides):
    payload = {
        "card_id": "lexicon",
        "kind": "code",
        "title": "src/video_generator/domain/editorial.py:253",
        "body": ('"verdade": _entry("magnifying glass over a document"),',),
    }
    payload.update(overrides)
    return ScreenCard(**payload)


class ScreenCardContractTests(unittest.TestCase):
    def test_every_kind_lays_out_inside_the_frame(self):
        cards = {
            "code": _code_card(),
            "terminal": ScreenCard("t", "terminal", title="$ doctor", body=("ffmpeg ok",)),
            "json": ScreenCard("j", "json", title="shot-plan.json", body=("{}",)),
            "statement": ScreenCard("s", "statement", body=("FUNCIONAVA.",)),
            "chain": ScreenCard("c", "chain", body=("TEXTO", "QUERY", "ASSET")),
            "compare": ScreenCard(
                "p", "compare", column_titles=("ANTES", "DEPOIS"),
                columns=(("lupa",), ("fachada",)),
            ),
            "stat": ScreenCard("n", "stat", title="59", body=("shots",)),
            "list": ScreenCard("l", "list", body=("LOCAL FIRST",)),
        }
        self.assertEqual(sorted(cards), sorted(SCREEN_KINDS))
        theme = ScreenTheme()
        for kind, card in cards.items():
            with self.subTest(kind=kind):
                layout = layout_card(card, theme)
                self.assertEqual(layout.kind, kind)
                self.assertTrue(layout.texts)
                for block in layout.texts:
                    self.assertGreaterEqual(block.x, 0)
                    self.assertGreaterEqual(block.y, 0)
                    self.assertLess(block.y, theme.height)
                    for line in block.lines:
                        self.assertLessEqual(
                            block.x + theme.measure(line, block.font, block.size),
                            theme.width - theme.margin_x,
                        )

    def test_layout_is_a_pure_function_of_card_and_theme(self):
        card = _code_card()
        self.assertEqual(layout_card(card).to_dict(), layout_card(card).to_dict())

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ScreenCardError):
            ScreenCard("x", "screenshot", body=("a",))

    def test_card_id_must_be_a_safe_slug(self):
        for card_id in ("Upper", "with space", "../escape", ""):
            with self.subTest(card_id=card_id), self.assertRaises(ScreenCardError):
                _code_card(card_id=card_id)

    def test_line_breaks_and_control_characters_are_refused(self):
        for line in ("one\ntwo", "tab\there", "bell\x07"):
            with self.subTest(line=line), self.assertRaises(ScreenCardError):
                _code_card(body=(line,))

    def test_body_line_budget_is_enforced(self):
        with self.assertRaises(ScreenCardError):
            _code_card(body=tuple(f"line {i}" for i in range(MAX_BODY_LINES + 1)))

    def test_highlight_must_point_at_a_body_line(self):
        with self.assertRaises(ScreenCardError):
            _code_card(highlight=(3,))

    def test_compare_requires_two_titled_columns(self):
        with self.assertRaises(ScreenCardError):
            ScreenCard("p", "compare", column_titles=("A",), columns=(("x",), ("y",)))
        with self.assertRaises(ScreenCardError):
            ScreenCard("p", "compare", column_titles=("A", "B"), columns=(("x",),))

    def test_a_non_compare_card_must_not_carry_columns(self):
        with self.assertRaises(ScreenCardError):
            _code_card(column_titles=("A", "B"), columns=(("x",), ("y",)))

    def test_a_body_that_cannot_fit_the_frame_is_refused_not_truncated(self):
        tall = ScreenTheme(body_size=90, line_gap=60)
        card = _code_card(body=tuple(f"line {index}" for index in range(20)))
        with self.assertRaises(ScreenLayoutError):
            layout_card(card, tall)

    def test_a_line_too_wide_for_the_frame_is_refused(self):
        card = ScreenCard("s", "statement", body=("X" * 90,))
        narrow = ScreenTheme(statement_size=104, margin_x=800)
        with self.assertRaises(ScreenLayoutError):
            layout_card(card, narrow)

    def test_a_long_chain_shrinks_instead_of_overflowing(self):
        short = layout_card(ScreenCard("c", "chain", body=("A", "B")))
        long = layout_card(
            ScreenCard("c", "chain", body=("A", "B", "C", "D", "E", "F", "G"))
        )
        self.assertLess(long.texts[-1].size, short.texts[-1].size)
        self.assertLess(max(block.y for block in long.texts), 1080)

    def test_highlighted_mono_lines_read_brighter_than_their_neighbours(self):
        theme = ScreenTheme()
        layout = layout_card(_code_card(body=("a", "b", "c"), highlight=(1,)), theme)
        body = {block.lines[0]: block for block in layout.texts}
        self.assertEqual(body["b"].colour, theme.foreground)
        self.assertEqual(body["a"].colour, theme.muted)
        self.assertTrue(any(box.colour == theme.accent for box in layout.boxes))

    def test_card_from_mapping_rejects_unknown_keys(self):
        with self.assertRaises(ScreenCardError):
            card_from_mapping({"card_id": "a", "kind": "list", "body": ["x"], "colour": "red"})

    def test_card_from_mapping_round_trips_a_card(self):
        card = _code_card(highlight=(0,), subtitle="sub", footer="foot")
        self.assertEqual(card_from_mapping(card.to_dict()), card)

    def test_theme_rejects_a_font_name_that_is_not_a_family(self):
        with self.assertRaises(ScreenCardError):
            ScreenTheme(mono_font="Consolas:fontcolor=red")

    def test_theme_rejects_a_colour_that_is_not_a_literal(self):
        with self.assertRaises(ScreenCardError):
            ScreenTheme(accent="red")


class ScreenRenderAdapterTests(unittest.TestCase):
    def test_no_card_text_ever_reaches_the_filter_graph(self):
        hostile = 'x:y=1\\,drawtext=text=pwned\'"'
        layout = layout_card(ScreenCard("h", "list", body=(hostile,)))
        chain = build_filter_chain(layout, [f"t{i:03d}.txt" for i in range(len(layout.texts))])
        self.assertNotIn("pwned", chain)
        self.assertNotIn(hostile, chain)
        self.assertIn("textfile=t000.txt", chain)

    def test_the_chain_draws_boxes_before_text(self):
        layout = layout_card(_code_card(body=("a", "b"), highlight=(0,)))
        chain = build_filter_chain(layout, [f"t{i:03d}.txt" for i in range(len(layout.texts))])
        self.assertLess(chain.rindex("drawbox="), chain.index("drawtext="))

    def test_the_command_is_a_single_frame_png_over_the_theme_ground(self):
        layout = layout_card(_code_card())
        command = build_command(layout, "null", "card.png")
        self.assertIn("color=c=0x0E1013:s=1920x1080", command)
        self.assertEqual(command[-1], "card.png")
        self.assertIn("-frames:v", command)
        self.assertNotIn("-c:a", command)

    def test_a_text_file_name_outside_the_ascii_set_is_refused(self):
        layout = layout_card(_code_card())
        with self.assertRaises(ScreenRenderError):
            build_filter_chain(layout, ["../escape.txt"] * len(layout.texts))

    def test_render_writes_each_block_to_its_own_utf8_file(self):
        layout = layout_card(ScreenCard("u", "list", body=("acentuação", "não")))
        seen = {}

        def runner(command, **kwargs):
            work = Path(kwargs["cwd"])
            for path in sorted(work.glob("*.txt")):
                seen[path.name] = path.read_text(encoding="utf-8")
            (work / "card.png").write_bytes(b"png")
            self.assertFalse(kwargs["shell"])
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "u.png"
            artifact = render_screen_card(
                layout, output, ffmpeg_path="ffmpeg", runner=runner
            )
        self.assertEqual(artifact.card_id, "u")
        self.assertIn("acentuação", "".join(seen.values()))

    def test_render_refuses_to_overwrite_an_existing_still(self):
        layout = layout_card(_code_card())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "card.png"
            output.write_bytes(b"existing")
            with self.assertRaises(ScreenRenderError):
                render_screen_card(layout, output, ffmpeg_path="ffmpeg", runner=lambda *a, **k: None)
            self.assertEqual(output.read_bytes(), b"existing")

    def test_render_reports_a_failing_ffmpeg_instead_of_claiming_success(self):
        layout = layout_card(_code_card())

        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "boom")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ScreenRenderError) as caught:
                render_screen_card(
                    layout, Path(directory) / "card.png",
                    ffmpeg_path="ffmpeg", runner=runner,
                )
        self.assertIn("boom", str(caught.exception))

    def test_render_requires_a_png_output(self):
        layout = layout_card(_code_card())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ScreenRenderError):
                render_screen_card(layout, Path(directory) / "card.jpg", ffmpeg_path="ffmpeg")


class RenderScreensCliTests(unittest.TestCase):
    def _deck(self, directory, **overrides):
        payload = {
            "schema_version": 1,
            "deck_id": "test-deck",
            "cards": [
                {"card_id": "one", "kind": "list", "body": ["LOCAL FIRST"]},
                {"card_id": "two", "kind": "stat", "title": "59", "body": ["shots"]},
            ],
        }
        payload.update(overrides)
        path = Path(directory) / "deck.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_layout_only_reports_without_touching_the_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = self._deck(directory)
            with patch("video_generator.cli.render_screen_card") as render:
                code = main([
                    "render-screens", "--deck", str(deck),
                    "--out-dir", str(Path(directory) / "out"), "--layout-only",
                ])
            self.assertEqual(code, 0)
            render.assert_not_called()
            self.assertFalse((Path(directory) / "out").exists())

    def test_a_deck_renders_every_card_and_writes_a_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = self._deck(directory)
            out_dir = Path(directory) / "out"

            def fake(layout, target, **kwargs):
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_bytes(b"png")
                from video_generator.adapters.screens import ScreenArtifact

                return ScreenArtifact(
                    layout.card_id, layout.kind, str(target), layout.width, layout.height, 3
                )

            with patch("video_generator.cli.render_screen_card", side_effect=fake):
                code = main([
                    "render-screens", "--deck", str(deck), "--out-dir", str(out_dir),
                ])
            self.assertEqual(code, 0)
            manifest = json.loads((out_dir / "screens.json").read_text(encoding="utf-8"))
            self.assertEqual([card["card_id"] for card in manifest["cards"]], ["one", "two"])
            self.assertTrue((out_dir / "one.png").is_file())

    def test_an_existing_still_is_kept_unless_force_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = self._deck(directory)
            out_dir = Path(directory) / "out"
            out_dir.mkdir()
            (out_dir / "one.png").write_bytes(b"keep")
            code = main(["render-screens", "--deck", str(deck), "--out-dir", str(out_dir)])
            self.assertEqual(code, 2)
            self.assertEqual((out_dir / "one.png").read_bytes(), b"keep")

    def test_a_duplicate_card_id_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = self._deck(
                directory,
                cards=[
                    {"card_id": "one", "kind": "list", "body": ["a"]},
                    {"card_id": "one", "kind": "list", "body": ["b"]},
                ],
            )
            code = main([
                "render-screens", "--deck", str(deck),
                "--out-dir", str(Path(directory) / "out"), "--layout-only",
            ])
            self.assertEqual(code, 2)

    def test_an_unknown_schema_version_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = self._deck(directory, schema_version=2)
            code = main([
                "render-screens", "--deck", str(deck),
                "--out-dir", str(Path(directory) / "out"), "--layout-only",
            ])
            self.assertEqual(code, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
