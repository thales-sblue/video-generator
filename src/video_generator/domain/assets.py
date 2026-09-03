"""Asset Resolver: a pure, deterministic layer that turns ``AssetRequirements``
into concrete, semantically-reasonable, legally-traceable asset choices.

``AssetRequirement -> candidates -> ranking -> chosen asset -> local file +
provenance -> asset-bindings.json``

Stdlib only. No I/O, no network, no adapters. This module answers *which*
concrete file should fill a shot and *why*; acquisition (copy / download,
hashing, probing) belongs to :mod:`video_generator.adapters.asset_providers`
and :mod:`video_generator.resolve`, which may touch the filesystem and the
network behind an explicit, opt-in boundary.

Dependencies point inward: this module imports from
:mod:`video_generator.domain.planning` (``AssetRequirement`` /
``AssetRequirements``); nothing in ``planning`` imports back.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from video_generator.domain.planning import AssetRequirement, AssetRequirements

SCHEMA_VERSION = 1

MEDIA_TYPES = ("image", "video")
ORIENTATIONS = ("landscape", "portrait", "square")
UNRESOLVED_REASONS = (
    "no_candidates",
    "no_compatible_candidate",
    "needs_editorial_override",
    "acquisition_failed",
    "below_quality_floor",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class AssetResolutionError(ValueError):
    """Raised when resolution data violates a contract or a cross-document rule."""


# --------------------------------------------------------------------------- #
# shared validation helpers (same shape as planning.py / models.py, kept local
# so the module stays self-contained and raises AssetResolutionError)
# --------------------------------------------------------------------------- #
def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetResolutionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _str_or_empty(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AssetResolutionError(f"{field_name} must be a string")
    return value.strip()


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise AssetResolutionError("contract payload must be an object")
    present = set(data)
    missing = required - present
    unknown = present - required - optional
    if missing:
        raise AssetResolutionError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise AssetResolutionError(f"unknown fields: {', '.join(sorted(unknown))}")


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssetResolutionError(f"{field_name} must be a finite number")
    return float(value)


def _non_negative(value: object, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0:
        raise AssetResolutionError(f"{field_name} must be >= 0")
    return number


def _positive(value: object, field_name: str) -> float:
    number = _number(value, field_name)
    if number <= 0:
        raise AssetResolutionError(f"{field_name} must be > 0")
    return number


def _int(value: object, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AssetResolutionError(f"{field_name} must be an integer >= {minimum}")
    return value


def _optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AssetResolutionError(f"{field_name} must be a positive integer or null")
    return value


def _optional_positive_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _positive(value, field_name)


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AssetResolutionError(f"{field_name} must be a boolean")
    return value


def _json_safe_number_map(value: object, field_name: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise AssetResolutionError(f"{field_name} must be an object")
    out: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise AssetResolutionError(f"{field_name} keys must be non-empty strings")
        out[key] = _number(item, f"{field_name}[{key}]")
    return out


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise AssetResolutionError(f"{field_name} must be an array of strings")
    out = tuple(_text(item, f"{field_name} entry") for item in value)
    if len(set(out)) != len(out):
        raise AssetResolutionError(f"{field_name} entries must be unique")
    return out


def _to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# lexical core — deterministic, PT-BR/EN aware, no NLP infrastructure
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")

# Closed-class words that never carry a visual concept (superset of the
# planning module's list; a deliberate small duplicate — a domain module must
# not import a sibling's private name, and unifying them is a separate refactor).
_STOPWORDS = frozenset(
    """
    a o e as os um uma uns umas de do da dos das no na nos nas em para por com
    que se ao aos por sua seu suas seus meu minha nossa nosso isso isto aquilo
    ele ela eles elas nos voce voces eu me te lhe mais menos muito pouco pra
    ja nao sim tambem como quando onde porque pois mas ou entao talvez sobre
    ser estar ter haver fazer poder ir vir dar ver ate entre sem the of to in
    on and or for with that as at by is are be this it an
    """.split()
)

# Words from the shot-type / scale vocabulary — structural, never a concept.
_STRUCTURAL_WORDS = frozenset(
    """
    wide medium close detail establishing insert symbolic screen interface
    document photography object environment human roll broll graphic simple text
    mostrar beat cena plano shot take
    """.split()
)

# Meta words that read like a concept but are useless as a visual query.
_GENERIC_WORDS = frozenset(
    """
    outras outra outros outro palavra palavras coisa coisas ideia ideias algo
    alguma alguns algumas tema temas assunto assuntos conceito conceitos
    exemplo exemplos geral gerais varios varias diverso diversos etc parte
    partes aspecto aspectos ponto pontos forma formas modo modos maneira
    """.split()
)


def _raw_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD.finditer(text or "")]


def _content_tokens(text: str, *, drop_generic: bool) -> tuple[str, ...]:
    """Ordered, de-duplicated content tokens: length >= 4, not a stopword and
    not a structural (shot-type/scale) word. Generic meta words are dropped for
    query terms and kept for candidate metadata bags."""

    seen: dict[str, None] = {}
    for token in _raw_tokens(text):
        if len(token) < 4 or token in _STOPWORDS or token in _STRUCTURAL_WORDS:
            continue
        if drop_generic and token in _GENERIC_WORDS:
            continue
        seen.setdefault(token, None)
    return tuple(seen)


def _bag_tokens(*texts: str) -> frozenset[str]:
    out: set[str] = set()
    for text in texts:
        for token in _raw_tokens(text):
            if len(token) >= 3 and token not in _STOPWORDS:
                out.add(token)
    return frozenset(out)


_INTENT_PREFIX = re.compile(r"^\s*mostrar:\s*", re.IGNORECASE)
_BEAT_SUFFIX = re.compile(r"\s*[—-]\s*beat\s+\d+\s*/\s*\d+\s*$", re.IGNORECASE)


def _intent_of(purpose: str) -> str:
    """Strip the derived ``mostrar: … — beat i/n`` scaffolding from a purpose
    string, leaving the visual-intent phrase."""

    text = _BEAT_SUFFIX.sub("", purpose or "")
    text = _INTENT_PREFIX.sub("", text)
    return text.replace("/", " ").strip()


@dataclass(frozen=True, slots=True)
class SanitizedQuery:
    """The deterministic, lexical clean-up of a shot's visual query."""

    terms: tuple[str, ...]
    usable: bool
    reason: str | None = None

    def to_text(self) -> str:
        return " ".join(self.terms)


