"""Measure the Editorial Visual Translation v1 cut against Visual Direction v1.

Baseline: ``output/visual-direction-v1/`` — the same script, the same duration,
the same canvas, the same seed, the same providers, the same direction policy
and the same narration. The only variables this cycle introduced are the
filmable-concept layer in front of the query, the positive editorial reading of
each candidate, and the removal of all 17 hand-written shot overrides.

Writes ``metrics.json``, ``shot-table.md`` and ``query-comparison.md`` under
``output/editorial-translation-v1/``. Read-only with respect to both renders.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_generator.domain.visual_concept import (  # noqa: E402
    non_english_tokens,
    stock_metaphor_props,
)

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "output" / "editorial-translation-v1"
BASE = ROOT / "output" / "visual-direction-v1"
OVERRIDES = ROOT / "projects" / "desumanizando_01" / "shot-overrides-visual-direction-v1.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(resolved: dict) -> str:
    url = resolved.get("provenance", {}).get("source_url", "")
    return url.rsplit("/", 2)[-2] if url else resolved.get("candidate_id", "?")


def _cut(root: Path) -> dict:
    shots = _load(root / "plan" / "shot-plan.json")["shots"]
    plan = _load(root / "resolved" / "asset-resolution-plan.json")
    resolved = {r["asset_id"]: r for r in plan["resolved"]}
    directions = {
        d["shot_id"]: d
        for d in _load(root / "plan" / "visual-direction.json")["directions"]
    }
    return {
        "root": root,
        "order": [s["shot_id"] for s in shots],
        "shots": {s["shot_id"]: s for s in shots},
        "resolved": resolved,
        "unresolved": plan["unresolved"],
        "directions": directions,
    }


def _portuguese_queries(cut: dict) -> list:
    """Queries whose leading term is a bare word from the narration.

    A query of one or two tokens that the translation layer did not author is,
    in this project's scripts, a Portuguese noun the planner fell back to. It
    is reported separately from "not authored here", because a multi-word
    English phrase from the concept lexicon is fine and a bare noun is not.

    The whole fallback list is scanned, not just the leading query: in the
    baseline an editorial override replaced the *leading* query of those 17
    shots by hand, and the bare Portuguese noun is still sitting behind it as
    the first fallback the resolver would have used.
    """

    out = []
    for shot_id in cut["order"]:
        shot = cut["shots"][shot_id]
        for query in [shot["visual_query"], *shot.get("asset_queries", ())]:
            unknown = non_english_tokens(query)
            if len(query.split()) <= 2 and unknown:
                out.append((shot_id, query))
                break
    return out


def _props(cut: dict) -> list:
    """Shots whose *query* asks a provider for a stock metaphor prop."""

    out = []
    for shot_id in cut["order"]:
        query = cut["shots"][shot_id]["visual_query"]
        props = stock_metaphor_props(query)
        if props:
            out.append((shot_id, query, list(props)))
    return out


def _prop_assets(cut: dict) -> list:
    """Shots whose *chosen asset* is named after a stock metaphor prop."""

    out = []
    for shot_id in cut["order"]:
        asset_id = cut["shots"][shot_id]["asset_id"]
        resolved = cut["resolved"].get(asset_id)
        if not resolved:
            continue
        slug = _slug(resolved).replace("-", " ")
        props = stock_metaphor_props(slug)
        if props:
            out.append((shot_id, _slug(resolved), list(props)))
    return out


def _families(cut: dict) -> Counter:
    return Counter(
        cut["resolved"][cut["shots"][s]["asset_id"]].get("visual_family", "?")
        for s in cut["order"]
        if cut["shots"][s]["asset_id"] in cut["resolved"]
    )


def _adjacent_family_repeats(cut: dict) -> tuple[int, int]:
    families = [
        cut["resolved"][cut["shots"][s]["asset_id"]].get("visual_family", "?")
        for s in cut["order"]
        if cut["shots"][s]["asset_id"] in cut["resolved"]
    ]
    pairs = list(zip(families, families[1:]))
    return sum(1 for a, b in pairs if a == b and a != "other"), len(pairs)


def _rejections(cut: dict) -> Counter:
    counts: Counter = Counter()
    for resolved in cut["resolved"].values():
        for reason in resolved.get("rejection_context", ()):
            counts[reason] += 1
    return counts


def main() -> int:
    new = _cut(NEW)
    base = _cut(BASE)
    overrides = set(_load(OVERRIDES)["shots"])

    new_slugs = {s: _slug(new["resolved"][new["shots"][s]["asset_id"]]) for s in new["order"] if new["shots"][s]["asset_id"] in new["resolved"]}
    base_slugs = {s: _slug(base["resolved"][base["shots"][s]["asset_id"]]) for s in base["order"] if base["shots"][s]["asset_id"] in base["resolved"]}
    changed = [s for s in new["order"] if new_slugs.get(s) != base_slugs.get(s)]

    concepts = Counter(
        (new["shots"][s].get("filmable_concept") or "-").split("/")[0]
        for s in new["order"]
    )
    concept_ids = Counter(
        new["shots"][s].get("filmable_concept") or "-" for s in new["order"]
    )

    new_adjacent, new_pairs = _adjacent_family_repeats(new)
    base_adjacent, base_pairs = _adjacent_family_repeats(base)

    metrics = {
        "baseline": "output/visual-direction-v1",
        "candidate": "output/editorial-translation-v1",
        "shots": {"baseline": len(base["order"]), "candidate": len(new["order"])},
        "manual_overrides": {"baseline": len(overrides), "candidate": 0},
        "bare_narration_queries": {
            "baseline": [s for s, _ in _portuguese_queries(base)],
            "candidate": [s for s, _ in _portuguese_queries(new)],
        },
        "queries_asking_for_a_stock_prop": {
            "baseline": _props(base),
            "candidate": _props(new),
        },
        "assets_named_after_a_stock_prop": {
            "baseline": _prop_assets(base),
            "candidate": _prop_assets(new),
        },
        "distinct_queries": {
            "baseline": len({base["shots"][s]["visual_query"] for s in base["order"]}),
            "candidate": len({new["shots"][s]["visual_query"] for s in new["order"]}),
        },
        "distinct_assets": {
            "baseline": len(set(base_slugs.values())),
            "candidate": len(set(new_slugs.values())),
        },
        "assets_changed_from_baseline": len(changed),
        "unresolved": {
            "baseline": [u["reason"] for u in base["unresolved"]],
            "candidate": [u["reason"] for u in new["unresolved"]],
        },
        "adjacent_same_visual_family": {
            "baseline": f"{base_adjacent}/{base_pairs}",
            "candidate": f"{new_adjacent}/{new_pairs}",
        },
        "visual_families": {
            "baseline": dict(_families(base).most_common()),
            "candidate": dict(_families(new).most_common()),
        },
        "rejection_context_on_the_winning_requirement": {
            "baseline": dict(_rejections(base).most_common()),
            "candidate": dict(_rejections(new).most_common()),
        },
        "filmable_concept_sources": dict(concepts.most_common()),
        "filmable_concepts_reused": {
            k: v for k, v in concept_ids.most_common() if v > 1
        },
        "distinct_filmable_concepts": len(concept_ids),
        "first_thirty_seconds": [],
    }

    elapsed = 0.0
    for shot_id in new["order"]:
        shot = new["shots"][shot_id]
        if elapsed >= 30.0:
            break
        metrics["first_thirty_seconds"].append(
            {
                "t": round(elapsed, 1),
                "shot": shot_id,
                "concept": shot.get("filmable_concept"),
                "query": shot["visual_query"],
                "asset": new_slugs.get(shot_id),
                "baseline_asset": base_slugs.get(shot_id),
                "composition": new["directions"][shot_id]["composition"],
                "motion": new["directions"][shot_id]["motion"],
                "grade": new["directions"][shot_id]["grade"],
            }
        )
        elapsed += shot["duration_seconds"]

    (NEW / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # --- shot table -------------------------------------------------------- #
    rows = [
        "# Editorial Visual Translation v1 — shot by shot",
        "",
        "| t | shot | meaning → concept | query | asset | candidates | refused | comp / motion / grade |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    elapsed = 0.0
    for shot_id in new["order"]:
        shot = new["shots"][shot_id]
        asset_id = shot["asset_id"]
        resolved = new["resolved"].get(asset_id, {})
        direction = new["directions"][shot_id]
        rows.append(
            "| {t} | {shot} | {concept} | `{query}` | {asset} | {considered} | {refused} | {comp} / {motion} / {grade} |".format(
                t=f"{elapsed:.1f}",
                shot=shot_id,
                concept=shot.get("filmable_concept") or "—",
                query=shot["visual_query"],
                asset=_slug(resolved) if resolved else "**unresolved**",
                considered=resolved.get("candidates_considered", "—"),
                refused=", ".join(resolved.get("rejection_context", ())) or "—",
                comp=direction["composition"],
                motion=direction["motion"],
                grade=direction["grade"],
            )
        )
        elapsed += shot["duration_seconds"]
    (NEW / "shot-table.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    # --- query comparison -------------------------------------------------- #
    rows = [
        "# Query comparison — Visual Direction v1 → Editorial Visual Translation v1",
        "",
        "`OV` marks one of the 17 shots that needed a hand-written editorial",
        "override in the baseline. None is overridden in the candidate.",
        "",
        "| shot | baseline query | candidate query | concept | asset changed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for shot_id in new["order"]:
        mark = "OV " if shot_id in overrides else ""
        rows.append(
            "| {mark}{shot} | `{old}` | `{newq}` | {concept} | {changed} |".format(
                mark=mark,
                shot=shot_id,
                old=base["shots"][shot_id]["visual_query"],
                newq=new["shots"][shot_id]["visual_query"],
                concept=new["shots"][shot_id].get("filmable_concept") or "—",
                changed="yes" if shot_id in changed else "no",
            )
        )
    (NEW / "query-comparison.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
