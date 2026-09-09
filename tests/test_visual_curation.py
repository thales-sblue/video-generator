"""Visual Curation v1 — the invariants and the failures that matter.

The stage exists to stop three things that actually happened in earlier cuts:
a Portuguese query handed to an English provider, a locale tag turned into a
national flag, and a render that quietly substituted an asset nobody approved.
Each of those has a test here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_generator.cli import main as cli_main
from video_generator.curation import (
    lock_from_files,
    load_curation_set,
    render_review_sheet,
    verify_visual_lock,
    write_curation_set,
    write_review_sheet,
)
from video_generator.domain.curation import (
    SCHEMA_VERSION,
    CurationAsset,
    CurationDecision,
    CurationError,
    CurationOption,
    CurationSet,
    build_visual_lock,
    editorial_gate,
    generic_fallback_reasons,
    group_consecutive,
    lock_violations,
    metadata_symbol_rejections,
    needs_human_approval,
    non_english_query_reasons,
    parse_approvals,
)


def make_option(option_id="A", **overrides):
    payload = {
        "option_id": option_id,
        "material_kind": "project_material",
        "visual_concept": "the repository's own test suite finishing with exit zero",
        "justification": "it is the evidence the narration is talking about",
        "assets": (CurationAsset(path="output/final.mp4", role="clip"),),
    }
    payload.update(overrides)
    return CurationOption(**payload)


def make_decision(sequence_id="seq_01", **overrides):
    payload = {
        "sequence_id": sequence_id,
        "block_id": "01_hook",
        "title": "Hook",
        "shot_indexes": (1, 2),
        "start_seconds": 0.0,
        "seconds": 6.0,
        "options": (make_option("A"),),
        "needs_approval": False,
    }
    payload.update(overrides)
    return CurationDecision(**payload)


class QueryLanguageTests(unittest.TestCase):
    def test_a_portuguese_query_is_rejected_before_it_reaches_a_provider(self):
        # The real defect: this query returned "The Flag of Brazil" from Pexels.
        reasons = non_english_query_reasons(
            "mao apagando setas num quadro branco e redesenhando na ordem inversa"
        )
        self.assertTrue(any(reason.startswith("query_not_english") for reason in reasons))

    def test_accents_alone_are_enough_to_reject(self):
        self.assertIn(
            "query_not_english:diacritics",
            non_english_query_reasons("papéis e gráficos sobre uma mesa de trabalho"),
        )

    def test_a_keyword_is_not_a_scene(self):
        self.assertIn(
            "query_is_a_keyword_not_a_scene",
            non_english_query_reasons("artificial intelligence"),
        )

    def test_an_english_scene_description_passes(self):
        self.assertEqual(
            (),
            non_english_query_reasons(
                "software developer reviewing an automated video editing pipeline"
            ),
        )

    def test_a_missing_query_is_named_as_such(self):
        self.assertEqual(("query_missing",), non_english_query_reasons("   "))


class MetadataAsImageTests(unittest.TestCase):
    def test_a_locale_tag_may_not_become_a_flag(self):
        reasons = metadata_symbol_rejections(
            query="waving flag of brazil in the wind",
            visual_concept="the narration is in PT-BR",
        )
        self.assertTrue(
            any(reason.startswith("national_symbol_without_narrative_reason") for reason in reasons)
        )
        self.assertTrue(
            any(reason.startswith("metadata_as_visual_subject") for reason in reasons)
        )

    def test_the_national_palette_is_flagged_next_to_a_symbol(self):
        self.assertIn(
            "national_palette",
            metadata_symbol_rejections(query="green and yellow flag close up"),
        )

    def test_a_map_is_allowed_when_the_narration_is_about_maps(self):
        self.assertEqual(
            (),
            metadata_symbol_rejections(
                query="hand tracing a route across a paper map on a table",
                narrative_terms=("map", "route"),
            ),
        )

    def test_a_neutral_scene_passes(self):
        self.assertEqual(
            (),
            metadata_symbol_rejections(
                query="developer reading a failing test report on a monitor"
            ),
        )


class GenericFallbackTests(unittest.TestCase):
    def test_the_named_cliches_are_rejected(self):
        for phrase in ("ai robot", "glowing brain", "matrix code", "server room"):
            with self.subTest(phrase=phrase):
                self.assertTrue(generic_fallback_reasons(f"a cinematic {phrase} shot"))

    def test_a_specific_scene_is_not_a_cliche(self):
        self.assertEqual(
            (),
            generic_fallback_reasons(
                "close up of a terminal printing a sha-256 digest line by line"
            ),
        )


class EditorialGateTests(unittest.TestCase):
    def test_an_external_asset_loses_to_real_project_material(self):
        option = make_option(
            "B",
            material_kind="external_asset",
            query="software developer reviewing an automated video pipeline",
            visual_concept="someone reviewing an automated cut",
            assets=(CurationAsset(path="output/resolved/x.mp4", role="clip"),),
        )
        self.assertIn(
            "real_project_material_covers_this",
            editorial_gate(option, real_material_available=True),
        )

    def test_the_same_asset_passes_when_nothing_real_covers_it(self):
        option = make_option(
            "B",
            material_kind="external_asset",
            query="software developer reviewing an automated video editing pipeline",
            visual_concept="someone reviewing an automated cut",
            assets=(CurationAsset(path="output/resolved/x.mp4", role="clip"),),
        )
        self.assertEqual((), editorial_gate(option, real_material_available=False))

    def test_an_external_asset_without_a_query_is_rejected(self):
        option = make_option(
            "B",
            material_kind="external_asset",
            visual_concept="something vaguely technological",
            assets=(CurationAsset(path="output/resolved/x.mp4", role="clip"),),
        )
        self.assertIn("query_missing", editorial_gate(option))

    def test_reasons_are_reported_once_each(self):
        option = make_option(
            "B",
            material_kind="external_asset",
            query="brazil flag waving",
            visual_concept="brazil flag waving",
            assets=(CurationAsset(path="output/resolved/x.mp4", role="clip"),),
        )
        reasons = editorial_gate(option)
        self.assertEqual(len(reasons), len(set(reasons)))


class ApprovalRoutingTests(unittest.TestCase):
    def test_real_material_does_not_need_a_human(self):
        self.assertEqual(
            (False, None), needs_human_approval(material_kinds=("project_material",))
        )

    def test_a_diagram_from_the_project_does_not_need_a_human(self):
        self.assertEqual(
            (False, None), needs_human_approval(material_kinds=("project_diagram",))
        )

    def test_an_external_asset_always_needs_a_human(self):
        self.assertEqual(
            (True, "external_asset"),
            needs_human_approval(material_kinds=("project_material", "external_asset")),
        )

    def test_choosing_between_plausible_frames_needs_a_human(self):
        self.assertEqual(
            (True, "frame_choice"),
            needs_human_approval(
                material_kinds=("project_material",), plausible_alternatives=3
            ),
        )

    def test_an_unknown_material_kind_fails_closed(self):
        with self.assertRaises(CurationError):
            needs_human_approval(material_kinds=("vibes",))


class DecisionShapeTests(unittest.TestCase):
    def test_a_decision_may_not_offer_more_than_three_options(self):
        options = tuple(make_option(letter) for letter in "ABCD")
        with self.assertRaises(CurationError):
            make_decision(needs_approval=True, options=options)

    def test_an_approval_decision_needs_at_least_two_options(self):
        with self.assertRaises(CurationError):
            make_decision(
                needs_approval=True,
                decision_reason="frame_choice",
                recommended_option_id="A",
                recommendation_reason="only one",
            )

    def test_a_recommendation_must_point_at_an_offered_option(self):
        with self.assertRaises(CurationError):
            make_decision(
                needs_approval=True,
                decision_reason="frame_choice",
                options=(make_option("A"), make_option("B")),
                recommended_option_id="C",
                recommendation_reason="nope",
            )

    def test_a_recommendation_must_say_why(self):
        with self.assertRaises(CurationError):
            make_decision(
                needs_approval=True,
                decision_reason="frame_choice",
                options=(make_option("A"), make_option("B")),
                recommended_option_id="A",
                recommendation_reason="   ",
            )

    def test_subjective_material_may_not_skip_approval(self):
        subjective = make_option(
            "A",
            material_kind="graphic_composition",
            assets=(CurationAsset(path="output/screens/a.png", role="card"),),
        )
        with self.assertRaises(CurationError):
            make_decision(needs_approval=False, options=(subjective,))

    def test_a_sequence_id_has_a_fixed_shape(self):
        with self.assertRaises(CurationError):
            make_decision(sequence_id="hook")


class ApprovalParsingTests(unittest.TestCase):
    def test_the_shorthand_the_author_asked_for_is_readable(self):
        answers = parse_approvals(
            "SEQ 03 -> B\nSEQ 05 → A\nSEQ 07 → regenerar\nseq_10: C\n"
        )
        self.assertEqual(answers["seq_03"]["verdict"], "B")
        self.assertEqual(answers["seq_05"]["verdict"], "A")
        self.assertEqual(answers["seq_07"]["verdict"], "regenerate")
        self.assertEqual(answers["seq_10"]["verdict"], "C")

    def test_a_note_in_parentheses_survives_as_a_manual_override(self):
        answers = parse_approvals("SEQ 02 -> C (segura mais 1s no primeiro shot)")
        self.assertEqual(answers["seq_02"]["note"], "segura mais 1s no primeiro shot")

    def test_an_unreadable_line_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(CurationError):
            parse_approvals("SEQ 03 talvez o B")

    def test_answering_the_same_sequence_twice_is_an_error(self):
        with self.assertRaises(CurationError):
            parse_approvals("SEQ 03 -> A\nSEQ 03 -> B")

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual({"seq_01"}, set(parse_approvals("# nota\n\nSEQ 01 -> A\n")))


class VisualLockTests(unittest.TestCase):
    def setUp(self):
        self.decision = make_decision(
            "seq_01",
            needs_approval=True,
            decision_reason="frame_choice",
            options=(make_option("A"), make_option("B")),
            recommended_option_id="B",
            recommendation_reason="B lands on the exit-zero line",
        )
        self.objective = make_decision("seq_02", start_seconds=6.0)
        self.set = CurationSet(
            set_id="canal_dev_01-curation",
            project_id="canal_dev_01",
            video_title="Um titulo",
            decisions=(self.decision, self.objective),
        )

    def test_a_lock_records_what_the_human_chose(self):
        lock = build_visual_lock(
            self.set,
            parse_approvals("SEQ 01 -> A"),
            approved_by="thales",
            approved_at="2026-09-09T00:00:00Z",
        )
        self.assertEqual(lock["schema_version"], SCHEMA_VERSION)
        first = lock["sequences"][0]
        self.assertEqual(first["approved_option"], "A")
        self.assertFalse(first["followed_recommendation"])
        self.assertEqual(lock["sequences"][1]["approved_option"], "A")

    def test_an_unanswered_decision_refuses_to_lock(self):
        with self.assertRaises(CurationError) as caught:
            build_visual_lock(
                self.set,
                parse_approvals("SEQ 02 -> A"),
                approved_by="thales",
                approved_at="2026-09-09T00:00:00Z",
            )
        self.assertIn("seq_01", str(caught.exception))

    def test_regenerate_is_not_an_approval(self):
        with self.assertRaises(CurationError) as caught:
            build_visual_lock(
                self.set,
                parse_approvals("SEQ 01 -> regenerar"),
                approved_by="thales",
                approved_at="2026-09-09T00:00:00Z",
            )
        self.assertIn("new candidates", str(caught.exception))

    def test_an_approval_for_an_unknown_sequence_is_refused(self):
        with self.assertRaises(CurationError):
            build_visual_lock(
                self.set,
                parse_approvals("SEQ 01 -> A\nSEQ 44 -> B"),
                approved_by="thales",
                approved_at="2026-09-09T00:00:00Z",
            )

    def test_a_note_is_carried_into_the_lock_as_a_manual_override(self):
        lock = build_visual_lock(
            self.set,
            parse_approvals("SEQ 01 -> B (corta 0.5s no fim)"),
            approved_by="thales",
            approved_at="2026-09-09T00:00:00Z",
        )
        self.assertEqual(lock["sequences"][0]["manual_overrides"], ["corta 0.5s no fim"])


class LockFailsClosedTests(unittest.TestCase):
    def _lock(self, path, digest):
        return {
            "schema_version": SCHEMA_VERSION,
            "sequences": [
                {
                    "sequence_id": "seq_01",
                    "assets": [{"path": str(path), "role": "clip", "sha256": digest}],
                }
            ],
        }

    def test_a_missing_asset_stops_the_render(self):
        problems = lock_violations(
            self._lock("output/gone.mp4", "0" * 64), digest_of=lambda path: None
        )
        self.assertTrue(any("missing" in problem for problem in problems))

    def test_a_changed_asset_stops_the_render(self):
        problems = lock_violations(
            self._lock("output/final.mp4", "a" * 64), digest_of=lambda path: "b" * 64
        )
        self.assertTrue(any("changed on disk" in problem for problem in problems))

    def test_an_intact_lock_reports_nothing(self):
        self.assertEqual(
            (),
            lock_violations(
                self._lock("output/final.mp4", "a" * 64), digest_of=lambda path: "a" * 64
            ),
        )

    def test_an_unknown_schema_is_refused(self):
        problems = lock_violations({"schema_version": 99}, digest_of=lambda path: None)
        self.assertTrue(problems)

    def test_a_sequence_without_assets_is_refused(self):
        lock = {
            "schema_version": SCHEMA_VERSION,
            "sequences": [{"sequence_id": "seq_01", "assets": []}],
        }
        self.assertTrue(lock_violations(lock, digest_of=lambda path: "a" * 64))


class GroupingTests(unittest.TestCase):
    def test_consecutive_shots_sharing_a_decision_become_one_sequence(self):
        shots = [("a", 1), ("a", 2), ("b", 3), ("a", 4)]
        groups = group_consecutive(shots, key=lambda shot: shot[0])
        self.assertEqual([marker for marker, _ in groups], ["a", "b", "a"])
        self.assertEqual(len(groups[0][1]), 2)


class RoundTripAndSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.media = self.root / "output" / "final.mp4"
        self.media.parent.mkdir(parents=True, exist_ok=True)
        self.media.write_bytes(b"not really a video, but a real file")
        self.preview = self.root / "previews" / "seq_01_a.png"
        self.preview.parent.mkdir(parents=True, exist_ok=True)
        self.preview.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        asset = CurationAsset(path="output/final.mp4", role="clip")
        self.set = CurationSet(
            set_id="demo-curation",
            project_id="demo",
            video_title="Demo",
            decisions=(
                CurationDecision(
                    sequence_id="seq_01",
                    block_id="01_hook",
                    title="Hook",
                    shot_indexes=(1, 2),
                    start_seconds=0.0,
                    seconds=6.0,
                    needs_approval=True,
                    decision_reason="frame_choice",
                    options=(
                        make_option("A", assets=(asset,), preview="previews/seq_01_a.png"),
                        make_option("B", assets=(asset,)),
                    ),
                    recommended_option_id="A",
                    recommendation_reason="A opens on the failure, not on the promise",
                    gate_rejections=(("pexels:15483829", ("national_symbol_without_narrative_reason:flag",)),),
                ),
            ),
        )

    def test_a_curation_set_survives_a_round_trip(self):
        path = write_curation_set(self.set, self.root / "curation-set.json")
        again = load_curation_set(path)
        self.assertEqual(again.to_payload(), self.set.to_payload())

    def test_an_unknown_schema_version_is_refused_on_load(self):
        path = self.root / "bad.json"
        path.write_text(json.dumps({"schema_version": 7}), encoding="utf-8")
        with self.assertRaises(CurationError):
            load_curation_set(path)

    def test_the_review_sheet_is_one_self_contained_file(self):
        html = render_review_sheet(self.set, preview_root=self.root)
        self.assertIn("RECOMENDADO: A", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("SEQ 01 -&gt; A", html)
        self.assertIn("national_symbol_without_narrative_reason", html)
        self.assertNotIn("<script", html)

    def test_the_sheet_survives_a_missing_preview(self):
        html = render_review_sheet(self.set, preview_root=self.root / "nowhere")
        self.assertIn("sem preview", html)

    def test_locking_stamps_every_asset_with_its_digest(self):
        lock = lock_from_files(
            self.set,
            "SEQ 01 -> A",
            approved_by="thales",
            approved_at="2026-09-09T00:00:00Z",
            root=self.root,
        )
        digest = lock["sequences"][0]["assets"][0]["sha256"]
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual((), verify_visual_lock(lock, root=self.root))

    def test_locking_refuses_an_asset_that_is_not_on_disk(self):
        with self.assertRaises(CurationError):
            lock_from_files(
                self.set,
                "SEQ 01 -> A",
                approved_by="thales",
                approved_at="2026-09-09T00:00:00Z",
                root=self.root / "nowhere",
            )

    def test_a_touched_asset_is_reported_not_replaced(self):
        lock = lock_from_files(
            self.set,
            "SEQ 01 -> A",
            approved_by="thales",
            approved_at="2026-09-09T00:00:00Z",
            root=self.root,
        )
        self.media.write_bytes(b"a different file entirely")
        problems = verify_visual_lock(lock, root=self.root)
        self.assertTrue(any("changed on disk" in problem for problem in problems))


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        media = self.root / "final.mp4"
        media.write_bytes(b"a real file")
        asset = CurationAsset(path=str(media), role="clip")
        curation_set = CurationSet(
            set_id="demo-curation",
            project_id="demo",
            video_title="Demo",
            decisions=(
                CurationDecision(
                    sequence_id="seq_01",
                    block_id="01_hook",
                    title="Hook",
                    shot_indexes=(1,),
                    start_seconds=0.0,
                    seconds=3.0,
                    needs_approval=True,
                    decision_reason="frame_choice",
                    options=(
                        make_option("A", assets=(asset,)),
                        make_option("B", assets=(asset,)),
                    ),
                    recommended_option_id="B",
                    recommendation_reason="B holds the terminal long enough to read",
                ),
            ),
        )
        self.set_path = write_curation_set(curation_set, self.root / "curation-set.json")
        self.review = self.root / "review.html"
        self.approvals = self.root / "approvals.txt"
        self.approvals.write_text("SEQ 01 -> B\n", encoding="utf-8")
        self.lock_path = self.root / "visual-lock.json"

    def test_curate_visuals_writes_the_sheet_and_reports_the_pending_decisions(self):
        code = cli_main(
            [
                "curate-visuals",
                "--set",
                str(self.set_path),
                "--review-out",
                str(self.review),
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(self.review.is_file())

    def test_visual_lock_then_verify_visual_lock(self):
        self.assertEqual(
            0,
            cli_main(
                [
                    "visual-lock",
                    "--set",
                    str(self.set_path),
                    "--approvals",
                    str(self.approvals),
                    "--approved-by",
                    "thales",
                    "--approved-at",
                    "2026-09-09T00:00:00Z",
                    "--out",
                    str(self.lock_path),
                ]
            ),
        )
        lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["sequences"][0]["approved_option"], "B")
        self.assertEqual(
            0, cli_main(["verify-visual-lock", "--lock", str(self.lock_path), "--json"])
        )

    def test_visual_lock_refuses_an_unanswered_decision(self):
        self.approvals.write_text("# nothing decided\nSEQ 01 -> regenerar\n", encoding="utf-8")
        self.assertEqual(
            3,
            cli_main(
                [
                    "visual-lock",
                    "--set",
                    str(self.set_path),
                    "--approvals",
                    str(self.approvals),
                    "--approved-by",
                    "thales",
                    "--out",
                    str(self.lock_path),
                ]
            ),
        )
        self.assertFalse(self.lock_path.exists())

    def test_verify_visual_lock_exits_one_when_an_asset_disappeared(self):
        cli_main(
            [
                "visual-lock",
                "--set",
                str(self.set_path),
                "--approvals",
                str(self.approvals),
                "--approved-by",
                "thales",
                "--out",
                str(self.lock_path),
            ]
        )
        (self.root / "final.mp4").unlink()
        self.assertEqual(
            1, cli_main(["verify-visual-lock", "--lock", str(self.lock_path)])
        )

    def test_write_review_sheet_creates_missing_directories(self):
        target = self.root / "deep" / "nested" / "review.html"
        written = write_review_sheet(load_curation_set(self.set_path), target)
        self.assertTrue(written.is_file())


if __name__ == "__main__":
    unittest.main()
