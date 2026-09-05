"""Measure the Visual Direction v1 cut against the editorial baseline.

Baseline: ``output/relevance-eval/render-final/`` — the Semantic Visual
Relevance cut, same script, same duration, same canvas, same seed, same
providers. The only variables this cycle introduced are the direction layer,
the hard editorial veto and the two fixed defects.

Writes ``metrics.json`` and ``shot-table.md`` under
``output/visual-direction-v1/``. Read-only with respect to both renders.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_generator.domain.relevance import visual_family  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "output" / "visual-direction-v1"
BASE = ROOT / "output" / "relevance-eval"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _frozen_stretch(order, shots, directions) -> float:
    """The longest span of timeline in which nothing on screen moves."""

    best = current = 0.0
    for shot_id in order:
        frozen = (
            shots[shot_id]["asset_type"] == "image"
            and directions[shot_id]["motion"] == "static_hold"
        )
        current = current + shots[shot_id]["duration_seconds"] if frozen else 0.0
        best = max(best, current)
    return best


def _longest_run(values):
    best = run = 0
    previous = object()
    for value in values:
        run = run + 1 if value == previous else 1
        previous = value
        best = max(best, run)
    return best


def _slug_bag(url: str | None, fallback: str) -> frozenset[str]:
    """The candidate's words, recovered from its source URL slug.

    An approximation of the metadata bag, used only for the *baseline*, whose
    resolution plan predates persisted families. The new cut reads its real
    ``visual_family`` straight off the plan.
    """

    if not url:
        return frozenset(fallback.lower().split())
    slug = url.rstrip("/").split("/")[-1]
    return frozenset(part for part in slug.split("-") if len(part) >= 3 and not part.isdigit())


def main() -> int:
    shot_plan = _load(NEW / "plan" / "shot-plan.json")
    direction = _load(NEW / "plan" / "visual-direction.json")
    resolution = _load(NEW / "resolved" / "asset-resolution-plan.json")
    events = _load(NEW / "text-events.json")
    overrides = _load(ROOT / "projects" / "desumanizando_01" / "shot-overrides-visual-direction-v1.json")

    base_resolution = _load(BASE / "resolved-relevance" / "asset-resolution-plan.json")
    base_plan = _load(BASE / "render-final" / "edit-plan.json")

    shots = {s["shot_id"]: s for s in shot_plan["shots"]}
    directions = {d["shot_id"]: d for d in direction["directions"]}
    resolved = {r["asset_id"]: r for r in resolution["resolved"]}
    base_resolved = {r["asset_id"]: r for r in base_resolution["resolved"]}

    order = [s["shot_id"] for s in shot_plan["shots"]]
    compositions = [directions[s]["composition"] for s in order]
    motions = [directions[s]["motion"] for s in order]
    grades = [directions[s]["grade"] for s in order]
    motifs = [directions[s]["visual_motif"] for s in order if directions[s]["visual_motif"]]

    # --- families, adjacency ------------------------------------------------ #
    new_families = []
    for shot_id in order:
        asset_id = shots[shot_id]["asset_id"]
        record = resolved.get(asset_id)
        new_families.append((record or {}).get("visual_family") or "other")
    base_families = []
    for shot_id in order:
        asset_id = shots[shot_id]["asset_id"]
        record = base_resolved.get(asset_id)
        if record is None:
            base_families.append(None)
            continue
        base_families.append(
            visual_family(_slug_bag(record["provenance"]["source_url"], record["asset_id"]))
        )

    def _adjacent_repeats(families):
        pairs = [
            (a, b)
            for a, b in zip(families, families[1:])
            if a is not None and b is not None
        ]
        return sum(1 for a, b in pairs if a == b and a != "other"), len(pairs)

    new_repeat, new_pairs = _adjacent_repeats(new_families)
    base_repeat, base_pairs = _adjacent_repeats(base_families)

    # --- how the baseline presented each shot ------------------------------- #
    base_segments = {
        op["operation_id"]: op
        for op in base_plan["operations"]
        if op["kind"] in ("image_clip", "sequence_clip")
    }
    changed_presentation = 0
    for shot_id in order:
        item = directions[shot_id]
        old = base_segments.get(shot_id, {}).get("parameters", {})
        old_composition = old.get("composition", "fullscreen")
        old_motion = old.get("motion")
        moved = old_motion is not None
        if (
            item["composition"] != old_composition
            or (item["motion"] != "static_hold") != moved
            or item["grade"] != "none"
        ):
            changed_presentation += 1

    # --- assets ------------------------------------------------------------- #
    new_paths = [resolved[shots[s]["asset_id"]]["provenance"]["sha256"] for s in order if shots[s]["asset_id"] in resolved]
    base_paths = [
        base_resolved[shots[s]["asset_id"]]["provenance"]["sha256"]
        for s in order
        if shots[s]["asset_id"] in base_resolved
    ]
    changed_assets = sum(
        1
        for shot_id in order
        if shots[shot_id]["asset_id"] in resolved
        and shots[shot_id]["asset_id"] in base_resolved
        and resolved[shots[shot_id]["asset_id"]]["provenance"]["sha256"]
        != base_resolved[shots[shot_id]["asset_id"]]["provenance"]["sha256"]
    )

    rejection_context = Counter()
    for record in resolved.values():
        for reason in record.get("rejection_context", ()):
            rejection_context[reason] += 1

    metrics = {
        "cut": "visual-direction-v1",
        "baseline_render": "output/relevance-eval/render-final/final.mp4",
        "shots": len(order),
        "compositions": dict(sorted(Counter(compositions).items())),
        "longest_same_composition_streak": _longest_run(compositions),
        "motions": dict(sorted(Counter(motions).items())),
        "longest_same_motion_streak": _longest_run(motions),
        # The run limit governs the choices the planner actually makes. Moving
        # footage is held static by a gate, not by a draw, so a run of video
        # shots is not a run of the same effect and is measured separately.
        "longest_same_motion_streak_stills_only": _longest_run(
            [
                directions[s]["motion"]
                for s in order
                if shots[s]["asset_type"] == "image"
            ]
        ),
        "video_shots": sum(1 for s in order if shots[s]["asset_type"] == "video"),
        # The only stillness a viewer can actually feel: consecutive stills
        # that are also held. A run of moving footage is not a frozen frame.
        "longest_frozen_stretch_seconds": round(_frozen_stretch(order, shots, directions), 2),
        "static_shots": sum(1 for m in motions if m == "static_hold"),
        "moving_shots": sum(1 for m in motions if m != "static_hold"),
        "grades": dict(sorted(Counter(grades).items())),
        "emphasis_events": len(events["events"]),
        "emphasis_with_highlight": sum(
            1 for e in events["events"] if e.get("highlight")
        ),
        "visual_motifs": dict(sorted(Counter(motifs).items())),
        "distinct_motifs": len(set(motifs)),
        "shots_with_a_motif": len(motifs),
        "distinct_assets": len(set(new_paths)),
        "repeated_assets": len(new_paths) - len(set(new_paths)),
        "unresolved_requirements": len(resolution["unresolved"]),
        "overrides_used": len(overrides["shots"]),
        "assets_changed_vs_baseline": changed_assets,
        "shots_whose_presentation_changed": changed_presentation,
        "adjacent_same_visual_family": {
            "visual_direction_v1": f"{new_repeat} / {new_pairs}",
            "baseline": f"{base_repeat} / {base_pairs}",
        },
        "winner_rejection_context": dict(sorted(rejection_context.items())),
        "baseline": {
            "shots": len(base_segments),
            "compositions": {"fullscreen": len(base_segments)},
            "motions": dict(
                sorted(
                    Counter(
                        op["parameters"].get("motion") or "static_hold"
                        for op in base_segments.values()
                    ).items()
                )
            ),
            "grades": {"none": len(base_segments)},
            "emphasis_events": 0,
            "distinct_assets": len(set(base_paths)),
            "unresolved_requirements": len(base_resolution["unresolved"]),
        },
    }

    (NEW / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # --- the shot table ------------------------------------------------------ #
    event_by_shot = {e["shot_id"]: e for e in events["events"] if e.get("shot_id")}
    lines = [
        "# Visual Direction v1 — shot by shot",
        "",
        "| shot | role | asset | motif | composition | motion | grade | emphasis | rationale |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for shot_id in order:
        shot = shots[shot_id]
        item = directions[shot_id]
        record = resolved.get(shot["asset_id"])
        if record and record["provenance"]["source_url"]:
            asset = record["provenance"]["source_url"].rstrip("/").split("/")[-1]
        elif record:
            asset = Path(record["local_path"]).name
        else:
            asset = "—"
        event = event_by_shot.get(shot_id)
        emphasis = f"`{event['text']}`" if event else "—"
        lines.append(
            "| "
            + " | ".join(
                (
                    shot_id,
                    shot.get("visual_role") or "—",
                    asset[:46],
                    item["visual_motif"] or "—",
                    item["composition"],
                    item["motion"],
                    item["grade"],
                    emphasis,
                    item["rationale"].replace("|", "/"),
                )
            )
            + " |"
        )
    (NEW / "shot-table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
