"""Impure orchestration for the Asset Resolver: search -> rank -> select ->
acquire -> hash -> validate -> provenance -> AssetResolutionPlan.

This module may touch the filesystem (copy / stage / hash / probe) and, through
its providers, the network. The pure decision logic lives in
:mod:`video_generator.domain.assets`; the acquisition boundary lives in
:mod:`video_generator.adapters.asset_providers`.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from video_generator.adapters.asset_providers import AssetProvider, ProviderError
from video_generator.domain.assets import (
    DEFAULT_SCORING_POLICY,
    SanitizedQuery,
    AssetProvenance,
    AssetResolutionError,
    AssetResolutionPlan,
    AssetScoringPolicy,
    ResolvedAsset,
    ReuseReview,
    UnresolvedRequirement,
    _iso_now,
    has_semantic_support,
    rank_candidates_with_report,
    review_reuse,
    sanitize_query,
    semantic_support,
)
from video_generator.domain.planning import AssetRequirements, ShotPlan
from video_generator.domain.relevance import visual_family

_IMAGE_FALLBACK_EXT = ".jpg"
_VIDEO_FALLBACK_EXT = ".mp4"
_ADJACENCY_WINDOW = 2


class ResolveError(RuntimeError):
    """Raised on an unrecoverable resolution failure (never on partial cover)."""


@dataclass(frozen=True, slots=True)
class ResolveResult:
    plan: AssetResolutionPlan
    revised_requirements: AssetRequirements
    review: ReuseReview | None
    provider_stats: Mapping[str, int] = field(default_factory=dict)
    # How many candidates each Semantic Visual Relevance rule refused across
    # the whole run. Empty when no requirement carried a relevance reading.
    rejection_counts: Mapping[str, int] = field(default_factory=dict)


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(asset_id: str) -> str:
    """A filesystem-safe stem for an ``asset_id`` so a crafted requirements file
    cannot write outside ``out_dir`` via ``../`` or absolute segments."""

    cleaned = _UNSAFE_NAME.sub("_", asset_id).strip("._")
    return cleaned or "asset"


def _ext_for(candidate, media_type: str) -> str:
    for locator in (candidate.local_path, candidate.remote_locator):
        if not locator:
            continue
        suffix = Path(locator.split("?")[0]).suffix.lower()
        if suffix and len(suffix) <= 6:
            return suffix
    return _IMAGE_FALLBACK_EXT if media_type == "image" else _VIDEO_FALLBACK_EXT


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_acquired(
    path: Path,
    *,
    media_type: str,
    duration_needed: float,
    policy: AssetScoringPolicy,
    probe: Callable[[Path], "tuple[int | None, int | None, float | None]"] | None,
    luma: "Callable[[Path], float | None] | None" = None,
) -> str | None:
    """Return None when the file is acceptable, else a short failure reason."""

    if not path.is_file() or path.stat().st_size == 0:
        return "acquired file is missing or empty"
    if luma is not None and policy.max_mean_luma is not None:
        # The one property no provider publishes and no lexicon can infer.
        # Measured here rather than at ranking time because it needs the file,
        # and here is the first moment the file exists.
        measured = luma(path)
        if measured is not None and measured > policy.max_mean_luma:
            return (
                f"mean brightness {measured:.2f} is above the channel ceiling "
                f"{policy.max_mean_luma:.2f}"
            )
    if probe is None:
        return None
    width, height, duration = probe(path)
    if media_type == "video":
        if duration is None:
            return "could not read a duration for a video asset"
        if duration + 1e-6 < duration_needed:
            return f"video is {duration:.2f}s, shorter than the needed {duration_needed:.2f}s"
    if width and height:
        long_edge, short_edge = max(width, height), min(width, height)
        if (
            policy.disqualify_below_resolution
            and (long_edge < policy.min_long_edge or short_edge < policy.min_short_edge)
        ):
            return f"resolution {width}x{height} is below the policy floor"
    return None


def resolve_assets(
    shot_plan: ShotPlan,
    asset_requirements: AssetRequirements,
    providers: Sequence[AssetProvider],
    *,
    out_dir: Path | str,
    scoring_policy: AssetScoringPolicy = DEFAULT_SCORING_POLICY,
    shot_context: Mapping[str, Mapping[str, str]] | None = None,
    do_review_reuse: bool = True,
    clock: Callable[[], str] = _iso_now,
    max_candidates_per_requirement: int = 24,
    # Six rather than three: with a brightness ceiling in play, the top of the
    # ranking can be several bright frames deep before a usable one appears,
    # and a download only happens when the candidates above it were refused.
    max_acquire_attempts: int = 6,
    probe: Callable[[Path], "tuple[int | None, int | None, float | None]"] | None = None,
    luma: "Callable[[Path], float | None] | None" = None,
) -> ResolveResult:
    if not isinstance(asset_requirements, AssetRequirements):
        raise ResolveError("asset_requirements must be an AssetRequirements")
    if not providers:
        raise ResolveError("at least one provider is required")

    out_dir = Path(out_dir)
    files_dir = out_dir / "files"
    staging_dir = out_dir / ".staging"

    review: ReuseReview | None = None
    if do_review_reuse and shot_context:
        review = review_reuse(asset_requirements, shot_context, scoring_policy)
        revised = review.as_requirements(asset_requirements)
    else:
        revised = asset_requirements

    providers_by_kind: dict[str, AssetProvider] = {}
    for provider in providers:
        providers_by_kind.setdefault(provider.source_kind, provider)

    resolved: list[ResolvedAsset] = []
    unresolved: list[UnresolvedRequirement] = []
    provider_stats: dict[str, int] = {}
    uses_by_candidate: dict[str, int] = {}
    recent_bags: list[frozenset[str]] = []
    # The visual families of the last few chosen assets, so Semantic Visual
    # Relevance can see the same look coming back three shots running — which
    # no per-candidate score can notice on its own.
    recent_families: list[str] = []
    rejection_counts: dict[str, int] = {}

    for req in revised.requirements:
        adjacent = frozenset().union(*recent_bags) if recent_bags else frozenset()
        families = tuple(recent_families[-_ADJACENCY_WINDOW:])

        # A requirement may offer several ways of asking for the same idea. Try
        # them best-first and keep the first that finds something the shot
        # actually shares meaning with; a picture of an exam beats a picture of
        # the person the exam happened to, and the fallbacks are what make that
        # reachable without a human rewriting the query.
        attempts: list[tuple[str, "SanitizedQuery", list, tuple]] = []
        failure: UnresolvedRequirement | None = None
        ranked: list = []
        verdicts: tuple = ()
        chosen_query: str | None = None
        chosen_sq = None
        for query in req.search_queries():
            # A frequency-derived query is thin, so it is enriched with the
            # shot's purpose. A requirement that states its own candidate
            # queries has already been enriched — folding the purpose in there
            # too would dilute a deliberate visual phrase with the narration's
            # own vocabulary, which is how a search stops meaning anything.
            context = "" if req.queries else req.purpose
            sq = sanitize_query(query, context, policy=scoring_policy)
            if not sq.usable:
                attempts.append((query, sq, [], ()))
                continue
            attempt_req = replace(req, query=query)
            raw_candidates = []
            for provider in providers:
                try:
                    raw_candidates.extend(
                        provider.search(
                            sq.terms,
                            media_type=req.type,
                            orientation=req.orientation,
                            min_duration_seconds=req.duration_needed_seconds,
                            limit=max_candidates_per_requirement,
                        )
                    )
                except ProviderError:
                    continue
            # de-dup identical candidate ids, keep first occurrence
            seen_ids: set[str] = set()
            candidates = []
            for candidate in raw_candidates:
                if candidate.candidate_id in seen_ids:
                    continue
                seen_ids.add(candidate.candidate_id)
                candidates.append(candidate)

            scored, attempt_verdicts = rank_candidates_with_report(
                attempt_req,
                candidates,
                scoring_policy,
                uses_by_candidate=uses_by_candidate,
                adjacent_terms=adjacent,
                recent_families=families,
            )
            verdicts = attempt_verdicts
            attempts.append((query, sq, candidates, attempt_verdicts))
            # Structural fit (type / orientation / resolution / duration) is
            # not a match: a candidate only earns a slot when it shares a real
            # visual term with the shot.
            eligible = [c for c in scored if has_semantic_support(c, scoring_policy)]
            if eligible:
                ranked = eligible
                chosen_query = query
                chosen_sq = sq
                break

        if not ranked:
            # report against the best attempt: the furthest one got
            usable = [row for row in attempts if row[1].usable]
            if not usable:
                first_sq = attempts[0][1] if attempts else None
                failure = UnresolvedRequirement(
                    asset_id=req.asset_id,
                    requirement=req,
                    reason="needs_editorial_override",
                    detail="query has too few meaningful terms after sanitisation",
                    sanitized_query=(first_sq.to_text() if first_sq else None) or None,
                )
            else:
                query, sq, candidates, verdicts = max(usable, key=lambda row: len(row[2]))
                if not candidates:
                    failure = UnresolvedRequirement(
                        asset_id=req.asset_id,
                        requirement=req,
                        reason="no_candidates",
                        detail=(
                            "no provider returned a candidate for any of the "
                            f"{len(usable)} candidate queries"
                        ),
                        sanitized_query=sq.to_text() or None,
                    )
                else:
                    rejected = [v for v in verdicts if v.rejection_reasons]
                    if rejected and len(rejected) == len(verdicts):
                        # every candidate was refused on editorial grounds, not
                        # on vocabulary: say so, because the fix is a different
                        # picture, not a different query
                        seen: dict[str, None] = {}
                        for verdict in rejected:
                            for reason in verdict.rejection_reasons:
                                seen.setdefault(reason, None)
                        failure = UnresolvedRequirement(
                            asset_id=req.asset_id,
                            requirement=req,
                            reason="editorially_rejected",
                            detail=(
                                f"all {len(rejected)} candidates were refused by "
                                "Semantic Visual Relevance: " + ", ".join(seen)
                            ),
                            sanitized_query=sq.to_text() or None,
                        )
                    else:
                        failure = UnresolvedRequirement(
                            asset_id=req.asset_id,
                            requirement=req,
                            reason="no_semantic_match",
                            detail=(
                                "no candidate query found a candidate sharing a "
                                "visual term with the shot; needs an editorial "
                                "visual query"
                            ),
                            sanitized_query=sq.to_text() or None,
                        )
            for verdict in verdicts:
                for reason in verdict.rejection_reasons:
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            unresolved.append(failure)
            continue

        for verdict in verdicts:
            for reason in verdict.rejection_reasons:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        # What the winner beat, kept so a pick can be explained later without
        # re-running the search: how many candidates were on the table and on
        # what editorial grounds the others were refused.
        refused = [v for v in verdicts if v.rejection_reasons]
        context: dict[str, None] = {}
        for verdict in refused:
            for reason in verdict.rejection_reasons:
                context.setdefault(reason, None)

        chosen_resolved: ResolvedAsset | None = None
        last_failure = ""
        for candidate in ranked[:max_acquire_attempts]:
            provider = providers_by_kind.get(candidate.source_kind)
            if provider is None:
                last_failure = f"no provider for source_kind {candidate.source_kind}"
                continue
            ext = _ext_for(candidate, req.type)
            stem = _safe_name(req.asset_id)
            final_path = files_dir / f"{stem}{ext}"
            stage_path = staging_dir / f"{stem}{ext}"
            if stage_path.exists():
                stage_path.unlink()
            try:
                acquired = provider.acquire(candidate, stage_path)
            except (ProviderError, OSError) as exc:
                last_failure = f"acquire failed: {exc}"
                continue

            failure = _validate_acquired(
                Path(acquired.local_path),
                media_type=req.type,
                duration_needed=req.duration_needed_seconds,
                policy=scoring_policy,
                probe=probe,
                luma=luma,
            )
            if failure:
                last_failure = failure
                Path(acquired.local_path).unlink(missing_ok=True)
                continue

            staged_hash = _sha256_of(Path(acquired.local_path))
            if final_path.exists():
                if _sha256_of(final_path) != staged_hash:
                    raise ResolveError(
                        f"{final_path} already exists with different bytes; refusing to "
                        "overwrite an existing asset"
                    )
                Path(acquired.local_path).unlink(missing_ok=True)
            else:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(acquired.local_path), str(final_path))

            provenance = AssetProvenance(
                asset_id=req.asset_id,
                candidate_id=candidate.candidate_id,
                source_kind=candidate.source_kind,
                source_url=candidate.source_url,
                author=candidate.author,
                license=candidate.license,
                license_url=candidate.license_url,
                acquired_at=clock(),
                original_filename=acquired.original_filename,
                local_path=str(final_path),
                sha256=staged_hash,
            )
            chosen_resolved = ResolvedAsset(
                asset_id=req.asset_id,
                local_path=str(final_path),
                candidate_id=candidate.candidate_id,
                requirement=req,
                score=candidate.score,
                provenance=provenance,
                matched_query=chosen_query,
                sanitized_query=(chosen_sq.to_text() if chosen_sq else None) or None,
                score_breakdown=dict(candidate.score_breakdown),
                visual_family=visual_family(candidate.metadata_bag()),
                candidates_considered=len(verdicts),
                rejected_candidates=len(refused),
                rejection_context=tuple(context),
            )
            uses_by_candidate[candidate.candidate_id] = (
                uses_by_candidate.get(candidate.candidate_id, 0) + 1
            )
            recent_bags.append(candidate.metadata_bag())
            del recent_bags[:-_ADJACENCY_WINDOW]
            recent_families.append(visual_family(candidate.metadata_bag()))
            del recent_families[:-_ADJACENCY_WINDOW]
            provider_stats[candidate.source_kind] = (
                provider_stats.get(candidate.source_kind, 0) + 1
            )
            break

        if chosen_resolved is None:
            unresolved.append(
                UnresolvedRequirement(
                    asset_id=req.asset_id,
                    requirement=req,
                    reason="below_quality_floor",
                    detail=last_failure or "no candidate could be acquired and validated",
                    sanitized_query=(chosen_sq.to_text() if chosen_sq else None) or None,
                )
            )
            continue
        resolved.append(chosen_resolved)

    if staging_dir.is_dir() and not any(staging_dir.iterdir()):
        staging_dir.rmdir()

    plan = AssetResolutionPlan(
        plan_id=f"{revised.script_id}-asset-resolution-plan",
        shot_plan_id=revised.shot_plan_id,
        script_id=revised.script_id,
        resolved=tuple(resolved),
        unresolved=tuple(unresolved),
    )
    plan.validate_against(revised)
    return ResolveResult(
        plan=plan,
        revised_requirements=revised,
        review=review,
        provider_stats=provider_stats,
        rejection_counts=dict(sorted(rejection_counts.items())),
    )