def sanitize_query(
    query: str,
    purpose: str = "",
    visual_intent: str = "",
    policy: "AssetScoringPolicy | None" = None,
) -> SanitizedQuery:
    """Turn a raw shot query plus its purpose / visual intent into a small,
    inspectable set of content terms. If too few survive, the requirement needs
    an editorial override rather than a search for noise."""

    policy = policy or DEFAULT_SCORING_POLICY
    ordered: dict[str, None] = {}
    for source in (query, _intent_of(purpose), visual_intent):
        for token in _content_tokens(source, drop_generic=True):
            ordered.setdefault(token, None)
    terms = tuple(ordered)
    usable = len(terms) >= policy.min_meaningful_query_terms
    return SanitizedQuery(
        terms=terms,
        usable=usable,
        reason=None if usable else "needs_editorial_override",
    )


# --------------------------------------------------------------------------- #
# scoring policy — weights are configuration, not scattered magic numbers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AssetScoringPolicy:
    weight_query_match: float = 6.0
    weight_purpose_match: float = 3.0
    weight_intent_match: float = 3.0
    weight_type_match: float = 4.0
    weight_orientation_match: float = 2.0
    weight_resolution: float = 2.0
    weight_duration: float = 2.0
    reuse_repetition_penalty: float = 1.5
    adjacent_similarity_penalty: float = 2.0
    min_long_edge: int = 1280
    min_short_edge: int = 720
    disqualify_below_resolution: bool = False
    require_orientation_match: bool = False
    min_meaningful_query_terms: int = 2
    reuse_semantic_min_shared_terms: int = 2
    video_duration_headroom_seconds: float = 0.5

    _FLOAT_FIELDS = (
        "weight_query_match",
        "weight_purpose_match",
        "weight_intent_match",
        "weight_type_match",
        "weight_orientation_match",
        "weight_resolution",
        "weight_duration",
        "reuse_repetition_penalty",
        "adjacent_similarity_penalty",
        "video_duration_headroom_seconds",
    )
    _INT_FIELDS = (
        ("min_long_edge", 1),
        ("min_short_edge", 1),
        ("min_meaningful_query_terms", 1),
        ("reuse_semantic_min_shared_terms", 1),
    )
    _BOOL_FIELDS = ("disqualify_below_resolution", "require_orientation_match")

    def __post_init__(self) -> None:
        for name in self._FLOAT_FIELDS:
            _non_negative(getattr(self, name), name)
        for name, minimum in self._INT_FIELDS:
            _int(getattr(self, name), name, minimum=minimum)
        for name in self._BOOL_FIELDS:
            _bool(getattr(self, name), name)
        if self.min_short_edge > self.min_long_edge:
            raise AssetResolutionError("min_short_edge must not exceed min_long_edge")

    def to_dict(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in self._FLOAT_FIELDS}
        payload.update({name: getattr(self, name) for name, _ in self._INT_FIELDS})
        payload.update({name: getattr(self, name) for name in self._BOOL_FIELDS})
        return payload

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetScoringPolicy":
        if not isinstance(data, Mapping):
            raise AssetResolutionError("policy payload must be an object")
        allowed = set(cls().to_dict())
        unknown = set(data) - allowed
        if unknown:
            raise AssetResolutionError(
                f"unknown policy fields: {', '.join(sorted(unknown))}"
            )
        merged = cls().to_dict()
        merged.update(data)
        return cls(**merged)


