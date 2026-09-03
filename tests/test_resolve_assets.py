"""Tests for the impure asset-resolution orchestrator (search -> rank ->
select -> acquire -> hash -> validate -> provenance), driven by fake
providers over real temp files. No network."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from video_generator.domain import (
    AssetRequirement,
    AssetRequirements,
    NarrativeScript,
    plan_scenes,
    plan_shots,
)
from video_generator.domain.assets import AssetCandidate
from video_generator.adapters.asset_providers import AcquiredFile
from video_generator.resolve import ResolveError, resolve_assets

SCRIPT = """\
Uma pessoa inteligente pode acreditar em algo absurdo.

O raciocinio serve para descobrir a verdade, mas tambem para proteger crencas antigas.

Coloque tudo isso dentro da internet: um sistema que observa o que prende a atencao.

Entao a pergunta muda: que coisas absurdas eu justifico porque me acho esperto demais?
"""


class _FakeProvider:
    """Serves in-memory candidates that point at real files it writes on
    acquire, so the orchestrator's copy/hash/validate path runs for real."""

    def __init__(self, source_kind, catalogue):
        self.source_kind = source_kind
        self._catalogue = catalogue  # list[dict]
        self.available = True
        self.search_calls = []

    def search(self, terms, *, media_type, orientation, min_duration_seconds, limit):
        self.search_calls.append(tuple(terms))
        out = []
        for row in self._catalogue:
            if row["media_type"] != media_type:
                continue
            out.append(
                AssetCandidate(
                    candidate_id=f"{self.source_kind}:{row['id']}",
                    source_kind=self.source_kind,
                    source_id=str(row["id"]),
                    media_type=row["media_type"],
                    local_path=None,
                    remote_locator=f"mem://{row['id']}",
                    title=row.get("title", row["id"]),
                    description=row.get("description", ""),
                    tags=tuple(row.get("tags", ())),
                    width=row.get("width", 1920),
                    height=row.get("height", 1080),
                    duration_seconds=row.get("duration_seconds"),
                    license=row.get("license", "CC0-1.0"),
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                    author=row.get("author", "Tester"),
                    source_url=f"https://example.org/{row['id']}",
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out[:limit]

    def acquire(self, candidate, dest_path):
        import hashlib

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = f"bytes-for-{candidate.source_id}".encode()
        dest.write_bytes(payload)
        return AcquiredFile(
            local_path=str(dest),
            original_filename=f"{candidate.source_id}.jpg",
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes_written=len(payload),
        )


def _plans():
    script = NarrativeScript.from_text("myside", SCRIPT, total_duration_seconds=90.0)
    scene_plan = plan_scenes(script, seed=7)
    shot_plan, requirements = plan_shots(scene_plan, seed=7, orientation="landscape")
    return script, scene_plan, shot_plan, requirements


def _shot_context(shot_plan):
    ctx = {}
    for shot in shot_plan.shots:
        ctx[shot.shot_id] = {
            "visual_query": shot.visual_query,
            "purpose": shot.purpose,
            "visual_intent": shot.purpose,
        }
    return ctx


class ResolveAssetsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "resolved"
        self.addCleanup(self._tmp.cleanup)
        _, _, self.shot_plan, self.requirements = _plans()
        # a catalogue rich enough to cover most image requirements
        self.catalogue = [
            {"id": f"img{i}", "media_type": "image",
             "tags": ["pessoa", "mesa", "escritorio", "noticia", "internet",
                      "raciocinio", "verdade", "crenca", "atencao", "mente"][: (i % 5) + 3],
             "description": "pessoa lendo uma noticia no computador em um escritorio",
             "width": 1920, "height": 1080}
            for i in range(12)
        ]

    def _providers(self, extra=()):
        return [_FakeProvider("local_library", self.catalogue + list(extra))]

    def test_every_resolved_asset_has_full_provenance(self):
        plan = resolve_assets(
            self.shot_plan, self.requirements, self._providers(),
            out_dir=self.out, shot_context=_shot_context(self.shot_plan),
            clock=lambda: "2026-09-02T10:00:00Z",
        ).plan
        self.assertTrue(plan.resolved)
        for r in plan.resolved:
            self.assertIsNotNone(r.provenance)
            self.assertRegex(r.provenance.sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(Path(r.local_path).exists())
            self.assertEqual(r.provenance.license, "CC0-1.0")

    def test_bindings_cover_exactly_resolved_assets(self):
        result = resolve_assets(
            self.shot_plan, self.requirements, self._providers(),
            out_dir=self.out, shot_context=_shot_context(self.shot_plan),
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        bindings = result.plan.to_bindings()
        self.assertEqual(set(bindings), {r.asset_id for r in result.plan.resolved})
        result.plan.validate_against(result.revised_requirements)

    def test_poor_query_becomes_needs_editorial_override(self):
        bad = AssetRequirement(
            asset_id="asset_scene_99_01", type="image",
            query="medium symbolic, outras, palavras",
            duration_needed_seconds=3.0, orientation="landscape",
            purpose="mostrar: outras / palavras — beat 1/2",
            used_by=("scene_99_shot_01",),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(bad,),
        )
        # a shot plan is not needed for this path; pass the real one's id via reqs
        result = resolve_assets(
            self.shot_plan, reqs, self._providers(), out_dir=self.out,
            shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(len(result.plan.unresolved), 1)
        self.assertEqual(result.plan.unresolved[0].reason, "needs_editorial_override")

    def test_structural_only_match_does_not_resolve(self):
        # A candidate that fits on media_type + orientation + resolution but
        # shares no visual term with the shot must NOT be resolved.
        req = AssetRequirement(
            asset_id="asset_scene_95_01", type="image",
            query="wide establishing, biblioteca antiga com prateleiras de madeira",
            duration_needed_seconds=3.0, orientation="landscape",
            purpose="mostrar: biblioteca / livros — beat 1/2",
            used_by=("scene_95_shot_01",),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(req,),
        )
        off_topic = [
            {"id": "beach", "media_type": "image",
             "tags": ["oceano", "praia", "areia", "ondas"],
             "title": "ondas quebrando numa praia",
             "description": "litoral ensolarado ao amanhecer",
             "width": 1920, "height": 1080},
        ]
        result = resolve_assets(
            self.shot_plan, reqs, [_FakeProvider("local_library", off_topic)],
            out_dir=self.out, shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(result.plan.resolved, ())
        self.assertEqual(len(result.plan.unresolved), 1)
        self.assertEqual(result.plan.unresolved[0].reason, "no_semantic_match")

        # add one on-topic candidate and the same requirement now resolves
        on_topic = off_topic + [
            {"id": "library", "media_type": "image",
             "tags": ["biblioteca", "livros", "prateleiras", "madeira"],
             "width": 1920, "height": 1080},
        ]
        ok = resolve_assets(
            self.shot_plan, reqs, [_FakeProvider("local_library", on_topic)],
            out_dir=self.out / "ok", shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(len(ok.plan.resolved), 1)
        self.assertEqual(ok.plan.resolved[0].candidate_id, "local_library:library")

    def test_no_candidate_of_right_type_is_unresolved(self):
        video_only = AssetRequirement(
            asset_id="asset_scene_98_01", type="video",
            query="wide b roll human, pessoa andando em um corredor de escritorio",
            duration_needed_seconds=6.0, orientation="landscape",
            purpose="mostrar: corredor / escritorio — beat 1/2",
            used_by=("scene_98_shot_01",),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(video_only,),
        )
        result = resolve_assets(
            self.shot_plan, reqs, self._providers(), out_dir=self.out,
            shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        # the image-only fake library returns nothing for a video requirement
        self.assertEqual(result.plan.unresolved[0].reason, "no_candidates")

    def test_short_video_candidate_is_rejected_but_long_one_wins(self):
        req = AssetRequirement(
            asset_id="asset_scene_97_01", type="video",
            query="wide environment, corredor de escritorio vazio a noite",
            duration_needed_seconds=6.0, orientation="landscape",
            purpose="mostrar: corredor / escritorio — beat 1/2",
            used_by=("scene_97_shot_01",),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(req,),
        )
        catalogue = [
            {"id": "vid_short", "media_type": "video", "duration_seconds": 2.0,
             "tags": ["corredor", "escritorio", "vazio"], "width": 1920, "height": 1080},
            {"id": "vid_long", "media_type": "video", "duration_seconds": 15.0,
             "tags": ["corredor", "escritorio", "vazio", "noite"],
             "width": 1920, "height": 1080},
        ]
        result = resolve_assets(
            self.shot_plan, reqs, [_FakeProvider("local_library", catalogue)],
            out_dir=self.out, shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(len(result.plan.resolved), 1)
        self.assertEqual(result.plan.resolved[0].candidate_id, "local_library:vid_long")

    def test_choice_is_deterministic_across_runs(self):
        ctx = _shot_context(self.shot_plan)
        a = resolve_assets(self.shot_plan, self.requirements, self._providers(),
                           out_dir=self.out / "a", shot_context=ctx,
                           clock=lambda: "2026-09-02T10:00:00Z")
        b = resolve_assets(self.shot_plan, self.requirements, self._providers(),
                           out_dir=self.out / "b", shot_context=ctx,
                           clock=lambda: "2026-09-02T10:00:00Z")
        self.assertEqual(
            [(r.asset_id, r.candidate_id) for r in a.plan.resolved],
            [(r.asset_id, r.candidate_id) for r in b.plan.resolved],
        )
        self.assertEqual(
            [u.asset_id for u in a.plan.unresolved],
            [u.asset_id for u in b.plan.unresolved],
        )

    def test_semantic_reuse_split_materialises_distinct_files(self):
        # Build a requirement shared by two shots whose intents don't overlap.
        shared = AssetRequirement(
            asset_id="asset_scene_01_01", type="image",
            query="close document, noticia, jornal, manchete",
            duration_needed_seconds=4.0, orientation="landscape",
            purpose="mostrar: noticia / jornal — beat 1/2",
            used_by=("scene_01_shot_01", "scene_05_shot_02"),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(shared,),
        )
        ctx = {
            "scene_01_shot_01": {
                "visual_query": "close document, noticia, jornal, manchete",
                "purpose": "mostrar: noticia / jornal — beat 1/2",
                "visual_intent": "a mesma noticia lida de dois jeitos",
            },
            "scene_05_shot_02": {
                "visual_query": "wide environment, servidores em um data center",
                "purpose": "mostrar: servidores / internet — beat 2/3",
                "visual_intent": "a infraestrutura da internet",
            },
        }
        catalogue = [
            {"id": "news", "media_type": "image",
             "tags": ["noticia", "jornal", "manchete"], "width": 1920, "height": 1080},
            {"id": "servers", "media_type": "image",
             "tags": ["servidores", "internet", "data", "center"],
             "width": 1920, "height": 1080},
        ]
        result = resolve_assets(
            self.shot_plan, reqs, [_FakeProvider("local_library", catalogue)],
            out_dir=self.out, shot_context=ctx,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(result.review.split_count, 1)
        self.assertEqual(len(result.plan.resolved), 2)
        chosen = sorted(r.candidate_id for r in result.plan.resolved)
        self.assertEqual(chosen, ["local_library:news", "local_library:servers"])
        paths = {r.local_path for r in result.plan.resolved}
        self.assertEqual(len(paths), 2)

    def test_crafted_asset_id_cannot_escape_out_dir(self):
        evil = AssetRequirement(
            asset_id="../../etc/pwned", type="image",
            query="wide establishing, pessoa lendo noticia em um escritorio",
            duration_needed_seconds=3.0, orientation="landscape",
            purpose="mostrar: pessoa / noticia — beat 1/2",
            used_by=("scene_96_shot_01",),
        )
        reqs = AssetRequirements(
            plan_id="myside-asset-requirements", shot_plan_id="myside-shot-plan",
            script_id="myside", orientation="landscape", requirements=(evil,),
        )
        result = resolve_assets(
            self.shot_plan, reqs, self._providers(), out_dir=self.out,
            shot_context={}, do_review_reuse=False,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertEqual(len(result.plan.resolved), 1)
        written = Path(result.plan.resolved[0].local_path).resolve()
        self.assertTrue(str(written).startswith(str((self.out / "files").resolve())))
        self.assertNotIn("..", written.name)

    def test_does_not_overwrite_a_pre_existing_different_file(self):
        ctx = _shot_context(self.shot_plan)
        probe_run = resolve_assets(
            self.shot_plan, self.requirements, self._providers(),
            out_dir=self.out / "probe", shot_context=ctx,
            clock=lambda: "2026-09-02T10:00:00Z",
        )
        self.assertTrue(probe_run.plan.resolved)
        squatted_name = Path(probe_run.plan.resolved[0].local_path).name
        fresh = self.out / "fresh"
        (fresh / "files").mkdir(parents=True)
        (fresh / "files" / squatted_name).write_bytes(b"not-ours-foreign-bytes")
        with self.assertRaises(ResolveError):
            resolve_assets(self.shot_plan, self.requirements, self._providers(),
                           out_dir=fresh, shot_context=ctx,
                           clock=lambda: "2026-09-02T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
