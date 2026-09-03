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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from video_generator.adapters.asset_providers import AssetProvider, ProviderError
from video_generator.domain.assets import (
    DEFAULT_SCORING_POLICY,
    AssetProvenance,
    AssetResolutionError,
    AssetResolutionPlan,
    AssetScoringPolicy,
    ResolvedAsset,
    ReuseReview,
    UnresolvedRequirement,
    _iso_now,
    has_semantic_support,
    rank_candidates,
    review_reuse,
    sanitize_query,
    semantic_support,
)
from video_generator.domain.planning import AssetRequirements, ShotPlan

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
) -> str | None:
    """Return None when the file is acceptable, else a short failure reason."""

    if not path.is_file() or path.stat().st_size == 0:
        return "acquired file is missing or empty"
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
    max_acquire_attempts: int = 3,
    probe: Callable[[Path], "tuple[int | None, int | None, float | None]"] | None = None,
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

    for req in revised.requirements:
        sq = sanitize_query(req.query, req.purpose, policy=scoring_policy)
        if not sq.usable:
            unresolved.append(
                UnresolvedRequirement(
                    asset_id=req.asset_id,
                    requirement=req,
                    reason="needs_editorial_override",
                    detail="query has too few meaningful terms after sanitisation",
                    sanitized_query=sq.to_text() or None,
                )
            )
            continue

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

        adjacent = frozenset().union(*recent_bags) if recent_bags else frozenset()
        ranked = rank_candidates(
            req,
            candidates,
            scoring_policy,
            uses_by_candidate=uses_by_candidate,
            adjacent_terms=adjacent,
        )
        if not ranked:
            unresolved.append(
                UnresolvedRequirement(
                    asset_id=req.asset_id,
                    requirement=req,
                    reason="no_candidates" if not candidates else "no_compatible_candidate",
                    detail=(
                        "no provider returned a candidate"
                        if not candidates
                        else "every candidate was disqualified on type / duration / resolution"
                    ),
                    sanitized_query=sq.to_text() or None,
                )
            )
            continue

        # Structural fit (type / orientation / resolution / duration) is not a
        # match: a candidate only earns a slot when it shares a real visual term
        # with the shot. Anything below the floor needs an editorial override.
        eligible = [c for c in ranked if has_semantic_support(c, scoring_policy)]
        if not eligible:
            best = semantic_support(ranked[0])
            unresolved.append(
                UnresolvedRequirement(
                    asset_id=req.asset_id,
                    requirement=req,
                    reason="no_semantic_match",
                    detail=(
                        "best candidate matched only on type / orientation / "
                        f"resolution (semantic score {best:.2f} <= floor "
                        f"{scoring_policy.min_semantic_score:.2f}); needs an "
                        "editorial visual query"
                    ),
                    sanitized_query=sq.to_text() or None,
                )
            )
            continue
        ranked = eligible

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
            )
            uses_by_candidate[candidate.candidate_id] = (
                uses_by_candidate.get(candidate.candidate_id, 0) + 1
            )
            recent_bags.append(candidate.metadata_bag())
            del recent_bags[:-_ADJACENCY_WINDOW]
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
                    sanitized_query=sq.to_text() or None,
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
    )