DEFAULT_SCORING_POLICY = AssetScoringPolicy()


# --------------------------------------------------------------------------- #
# AssetCandidate
# --------------------------------------------------------------------------- #
def _orientation_of(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


@dataclass(frozen=True, slots=True)
class AssetCandidate:
    candidate_id: str
    source_kind: str
    source_id: str
    media_type: str
    local_path: str | None
    remote_locator: str | None
    title: str
    description: str
    tags: tuple[str, ...]
    width: int | None
    height: int | None
    duration_seconds: float | None
    license: str
    license_url: str | None
    author: str | None
    source_url: str | None
    score: float
    score_breakdown: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source_kind", _text(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id"))
        if self.media_type not in MEDIA_TYPES:
            raise AssetResolutionError("media_type must be image or video")
        object.__setattr__(self, "local_path", _optional_text(self.local_path, "local_path"))
        object.__setattr__(
            self, "remote_locator", _optional_text(self.remote_locator, "remote_locator")
        )
        if self.local_path is None and self.remote_locator is None:
            raise AssetResolutionError(
                "a candidate needs either local_path or remote_locator"
            )
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "description", _str_or_empty(self.description, "description"))
        object.__setattr__(self, "tags", _string_tuple(self.tags, "tags"))
        object.__setattr__(self, "width", _optional_positive_int(self.width, "width"))
        object.__setattr__(self, "height", _optional_positive_int(self.height, "height"))
        object.__setattr__(
            self,
            "duration_seconds",
            _optional_positive_number(self.duration_seconds, "duration_seconds"),
        )
        object.__setattr__(self, "license", _text(self.license, "license"))
        object.__setattr__(self, "license_url", _optional_text(self.license_url, "license_url"))
        object.__setattr__(self, "author", _optional_text(self.author, "author"))
        object.__setattr__(self, "source_url", _optional_text(self.source_url, "source_url"))
        object.__setattr__(self, "score", _number(self.score, "score"))
        object.__setattr__(
            self,
            "score_breakdown",
            MappingProxyType(_json_safe_number_map(self.score_breakdown, "score_breakdown")),
        )

    @property
    def orientation(self) -> str | None:
        return _orientation_of(self.width, self.height)

    def metadata_bag(self) -> frozenset[str]:
        return _bag_tokens(self.title, self.description, " ".join(self.tags))

    def with_score(self, breakdown: "ScoreBreakdown") -> "AssetCandidate":
        return AssetCandidate(
            **{**self.to_dict(), "score": breakdown.total,
               "score_breakdown": dict(breakdown.components)}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "media_type": self.media_type,
            "local_path": self.local_path,
            "remote_locator": self.remote_locator,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "width": self.width,
            "height": self.height,
            "duration_seconds": self.duration_seconds,
            "license": self.license,
            "license_url": self.license_url,
            "author": self.author,
            "source_url": self.source_url,
            "score": self.score,
            "score_breakdown": dict(self.score_breakdown),
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetCandidate":
        _keys(
            data,
            required={
                "candidate_id", "source_kind", "source_id", "media_type",
                "local_path", "remote_locator", "title", "description", "tags",
                "width", "height", "duration_seconds", "license", "license_url",
                "author", "source_url", "score", "score_breakdown",
            },
            optional=set(),
        )
        return cls(
            candidate_id=data["candidate_id"],
            source_kind=data["source_kind"],
            source_id=data["source_id"],
            media_type=data["media_type"],
            local_path=data["local_path"],
            remote_locator=data["remote_locator"],
            title=data["title"],
            description=data["description"],
            tags=tuple(data["tags"]),
            width=data["width"],
            height=data["height"],
            duration_seconds=data["duration_seconds"],
            license=data["license"],
            license_url=data["license_url"],
            author=data["author"],
            source_url=data["source_url"],
            score=data["score"],
            score_breakdown=data["score_breakdown"],
        )


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    components: Mapping[str, float]
    disqualified: bool
    disqualified_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "components", MappingProxyType(dict(self.components))
        )
        object.__setattr__(
            self, "disqualified_reasons", tuple(self.disqualified_reasons)
        )

    @property
    def total(self) -> float:
        return sum(self.components.values())


def _overlap_score(query_terms: tuple[str, ...], bag: frozenset[str], weight: float) -> float:
    if not query_terms or weight == 0.0:
        return 0.0
    shared = sum(1 for term in query_terms if term in bag)
    return weight * shared / len(query_terms)


def score_candidate(
    requirement: AssetRequirement,
    candidate: AssetCandidate,
    policy: AssetScoringPolicy = DEFAULT_SCORING_POLICY,
    *,
    uses: int = 1,
    adjacent_terms: frozenset[str] = frozenset(),
) -> ScoreBreakdown:
    """Score one candidate against one requirement, every term inspectable."""

    reasons: list[str] = []

    if candidate.media_type != requirement.type:
        reasons.append("media_type")

    if requirement.type == "video":
        needed = requirement.duration_needed_seconds + policy.video_duration_headroom_seconds
        if candidate.duration_seconds is None or candidate.duration_seconds + 1e-9 < needed:
            reasons.append("duration")

    long_edge = max(candidate.width or 0, candidate.height or 0)
    short_edge = min(candidate.width or 0, candidate.height or 0)
    dims_known = bool(candidate.width and candidate.height)
    below_res = dims_known and (
        long_edge < policy.min_long_edge or short_edge < policy.min_short_edge
    )
    if below_res and policy.disqualify_below_resolution:
        reasons.append("resolution")

    cand_orientation = candidate.orientation
    orientation_mismatch = (
        cand_orientation is not None and cand_orientation != requirement.orientation
    )
    if orientation_mismatch and policy.require_orientation_match:
        reasons.append("orientation")

    bag = candidate.metadata_bag()
    query_terms = sanitize_query(requirement.query, requirement.purpose, policy=policy).terms
    purpose_terms = _content_tokens(_intent_of(requirement.purpose), drop_generic=True)
    intent_terms = _content_tokens(_intent_of(requirement.purpose), drop_generic=False)

    components: dict[str, float] = {
        "query_match": _overlap_score(query_terms, bag, policy.weight_query_match),
        "purpose_match": _overlap_score(purpose_terms, bag, policy.weight_purpose_match),
        "intent_match": _overlap_score(intent_terms, bag, policy.weight_intent_match),
        "type_match": policy.weight_type_match if candidate.media_type == requirement.type else 0.0,
    }

    if cand_orientation is None:
        components["orientation_match"] = 0.0
    elif orientation_mismatch:
        components["orientation_match"] = -policy.weight_orientation_match
    else:
        components["orientation_match"] = policy.weight_orientation_match

    if not dims_known:
        components["resolution"] = 0.0
    elif below_res:
        components["resolution"] = -0.5 * policy.weight_resolution
    else:
        components["resolution"] = policy.weight_resolution

    if requirement.type == "video" and candidate.duration_seconds is not None:
        needed = max(requirement.duration_needed_seconds, 1e-6)
        headroom_ratio = max(0.0, min(1.0, (candidate.duration_seconds - needed) / needed))
        components["duration"] = policy.weight_duration * headroom_ratio
    else:
        components["duration"] = 0.0

    components["repetition_penalty"] = -policy.reuse_repetition_penalty * max(0, uses - 1)

    if adjacent_terms and bag:
        shared_adj = len(bag & adjacent_terms)
        ratio = shared_adj / len(bag)
        components["adjacent_penalty"] = -policy.adjacent_similarity_penalty * ratio
    else:
        components["adjacent_penalty"] = 0.0

    return ScoreBreakdown(
        components=components,
        disqualified=bool(reasons),
        disqualified_reasons=tuple(reasons),
    )


def rank_candidates(
    requirement: AssetRequirement,
    candidates: "list[AssetCandidate] | tuple[AssetCandidate, ...]",
    policy: AssetScoringPolicy = DEFAULT_SCORING_POLICY,
    *,
    uses_by_candidate: Mapping[str, int] | None = None,
    adjacent_terms: frozenset[str] = frozenset(),
) -> list[AssetCandidate]:
    """Score, drop disqualified, and return the survivors ordered by descending
    total with a ``candidate_id`` tie-break so the ranking is deterministic."""

    uses_by_candidate = uses_by_candidate or {}
    scored: list[tuple[float, str, AssetCandidate]] = []
    for candidate in candidates:
        breakdown = score_candidate(
            requirement,
            candidate,
            policy,
            uses=uses_by_candidate.get(candidate.candidate_id, 1),
            adjacent_terms=adjacent_terms,
        )
        if breakdown.disqualified:
            continue
        ranked_candidate = candidate.with_score(breakdown)
        scored.append((ranked_candidate.score, ranked_candidate.candidate_id, ranked_candidate))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored]


# --------------------------------------------------------------------------- #
# semantic reuse review — break structurally-valid but meaning-thin reuses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ReuseReview:
    requirements: tuple[AssetRequirement, ...]
    split_count: int
    splits: tuple[tuple[str, str], ...]  # (original asset_id, peeled shot_id)

    def as_requirements(self, template: AssetRequirements) -> AssetRequirements:
        return AssetRequirements(
            plan_id=template.plan_id,
            shot_plan_id=template.shot_plan_id,
            script_id=template.script_id,
            orientation=template.orientation,
            requirements=self.requirements,
        )


def _shot_terms(context: Mapping[str, Any], shot_id: str) -> frozenset[str]:
    entry = context.get(shot_id, {}) if isinstance(context, Mapping) else {}
    query = str(entry.get("visual_query", "")) if isinstance(entry, Mapping) else ""
    purpose = str(entry.get("purpose", "")) if isinstance(entry, Mapping) else ""
    intent = str(entry.get("visual_intent", "")) if isinstance(entry, Mapping) else ""
    tokens: set[str] = set()
    tokens.update(_content_tokens(query, drop_generic=True))
    tokens.update(_content_tokens(_intent_of(purpose), drop_generic=True))
    tokens.update(_content_tokens(intent, drop_generic=True))
    return frozenset(tokens)


def review_reuse(
    asset_requirements: AssetRequirements,
    shot_context: Mapping[str, Mapping[str, str]],
    policy: AssetScoringPolicy = DEFAULT_SCORING_POLICY,
) -> ReuseReview:
    """Re-partition every multi-shot requirement: a shot only keeps sharing an
    asset when its own visual query / purpose / intent overlaps the anchor
    shot's by at least ``policy.reuse_semantic_min_shared_terms`` content terms.
    Incompatible shots are peeled into their own requirement so a distinct file
    is materialised for them."""

    revised: list[AssetRequirement] = []
    splits: list[tuple[str, str]] = []
    for req in asset_requirements.requirements:
        if len(req.used_by) <= 1:
            revised.append(req)
            continue
        anchor = req.used_by[0]
        anchor_terms = _shot_terms(shot_context, anchor)
        if not anchor_terms:
            anchor_terms = frozenset(
                _content_tokens(req.query, drop_generic=True)
            ) | frozenset(_content_tokens(_intent_of(req.purpose), drop_generic=True))
        kept: list[str] = [anchor]
        peeled: list[str] = []
        for member in req.used_by[1:]:
            shared = anchor_terms & _shot_terms(shot_context, member)
            if len(shared) >= policy.reuse_semantic_min_shared_terms:
                kept.append(member)
            else:
                peeled.append(member)
        revised.append(
            AssetRequirement(
                asset_id=req.asset_id,
                type=req.type,
                query=req.query,
                duration_needed_seconds=req.duration_needed_seconds,
                orientation=req.orientation,
                purpose=req.purpose,
                used_by=tuple(kept),
                min_count=req.min_count,
                notes=req.notes,
            )
        )
        for member in peeled:
            entry = shot_context.get(member, {}) if isinstance(shot_context, Mapping) else {}
            entry = entry if isinstance(entry, Mapping) else {}
            revised.append(
                AssetRequirement(
                    asset_id=f"{req.asset_id}__solo_{member}",
                    type=req.type,
                    query=str(entry.get("visual_query") or req.query),
                    duration_needed_seconds=req.duration_needed_seconds,
                    orientation=req.orientation,
                    purpose=str(entry.get("purpose") or req.purpose),
                    used_by=(member,),
                )
            )
            splits.append((req.asset_id, member))
    return ReuseReview(
        requirements=tuple(revised),
        split_count=len(splits),
        splits=tuple(splits),
    )


# --------------------------------------------------------------------------- #
# AssetProvenance
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AssetProvenance:
    asset_id: str
    candidate_id: str
    source_kind: str
    source_url: str | None
    author: str | None
    license: str
    license_url: str | None
    acquired_at: str
    original_filename: str
    local_path: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source_kind", _text(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_url", _optional_text(self.source_url, "source_url"))
        object.__setattr__(self, "author", _optional_text(self.author, "author"))
        object.__setattr__(self, "license", _text(self.license, "license"))
        object.__setattr__(self, "license_url", _optional_text(self.license_url, "license_url"))
        acquired_at = _text(self.acquired_at, "acquired_at")
        if not _TIMESTAMP_RE.match(acquired_at):
            raise AssetResolutionError("acquired_at must be an ISO-8601 UTC instant (…Z)")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(
            self, "original_filename", _text(self.original_filename, "original_filename")
        )
        object.__setattr__(self, "local_path", _text(self.local_path, "local_path"))
        sha = _text(self.sha256, "sha256").lower()
        if not _SHA256_RE.match(sha):
            raise AssetResolutionError("sha256 must be 64 lowercase hex characters")
        object.__setattr__(self, "sha256", sha)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "candidate_id": self.candidate_id,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "author": self.author,
            "license": self.license,
            "license_url": self.license_url,
            "acquired_at": self.acquired_at,
            "original_filename": self.original_filename,
            "local_path": self.local_path,
            "sha256": self.sha256,
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetProvenance":
        _keys(
            data,
            required={
                "asset_id", "candidate_id", "source_kind", "source_url", "author",
                "license", "license_url", "acquired_at", "original_filename",
                "local_path", "sha256",
            },
            optional=set(),
        )
        return cls(**data)


# --------------------------------------------------------------------------- #
# ResolvedAsset / UnresolvedRequirement / AssetResolutionPlan
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    asset_id: str
    local_path: str
    candidate_id: str
    requirement: AssetRequirement
    score: float
    provenance: AssetProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "local_path", _text(self.local_path, "local_path"))
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        if not isinstance(self.requirement, AssetRequirement):
            raise AssetResolutionError("requirement must be an AssetRequirement")
        object.__setattr__(self, "score", _number(self.score, "score"))
        if not isinstance(self.provenance, AssetProvenance):
            raise AssetResolutionError("a resolved asset must carry an AssetProvenance")
        if self.provenance.local_path != self.local_path:
            raise AssetResolutionError(
                "resolved local_path must match its provenance local_path"
            )
        if self.provenance.asset_id != self.asset_id:
            raise AssetResolutionError("provenance asset_id must match the resolved asset_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "local_path": self.local_path,
            "candidate_id": self.candidate_id,
            "requirement": self.requirement.to_dict(),
            "score": self.score,
            "provenance": self.provenance.to_dict(),
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResolvedAsset":
        _keys(
            data,
            required={
                "asset_id", "local_path", "candidate_id", "requirement",
                "score", "provenance",
            },
            optional=set(),
        )
        return cls(
            asset_id=data["asset_id"],
            local_path=data["local_path"],
            candidate_id=data["candidate_id"],
            requirement=AssetRequirement.from_dict(data["requirement"]),
            score=data["score"],
            provenance=AssetProvenance.from_dict(data["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class UnresolvedRequirement:
    asset_id: str
    requirement: AssetRequirement
    reason: str
    detail: str | None = None
    sanitized_query: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        if not isinstance(self.requirement, AssetRequirement):
            raise AssetResolutionError("requirement must be an AssetRequirement")
        if self.reason not in UNRESOLVED_REASONS:
            raise AssetResolutionError(
                f"reason must be one of {', '.join(UNRESOLVED_REASONS)}"
            )
        object.__setattr__(self, "detail", _optional_text(self.detail, "detail"))
        object.__setattr__(
            self, "sanitized_query", _optional_text(self.sanitized_query, "sanitized_query")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "requirement": self.requirement.to_dict(),
            "reason": self.reason,
            "detail": self.detail,
            "sanitized_query": self.sanitized_query,
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UnresolvedRequirement":
        _keys(
            data,
            required={"asset_id", "requirement", "reason"},
            optional={"detail", "sanitized_query"},
        )
        return cls(
            asset_id=data["asset_id"],
            requirement=AssetRequirement.from_dict(data["requirement"]),
            reason=data["reason"],
            detail=data.get("detail"),
            sanitized_query=data.get("sanitized_query"),
        )


@dataclass(frozen=True, slots=True)
class AssetResolutionPlan:
    plan_id: str
    shot_plan_id: str
    script_id: str
    resolved: tuple[ResolvedAsset, ...]
    unresolved: tuple[UnresolvedRequirement, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "shot_plan_id", _text(self.shot_plan_id, "shot_plan_id"))
        object.__setattr__(self, "script_id", _text(self.script_id, "script_id"))
        resolved = tuple(self.resolved)
        unresolved = tuple(self.unresolved)
        if not all(isinstance(r, ResolvedAsset) for r in resolved):
            raise AssetResolutionError("resolved must contain ResolvedAsset values")
        if not all(isinstance(u, UnresolvedRequirement) for u in unresolved):
            raise AssetResolutionError("unresolved must contain UnresolvedRequirement values")
        object.__setattr__(self, "resolved", resolved)
        object.__setattr__(self, "unresolved", unresolved)
        ids = [r.asset_id for r in resolved] + [u.asset_id for u in unresolved]
        if len(set(ids)) != len(ids):
            raise AssetResolutionError(
                "an asset_id appears more than once across resolved / unresolved"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise AssetResolutionError(f"schema_version must be {SCHEMA_VERSION}")

    def to_bindings(self) -> dict[str, str]:
        return {r.asset_id: r.local_path for r in self.resolved}

    def validate_against(self, asset_requirements: AssetRequirements) -> None:
        if not isinstance(asset_requirements, AssetRequirements):
            raise AssetResolutionError("validate_against needs an AssetRequirements")
        required = {r.asset_id for r in asset_requirements.requirements}
        covered = {r.asset_id for r in self.resolved} | {u.asset_id for u in self.unresolved}
        if covered != required:
            missing = required - covered
            extra = covered - required
            problem = []
            if missing:
                problem.append(f"unhandled: {', '.join(sorted(missing))}")
            if extra:
                problem.append(f"unknown: {', '.join(sorted(extra))}")
            raise AssetResolutionError(
                "resolution plan must cover every requirement exactly once ("
                + "; ".join(problem)
                + ")"
            )
        for resolved in self.resolved:
            if resolved.provenance is None:  # pragma: no cover - constructor guards it
                raise AssetResolutionError("every resolved asset needs provenance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "shot_plan_id": self.shot_plan_id,
            "script_id": self.script_id,
            "resolved": [r.to_dict() for r in self.resolved],
            "unresolved": [u.to_dict() for u in self.unresolved],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetResolutionPlan":
        _keys(
            data,
            required={
                "schema_version", "plan_id", "shot_plan_id", "script_id",
                "resolved", "unresolved",
            },
            optional=set(),
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise AssetResolutionError(f"schema_version must be {SCHEMA_VERSION}")
        return cls(
            plan_id=data["plan_id"],
            shot_plan_id=data["shot_plan_id"],
            script_id=data["script_id"],
            resolved=tuple(ResolvedAsset.from_dict(r) for r in data["resolved"]),
            unresolved=tuple(
                UnresolvedRequirement.from_dict(u) for u in data["unresolved"]
            ),
            schema_version=data["schema_version"],
        )
