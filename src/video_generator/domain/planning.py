"""Scene Planner + Shot Planner: a pure, deterministic layer before EditPlan.

Turns a narrated script into a dense visual timeline expressed as inspectable
intermediate contracts (``NarrativeScript`` -> ``ScenePlan`` + ``ShotPlan`` +
``AssetRequirements``), then converts a resolved shot plan into an ``EditPlan``
for the existing ``video-sequence`` renderer.

Stdlib only. No I/O, no adapters, no renderer. Dependencies point inward: this
module may import from :mod:`video_generator.domain.models`; nothing there
imports back. Editorial content in the drafts is *derived* and meant to be
overridden by the orchestrator via :func:`apply_overrides`; every editorial
field carries a ``provenance`` marker.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from video_generator.domain.models import EditOperation, EditPlan, TargetFormat

SCHEMA_VERSION = 1

# The six Ken Burns moves the FFmpeg adapter knows how to translate (kept in
# sync with ``workflows.sequence.IMAGE_MOTIONS``; a domain module must not import
# outward from a workflow).
IMAGE_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down")

PROVENANCE_VALUES = ("derived", "authored")
ASSET_TYPES = ("image", "video")
ORIENTATIONS = ("landscape", "portrait", "square")

_DURATION_EPSILON = 1e-3  # "within 1 ms"


class PlanningError(ValueError):
    """Raised when planning data violates a contract or a cross-document rule."""


# --------------------------------------------------------------------------- #
# shared validation helpers (same shape as models.py, kept local so the module
# stays self-contained and its errors are PlanningError, not ContractError)
# --------------------------------------------------------------------------- #
def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _schema_version(value: object) -> int:
    if value != SCHEMA_VERSION:
        raise PlanningError(f"schema_version must be {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise PlanningError("contract payload must be an object")
    present = set(data)
    missing = required - present
    unknown = present - required - optional
    if missing:
        raise PlanningError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise PlanningError(f"unknown fields: {', '.join(sorted(unknown))}")


def _positive_number(value: object, field_name: str, *, allow_zero: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        qualifier = "non-negative" if allow_zero else "positive"
        raise PlanningError(f"{field_name} must be a finite {qualifier} number")
    return float(value)


def _int(value: object, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlanningError(f"{field_name} must be an integer >= {minimum}")
    return value


def _json_safe(value: object, field_name: str) -> object:
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise PlanningError(f"{field_name} must contain JSON-safe values") from exc


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- #
# tiny PT-BR text heuristics — feed *derived* drafts only
# --------------------------------------------------------------------------- #
_VOWEL_GROUP = re.compile(r"[aeiouyàáâãéêíóôõúü]+", re.IGNORECASE)
_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n")


def _estimate_syllables(word: str) -> int:
    """Rough syllable count. A deliberate ~8-line twin of the one in
    :mod:`video_generator.subtitles`; unifying them is a separate refactor and a
    domain module must not import outward."""

    letters = "".join(ch for ch in word if ch.isalpha()).lower()
    if not letters:
        return 0
    groups = len(_VOWEL_GROUP.findall(letters))
    if groups > 1 and letters.endswith("e"):
        groups -= 1
    return max(1, groups)


# closed-class PT-BR words that never make a useful visual keyword
_STOPWORDS = frozenset(
    """
    a o e as os um uma uns umas de do da dos das no na nos nas em para por com
    que se ao aos à às sua seu suas seus meu minha nossa nosso isso isto aquilo
    ele ela eles elas nós você vocês eu me te lhe nos vos mais menos muito pouco
    já não sim também como quando onde porque pois mas ou entãa então talvez
    ser estar ter haver fazer poder ir vir dar ver só até sobre entre sem
    the of to in on and or for with that as at by is are be this it
    """.split()
)


def _keywords(text: str, limit: int = 2) -> tuple[str, ...]:
    """Return up to ``limit`` frequent content words, ordered by (count desc,
    first-occurrence asc). Deterministic."""

    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for position, match in enumerate(_WORD.finditer(text.lower())):
        token = match.group(0)
        if len(token) < 4 or token in _STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
        first.setdefault(token, position)
    ranked = sorted(counts, key=lambda tok: (-counts[tok], first[tok]))
    return tuple(ranked[:limit])


def _decliche(token: str, policy: "RhythmPolicy") -> str:
    """Swap a discouraged visual clichÃ© token for a concrete phrase."""

    return policy.discouraged_cliches.get(token, token)


# --------------------------------------------------------------------------- #
# 2.1 RhythmPolicy
# --------------------------------------------------------------------------- #
_DEFAULT_SHOT_TYPE_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "b_roll_human": 3.0,
        "environment": 3.0,
        "object": 2.0,
        "screen": 2.0,
        "interface": 2.0,
        "document": 2.0,
        "photography": 2.0,
        "simple_graphic": 1.0,
        "on_screen_text": 1.0,
        "close_detail": 2.0,
        "establishing": 2.0,
        "insert": 2.0,
        "symbolic": 1.0,
    }
)
_DEFAULT_DISCOURAGED_CLICHES: Mapping[str, str] = MappingProxyType(
    {
        "cérebro": "pessoa concentrada em uma mesa",
        "cerebro": "pessoa concentrada em uma mesa",
        "máscara": "rosto parcialmente na sombra",
        "mascara": "rosto parcialmente na sombra",
        "silhueta": "pessoa de costas contra uma janela",
        "labirinto": "corredor estreito de escritório",
        "marionete": "mãos ajustando um objeto pequeno",
    }
)
_DEFAULT_ASSET_TYPE_FOR_SHOT_TYPE: Mapping[str, str] = MappingProxyType(
    {
        "b_roll_human": "video",
        "environment": "video",
        "interface": "video",
        "screen": "video",
        "establishing": "video",
    }
)
_BEAT_SHOT_TYPE_BONUS = MappingProxyType(
    {"establishing": 2.0, "symbolic": 2.0, "on_screen_text": 1.0}
)


@dataclass(frozen=True, slots=True)
class RhythmPolicy:
    """Editorial rhythm and density as data, not hardcoded constants."""

    min_shot_seconds: float = 1.5
    ideal_shot_seconds_low: float = 2.0
    ideal_shot_seconds_high: float = 6.0
    soft_max_shot_seconds: float = 8.0
    absolute_max_shot_seconds: float = 10.0
    min_scene_seconds: float = 6.0
    max_scene_seconds: float = 26.0
    speaking_rate_wpm: float = 155.0
    scale_cycle: tuple[str, ...] = ("wide", "medium", "close", "detail")
    shot_type_weights: Mapping[str, float] = field(
        default_factory=lambda: _DEFAULT_SHOT_TYPE_WEIGHTS
    )
    discouraged_cliches: Mapping[str, str] = field(
        default_factory=lambda: _DEFAULT_DISCOURAGED_CLICHES
    )
    asset_type_for_shot_type: Mapping[str, str] = field(
        default_factory=lambda: _DEFAULT_ASSET_TYPE_FOR_SHOT_TYPE
    )
    max_consecutive_same_scale: int = 2
    max_consecutive_same_shot_type: int = 1
    reuse_max_per_source: int = 3
    reuse_min_scene_gap: int = 2
    min_distinct_asset_ratio: float = 0.6
    duration_jitter_fraction: float = 0.2
    duration_needed_headroom_seconds: float = 1.0

    def __post_init__(self) -> None:
        bands = (
            self.min_shot_seconds,
            self.ideal_shot_seconds_low,
            self.ideal_shot_seconds_high,
            self.soft_max_shot_seconds,
            self.absolute_max_shot_seconds,
        )
        for value, name in zip(
            bands,
            (
                "min_shot_seconds",
                "ideal_shot_seconds_low",
                "ideal_shot_seconds_high",
                "soft_max_shot_seconds",
                "absolute_max_shot_seconds",
            ),
        ):
            _positive_number(value, name)
        if not all(a <= b for a, b in zip(bands, bands[1:])):
            raise PlanningError(
                "shot duration band must be ordered: min <= ideal_low <= "
                "ideal_high <= soft_max <= absolute_max"
            )
        _positive_number(self.min_scene_seconds, "min_scene_seconds")
        _positive_number(self.max_scene_seconds, "max_scene_seconds")
        if self.max_scene_seconds <= self.min_scene_seconds:
            raise PlanningError("max_scene_seconds must exceed min_scene_seconds")
        rate = _positive_number(self.speaking_rate_wpm, "speaking_rate_wpm")
        if not 60.0 <= rate <= 400.0:
            raise PlanningError("speaking_rate_wpm must be between 60 and 400")
        if not self.scale_cycle or len(set(self.scale_cycle)) != len(self.scale_cycle):
            raise PlanningError("scale_cycle must be non-empty and unique")
        if not all(isinstance(s, str) and s for s in self.scale_cycle):
            raise PlanningError("scale_cycle entries must be non-empty strings")
        if not self.shot_type_weights:
            raise PlanningError("shot_type_weights must not be empty")
        weights = dict(self.shot_type_weights)
        for key, weight in weights.items():
            if not isinstance(key, str) or not key:
                raise PlanningError("shot_type_weights keys must be non-empty strings")
            _positive_number(weight, f"shot_type_weights[{key}]", allow_zero=True)
        if not any(w > 0 for w in weights.values()):
            raise PlanningError("at least one shot_type weight must be positive")
        cliches = dict(self.discouraged_cliches)
        for key, replacement in cliches.items():
            if not isinstance(key, str) or not key:
                raise PlanningError("discouraged_cliches keys must be non-empty strings")
            _text(replacement, f"discouraged_cliches[{key}]")
        asset_types = dict(self.asset_type_for_shot_type)
        for key, value in asset_types.items():
            if key not in weights:
                raise PlanningError(
                    f"asset_type_for_shot_type key {key!r} is not a known shot type"
                )
            if value not in ASSET_TYPES:
                raise PlanningError("asset_type_for_shot_type values must be image or video")
        for name in (
            "max_consecutive_same_scale",
            "max_consecutive_same_shot_type",
            "reuse_max_per_source",
        ):
            _int(getattr(self, name), name, minimum=1)
        _int(self.reuse_min_scene_gap, "reuse_min_scene_gap", minimum=0)
        ratio = _positive_number(self.min_distinct_asset_ratio, "min_distinct_asset_ratio")
        if ratio > 1.0:
            raise PlanningError("min_distinct_asset_ratio must be in (0, 1]")
        jitter = _positive_number(
            self.duration_jitter_fraction, "duration_jitter_fraction", allow_zero=True
        )
        if jitter > 0.9:
            raise PlanningError("duration_jitter_fraction must be in [0, 0.9]")
        _positive_number(
            self.duration_needed_headroom_seconds,
            "duration_needed_headroom_seconds",
            allow_zero=True,
        )
        # freeze the mappings so the dataclass stays hashable/immutable
        object.__setattr__(self, "scale_cycle", tuple(self.scale_cycle))
        object.__setattr__(self, "shot_type_weights", MappingProxyType(dict(weights)))
        object.__setattr__(self, "discouraged_cliches", MappingProxyType(dict(cliches)))
        object.__setattr__(
            self, "asset_type_for_shot_type", MappingProxyType(dict(asset_types))
        )

    def asset_type_for(self, shot_type: str) -> str:
        return self.asset_type_for_shot_type.get(shot_type, "image")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_shot_seconds": self.min_shot_seconds,
            "ideal_shot_seconds_low": self.ideal_shot_seconds_low,
            "ideal_shot_seconds_high": self.ideal_shot_seconds_high,
            "soft_max_shot_seconds": self.soft_max_shot_seconds,
            "absolute_max_shot_seconds": self.absolute_max_shot_seconds,
            "min_scene_seconds": self.min_scene_seconds,
            "max_scene_seconds": self.max_scene_seconds,
            "speaking_rate_wpm": self.speaking_rate_wpm,
            "scale_cycle": list(self.scale_cycle),
            "shot_type_weights": dict(self.shot_type_weights),
            "discouraged_cliches": dict(self.discouraged_cliches),
            "asset_type_for_shot_type": dict(self.asset_type_for_shot_type),
            "max_consecutive_same_scale": self.max_consecutive_same_scale,
            "max_consecutive_same_shot_type": self.max_consecutive_same_shot_type,
            "reuse_max_per_source": self.reuse_max_per_source,
            "reuse_min_scene_gap": self.reuse_min_scene_gap,
            "min_distinct_asset_ratio": self.min_distinct_asset_ratio,
            "duration_jitter_fraction": self.duration_jitter_fraction,
            "duration_needed_headroom_seconds": self.duration_needed_headroom_seconds,
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RhythmPolicy":
        if not isinstance(data, Mapping):
            raise PlanningError("policy payload must be an object")
        allowed = set(cls().to_dict())
        unknown = set(data) - allowed
        if unknown:
            raise PlanningError(f"unknown policy fields: {', '.join(sorted(unknown))}")
        merged = cls().to_dict()
        merged.update(data)
        return cls(
            min_shot_seconds=merged["min_shot_seconds"],
            ideal_shot_seconds_low=merged["ideal_shot_seconds_low"],
            ideal_shot_seconds_high=merged["ideal_shot_seconds_high"],
            soft_max_shot_seconds=merged["soft_max_shot_seconds"],
            absolute_max_shot_seconds=merged["absolute_max_shot_seconds"],
            min_scene_seconds=merged["min_scene_seconds"],
            max_scene_seconds=merged["max_scene_seconds"],
            speaking_rate_wpm=merged["speaking_rate_wpm"],
            scale_cycle=tuple(merged["scale_cycle"]),
            shot_type_weights=dict(merged["shot_type_weights"]),
            discouraged_cliches=dict(merged["discouraged_cliches"]),
            asset_type_for_shot_type=dict(merged["asset_type_for_shot_type"]),
            max_consecutive_same_scale=merged["max_consecutive_same_scale"],
            max_consecutive_same_shot_type=merged["max_consecutive_same_shot_type"],
            reuse_max_per_source=merged["reuse_max_per_source"],
            reuse_min_scene_gap=merged["reuse_min_scene_gap"],
            min_distinct_asset_ratio=merged["min_distinct_asset_ratio"],
            duration_jitter_fraction=merged["duration_jitter_fraction"],
            duration_needed_headroom_seconds=merged["duration_needed_headroom_seconds"],
        )


DEFAULT_RHYTHM_POLICY = RhythmPolicy()


# --------------------------------------------------------------------------- #
# 2.2 NarrativeScript / NarrativeBlock
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NarrativeBlock:
    text: str
    duration_seconds: float | None = None
    visual_intent: str | None = None
    emphasis: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _text(self.text, "block text"))
        if self.duration_seconds is not None:
            object.__setattr__(
                self,
                "duration_seconds",
                _positive_number(self.duration_seconds, "block duration_seconds"),
            )
        object.__setattr__(
            self, "visual_intent", _optional_text(self.visual_intent, "block visual_intent")
        )
        if not isinstance(self.emphasis, bool):
            raise PlanningError("block emphasis must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "duration_seconds": self.duration_seconds,
            "visual_intent": self.visual_intent,
            "emphasis": self.emphasis,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrativeBlock":
        _keys(
            data,
            required={"text"},
            optional={"duration_seconds", "visual_intent", "emphasis"},
        )
        return cls(
            text=data["text"],
            duration_seconds=data.get("duration_seconds"),
            visual_intent=data.get("visual_intent"),
            emphasis=data.get("emphasis", False),
        )


@dataclass(frozen=True, slots=True)
class NarrativeScript:
    script_id: str
    blocks: tuple[NarrativeBlock, ...]
    language: str = "pt-br"
    total_duration_seconds: float | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "script_id", _text(self.script_id, "script_id"))
        object.__setattr__(self, "language", _text(self.language, "language"))
        if not isinstance(self.blocks, (list, tuple)) or not self.blocks:
            raise PlanningError("blocks must contain at least one item")
        blocks = tuple(self.blocks)
        if not all(isinstance(b, NarrativeBlock) for b in blocks):
            raise PlanningError("blocks must contain NarrativeBlock values")
        object.__setattr__(self, "blocks", blocks)
        if self.total_duration_seconds is not None:
            total = _positive_number(
                self.total_duration_seconds, "total_duration_seconds"
            )
            object.__setattr__(self, "total_duration_seconds", total)
            declared = [b.duration_seconds for b in blocks]
            if all(d is not None for d in declared):
                if abs(sum(declared) - total) > 1.0:
                    raise PlanningError(
                        "total_duration_seconds disagrees with the sum of block "
                        "durations by more than 1 second"
                    )
        _schema_version(self.schema_version)

    @classmethod
    def from_text(cls, script_id: str, text: str, **kwargs: Any) -> "NarrativeScript":
        paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
        if not paragraphs:
            raise PlanningError("script text has no non-empty paragraphs")
        return cls(
            script_id=script_id,
            blocks=tuple(NarrativeBlock(p) for p in paragraphs),
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "script_id": self.script_id,
            "language": self.language,
            "total_duration_seconds": self.total_duration_seconds,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrativeScript":
        _keys(
            data,
            required={"schema_version", "script_id", "blocks"},
            optional={"language", "total_duration_seconds"},
        )
        raw_blocks = data["blocks"]
        if not isinstance(raw_blocks, (list, tuple)):
            raise PlanningError("blocks must be an array")
        return cls(
            schema_version=data["schema_version"],
            script_id=data["script_id"],
            blocks=tuple(NarrativeBlock.from_dict(b) for b in raw_blocks),
            language=data.get("language", "pt-br"),
            total_duration_seconds=data.get("total_duration_seconds"),
        )


# --------------------------------------------------------------------------- #
# 2.3 ScenePlan / Scene
# --------------------------------------------------------------------------- #
def _scene_id(index: int) -> str:
    return f"scene_{index:02d}"


def _block_weight(text: str) -> float:
    """Speaking-time weight of a block: syllables plus a pause allowance for
    sentence- and clause-final punctuation."""

    syllables = sum(_estimate_syllables(w) for w in _WORD.findall(text)) or 1
    weight = float(syllables)
    weight += 3.0 * (text.count(".") + text.count("!") + text.count("?") + text.count("…"))
    weight += 1.2 * (text.count(",") + text.count(";") + text.count(":"))
    return weight


def _resolve_block_durations(script: "NarrativeScript", policy: "RhythmPolicy") -> list[float]:
    declared = [b.duration_seconds for b in script.blocks]
    if all(d is not None for d in declared):
        return [float(d) for d in declared]
    if script.total_duration_seconds is not None:
        fixed = sum(d for d in declared if d is not None)
        remaining = max(0.0, script.total_duration_seconds - fixed)
        unset = [i for i, d in enumerate(declared) if d is None]
        weights = [_block_weight(script.blocks[i].text) for i in unset]
        total_weight = sum(weights) or 1.0
        out = list(declared)
        for i, w in zip(unset, weights):
            out[i] = remaining * w / total_weight
        return [float(d) for d in out]
    # pure estimate: words / wpm
    out: list[float] = []
    for block, d in zip(script.blocks, declared):
        if d is not None:
            out.append(float(d))
            continue
        words = len(_WORD.findall(block.text))
        out.append(max(policy.min_shot_seconds, words / policy.speaking_rate_wpm * 60.0))
    return out


@dataclass(frozen=True, slots=True)
class Scene:
    scene_id: str
    narration: str
    duration_seconds: float
    visual_intent: str
    visual_intent_provenance: str
    source_block_indices: tuple[int, ...]
    emphasis_offsets: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", _text(self.scene_id, "scene_id"))
        object.__setattr__(self, "narration", _text(self.narration, "narration"))
        object.__setattr__(
            self,
            "duration_seconds",
            _positive_number(self.duration_seconds, "scene duration_seconds"),
        )
        object.__setattr__(self, "visual_intent", _text(self.visual_intent, "visual_intent"))
        if self.visual_intent_provenance not in PROVENANCE_VALUES:
            raise PlanningError("visual_intent_provenance must be derived or authored")
        indices = tuple(self.source_block_indices)
        if not indices:
            raise PlanningError("source_block_indices must contain at least one index")
        if any(
            isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in indices
        ):
            raise PlanningError("source_block_indices must be non-negative integers")
        if list(indices) != sorted(indices) or len(set(indices)) != len(indices):
            raise PlanningError("source_block_indices must be strictly increasing")
        if list(indices) != list(range(indices[0], indices[0] + len(indices))):
            raise PlanningError("source_block_indices must be contiguous within a scene")
        object.__setattr__(self, "source_block_indices", indices)
        offsets = tuple(
            _positive_number(o, "emphasis_offset") for o in self.emphasis_offsets
        )
        if list(offsets) != sorted(offsets) or len(set(offsets)) != len(offsets):
            raise PlanningError("emphasis_offsets must be sorted and unique")
        if any(not (0.0 < o < self.duration_seconds) for o in offsets):
            raise PlanningError(
                "emphasis_offsets must lie strictly inside (0, duration_seconds)"
            )
        object.__setattr__(self, "emphasis_offsets", offsets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "narration": self.narration,
            "duration_seconds": self.duration_seconds,
            "visual_intent": self.visual_intent,
            "visual_intent_provenance": self.visual_intent_provenance,
            "source_block_indices": list(self.source_block_indices),
            "emphasis_offsets": list(self.emphasis_offsets),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Scene":
        _keys(
            data,
            required={
                "scene_id",
                "narration",
                "duration_seconds",
                "visual_intent",
                "visual_intent_provenance",
                "source_block_indices",
                "emphasis_offsets",
            },
            optional=set(),
        )
        return cls(
            scene_id=data["scene_id"],
            narration=data["narration"],
            duration_seconds=data["duration_seconds"],
            visual_intent=data["visual_intent"],
            visual_intent_provenance=data["visual_intent_provenance"],
            source_block_indices=tuple(data["source_block_indices"]),
            emphasis_offsets=tuple(data["emphasis_offsets"]),
        )


@dataclass(frozen=True, slots=True)
class ScenePlan:
    plan_id: str
    script_id: str
    seed: int
    policy: RhythmPolicy
    scenes: tuple[Scene, ...]
    total_duration_seconds: float
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "script_id", _text(self.script_id, "script_id"))
        _int(self.seed, "seed", minimum=0)
        if not isinstance(self.policy, RhythmPolicy):
            raise PlanningError("policy must be a RhythmPolicy")
        scenes = tuple(self.scenes)
        if not scenes or not all(isinstance(s, Scene) for s in scenes):
            raise PlanningError("scenes must contain at least one Scene")
        object.__setattr__(self, "scenes", scenes)
        expected_ids = [_scene_id(i + 1) for i in range(len(scenes))]
        if [s.scene_id for s in scenes] != expected_ids:
            raise PlanningError("scene_id values must be contiguous scene_01..scene_NN")
        cursor = 0
        for scene in scenes:
            if scene.source_block_indices[0] != cursor:
                raise PlanningError(
                    "source_block_indices must form 0,1,2,... across scenes with no gap"
                )
            cursor = scene.source_block_indices[-1] + 1
        total = _positive_number(self.total_duration_seconds, "total_duration_seconds")
        if abs(sum(s.duration_seconds for s in scenes) - total) > _DURATION_EPSILON:
            raise PlanningError(
                "sum of scene durations must match total_duration_seconds within 1 ms"
            )
        _schema_version(self.schema_version)

    def scene(self, scene_id: str) -> Scene:
        for candidate in self.scenes:
            if candidate.scene_id == scene_id:
                return candidate
        raise PlanningError(f"unknown scene_id: {scene_id}")

    def validate_against(self, script: "NarrativeScript") -> None:
        if not isinstance(script, NarrativeScript):
            raise PlanningError("validate_against needs a NarrativeScript")
        if script.script_id != self.script_id:
            raise PlanningError("scene plan script_id does not match the script")
        highest = max(i for s in self.scenes for i in s.source_block_indices)
        if highest != len(script.blocks) - 1:
            raise PlanningError(
                "highest source_block_index must be the script's last block"
            )
        for scene in self.scenes:
            blocks = [script.blocks[i] for i in scene.source_block_indices]
            if scene.narration != "\n\n".join(b.text for b in blocks):
                raise PlanningError(
                    f"{scene.scene_id} narration does not match its source blocks"
                )
            durations = _resolve_block_durations(script, self.policy)
            running = 0.0
            for position, block_index in enumerate(scene.source_block_indices):
                block = script.blocks[block_index]
                if block.emphasis and position != 0:
                    if not any(
                        abs(running - o) <= _DURATION_EPSILON
                        for o in scene.emphasis_offsets
                    ):
                        raise PlanningError(
                            f"{scene.scene_id} is missing an emphasis offset for an "
                            "interior emphasis block"
                        )
                running += durations[block_index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "script_id": self.script_id,
            "seed": self.seed,
            "policy": self.policy.to_dict(),
            "scenes": [s.to_dict() for s in self.scenes],
            "total_duration_seconds": self.total_duration_seconds,
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScenePlan":
        _keys(
            data,
            required={
                "schema_version",
                "plan_id",
                "script_id",
                "seed",
                "policy",
                "scenes",
                "total_duration_seconds",
            },
            optional=set(),
        )
        return cls(
            schema_version=data["schema_version"],
            plan_id=data["plan_id"],
            script_id=data["script_id"],
            seed=data["seed"],
            policy=RhythmPolicy.from_dict(data["policy"]),
            scenes=tuple(Scene.from_dict(s) for s in data["scenes"]),
            total_duration_seconds=data["total_duration_seconds"],
        )


def _derived_visual_intent(text: str, policy: RhythmPolicy) -> str:
    keywords = [_decliche(k, policy) for k in _keywords(text, limit=2)]
    if not keywords:
        keywords = ["a ideia central da cena"]
    return "mostrar: " + " / ".join(keywords)


def plan_scenes(
    script: "NarrativeScript",
    *,
    policy: RhythmPolicy = DEFAULT_RHYTHM_POLICY,
    seed: int = 0,
    plan_id: str | None = None,
) -> ScenePlan:
    """Group a narrated script into scenes. Pure and deterministic."""

    if not isinstance(script, NarrativeScript):
        raise PlanningError("plan_scenes needs a NarrativeScript")
    durations = _resolve_block_durations(script, policy)

    # 1. group blocks into scene bins
    bins: list[list[int]] = []
    current: list[int] = []
    running = 0.0
    for index, (block, dur) in enumerate(zip(script.blocks, durations)):
        if dur > policy.max_scene_seconds and current:
            bins.append(current)
            current = []
            running = 0.0
        current.append(index)
        running += dur
        long_block = dur > policy.max_scene_seconds
        if (running >= policy.min_scene_seconds or long_block) and index != len(
            script.blocks
        ) - 1:
            bins.append(current)
            current = []
            running = 0.0
    if current:
        bins.append(current)
    if len(bins) >= 2 and sum(durations[i] for i in bins[-1]) < policy.min_scene_seconds:
        bins[-2].extend(bins.pop())

    # 2. build scenes
    scenes: list[Scene] = []
    for ordinal, indices in enumerate(bins, start=1):
        blocks = [script.blocks[i] for i in indices]
        scene_duration = sum(durations[i] for i in indices)
        offsets: list[float] = []
        running = 0.0
        for position, block_index in enumerate(indices):
            if script.blocks[block_index].emphasis and position != 0:
                offsets.append(round(running, 6))
            running += durations[block_index]
        authored = next((b.visual_intent for b in blocks if b.visual_intent), None)
        if authored is not None:
            intent, provenance = authored, "authored"
        else:
            intent = _derived_visual_intent(" ".join(b.text for b in blocks), policy)
            provenance = "derived"
        scenes.append(
            Scene(
                scene_id=_scene_id(ordinal),
                narration="\n\n".join(b.text for b in blocks),
                duration_seconds=scene_duration,
                visual_intent=intent,
                visual_intent_provenance=provenance,
                source_block_indices=tuple(indices),
                emphasis_offsets=tuple(o for o in offsets if 0.0 < o < scene_duration),
            )
        )

    plan = ScenePlan(
        plan_id=plan_id or f"{script.script_id}-scene-plan",
        script_id=script.script_id,
        seed=seed,
        policy=policy,
        scenes=tuple(scenes),
        total_duration_seconds=sum(s.duration_seconds for s in scenes),
    )
    plan.validate_against(script)
    return plan


# --------------------------------------------------------------------------- #
# 2.4 ShotPlan / Shot
# --------------------------------------------------------------------------- #
_PROVENANCE_FIELDS = ("visual_query", "purpose", "shot_type")
_FRAMING_KEYS = ("motion", "crop_bias")


def _shot_id(scene_id: str, index: int) -> str:
    return f"{scene_id}_shot_{index:02d}"


def _runs_ok(values: list[Any], limit: int) -> bool:
    run = 1
    for a, b in zip(values, values[1:]):
        run = run + 1 if a == b else 1
        if run > limit:
            return False
    return True


@dataclass(frozen=True, slots=True)
class Shot:
    shot_id: str
    scene_id: str
    index: int
    duration_seconds: float
    asset_type: str
    shot_type: str
    scale: str
    visual_query: str
    purpose: str
    beat: bool
    asset_id: str
    reuse_of: str | None
    framing: Mapping[str, Any]
    justification: str | None
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        object.__setattr__(self, "scene_id", _text(self.scene_id, "scene_id"))
        _int(self.index, "index", minimum=1)
        if self.shot_id != _shot_id(self.scene_id, self.index):
            raise PlanningError("shot_id must be f'{scene_id}_shot_{index:02d}'")
        object.__setattr__(
            self,
            "duration_seconds",
            _positive_number(self.duration_seconds, "shot duration_seconds"),
        )
        if self.asset_type not in ASSET_TYPES:
            raise PlanningError("asset_type must be image or video")
        object.__setattr__(self, "shot_type", _text(self.shot_type, "shot_type"))
        object.__setattr__(self, "scale", _text(self.scale, "scale"))
        if self.shot_type not in DEFAULT_RHYTHM_POLICY.shot_type_weights:
            # shot_type must be a known palette key; a custom policy may extend
            # the palette, in which case ShotPlan re-checks against its policy.
            pass
        object.__setattr__(self, "visual_query", _text(self.visual_query, "visual_query"))
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        if not isinstance(self.beat, bool):
            raise PlanningError("beat must be a boolean")
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "reuse_of", _optional_text(self.reuse_of, "reuse_of"))
        framing = dict(self.framing)
        if set(framing) - set(_FRAMING_KEYS):
            raise PlanningError(f"framing keys must be a subset of {_FRAMING_KEYS}")
        motion = framing.get("motion")
        if motion is not None and motion not in IMAGE_MOTIONS:
            raise PlanningError("framing motion must be null or one of IMAGE_MOTIONS")
        object.__setattr__(self, "framing", _freeze(_json_safe(framing, "framing")))
        object.__setattr__(
            self, "justification", _optional_text(self.justification, "justification")
        )
        provenance = dict(self.provenance)
        if set(provenance) - set(_PROVENANCE_FIELDS):
            raise PlanningError(f"provenance keys must be a subset of {_PROVENANCE_FIELDS}")
        if any(v not in PROVENANCE_VALUES for v in provenance.values()):
            raise PlanningError("provenance values must be derived or authored")
        object.__setattr__(self, "provenance", MappingProxyType(dict(provenance)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "index": self.index,
            "duration_seconds": self.duration_seconds,
            "asset_type": self.asset_type,
            "shot_type": self.shot_type,
            "scale": self.scale,
            "visual_query": self.visual_query,
            "purpose": self.purpose,
            "beat": self.beat,
            "asset_id": self.asset_id,
            "reuse_of": self.reuse_of,
            "framing": _thaw(self.framing),
            "justification": self.justification,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Shot":
        _keys(
            data,
            required={
                "shot_id", "scene_id", "index", "duration_seconds", "asset_type",
                "shot_type", "scale", "visual_query", "purpose", "beat",
                "asset_id", "reuse_of", "framing", "justification", "provenance",
            },
            optional=set(),
        )
        return cls(
            shot_id=data["shot_id"],
            scene_id=data["scene_id"],
            index=data["index"],
            duration_seconds=data["duration_seconds"],
            asset_type=data["asset_type"],
            shot_type=data["shot_type"],
            scale=data["scale"],
            visual_query=data["visual_query"],
            purpose=data["purpose"],
            beat=data["beat"],
            asset_id=data["asset_id"],
            reuse_of=data["reuse_of"],
            framing=data["framing"],
            justification=data["justification"],
            provenance=data["provenance"],
        )


@dataclass(frozen=True, slots=True)
class ShotPlan:
    plan_id: str
    scene_plan_id: str
    script_id: str
    seed: int
    policy: RhythmPolicy
    shots: tuple[Shot, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "scene_plan_id", _text(self.scene_plan_id, "scene_plan_id"))
        object.__setattr__(self, "script_id", _text(self.script_id, "script_id"))
        _int(self.seed, "seed", minimum=0)
        if not isinstance(self.policy, RhythmPolicy):
            raise PlanningError("policy must be a RhythmPolicy")
        shots = tuple(self.shots)
        if not shots or not all(isinstance(s, Shot) for s in shots):
            raise PlanningError("shots must contain at least one Shot")
        object.__setattr__(self, "shots", shots)
        ids = [s.shot_id for s in shots]
        if len(set(ids)) != len(ids):
            raise PlanningError("shot_id values must be unique")
        # scenes appear grouped and in scene_01, scene_02, ... order
        order: list[str] = []
        for shot in shots:
            if not order or order[-1] != shot.scene_id:
                if shot.scene_id in order:
                    raise PlanningError("a scene's shots must be contiguous")
                order.append(shot.scene_id)
        for position, scene_id in enumerate(order, start=1):
            if scene_id != _scene_id(position):
                raise PlanningError("scene ids must be contiguous scene_01..scene_NN")
        # per scene index 1..k
        per_scene: dict[str, list[int]] = {}
        for shot in shots:
            per_scene.setdefault(shot.scene_id, []).append(shot.index)
        for scene_id, indices in per_scene.items():
            if indices != list(range(1, len(indices) + 1)):
                raise PlanningError(f"{scene_id} shot indices must be contiguous 1..k")
        # duration bounds + justification rule
        for shot in shots:
            if shot.duration_seconds > self.policy.absolute_max_shot_seconds + 1e-9:
                raise PlanningError("a shot exceeds absolute_max_shot_seconds")
            over_soft = shot.duration_seconds > self.policy.soft_max_shot_seconds + 1e-9
            if over_soft and shot.justification is None:
                raise PlanningError("a shot over soft_max_shot_seconds needs a justification")
            if not over_soft and shot.justification is not None:
                raise PlanningError("justification is only for shots over soft_max_shot_seconds")
            if shot.shot_type not in self.policy.shot_type_weights:
                raise PlanningError(f"unknown shot_type: {shot.shot_type}")
            if shot.scale not in self.policy.scale_cycle:
                raise PlanningError(f"unknown scale: {shot.scale}")
            expected_asset_type = self.policy.asset_type_for(shot.shot_type)
            if shot.asset_type != expected_asset_type:
                raise PlanningError(
                    f"{shot.shot_id} asset_type must be {expected_asset_type} for "
                    f"shot_type {shot.shot_type}"
                )
        if not _runs_ok([s.scale for s in shots], self.policy.max_consecutive_same_scale):
            raise PlanningError("too many consecutive shots share a scale")
        if not _runs_ok(
            [s.shot_type for s in shots], self.policy.max_consecutive_same_shot_type
        ):
            raise PlanningError("too many consecutive shots share a shot_type")
        seen_assets: set[str] = set()
        for shot in shots:
            if shot.reuse_of is not None and shot.reuse_of not in seen_assets:
                raise PlanningError("reuse_of must point at an earlier shot's asset_id")
            seen_assets.add(shot.asset_id)
        _schema_version(self.schema_version)

    def shots_for(self, scene_id: str) -> tuple[Shot, ...]:
        return tuple(s for s in self.shots if s.scene_id == scene_id)

    def validate_against(self, scene_plan: "ScenePlan") -> None:
        if not isinstance(scene_plan, ScenePlan):
            raise PlanningError("validate_against needs a ScenePlan")
        plan_scene_ids = {s.scene_id for s in scene_plan.scenes}
        shot_scene_ids = {s.scene_id for s in self.shots}
        if shot_scene_ids - plan_scene_ids:
            raise PlanningError("shots reference scenes absent from the scene plan")
        if plan_scene_ids - shot_scene_ids:
            raise PlanningError("every scene must have at least one shot")
        first_scene_ordinal: dict[str, int] = {}
        users: dict[str, int] = {}
        for shot in self.shots:
            users[shot.asset_id] = users.get(shot.asset_id, 0) + 1
            ordinal = int(shot.scene_id.split("_")[1])
            first_scene_ordinal.setdefault(shot.asset_id, ordinal)
        for shot in self.shots:
            if shot.reuse_of is not None:
                ordinal = int(shot.scene_id.split("_")[1])
                gap = ordinal - first_scene_ordinal[shot.reuse_of]
                if gap < self.policy.reuse_min_scene_gap:
                    raise PlanningError(
                        "reuse_of must reach back at least reuse_min_scene_gap scenes"
                    )
        for asset_id, count in users.items():
            if count > self.policy.reuse_max_per_source:
                raise PlanningError("an asset_id is reused more than reuse_max_per_source")
        for scene in scene_plan.scenes:
            scene_shots = self.shots_for(scene.scene_id)
            if abs(
                sum(s.duration_seconds for s in scene_shots) - scene.duration_seconds
            ) > _DURATION_EPSILON:
                raise PlanningError(
                    f"{scene.scene_id} shot durations do not sum to the scene duration"
                )
            starts: list[tuple[float, Shot]] = []
            running = 0.0
            for shot in scene_shots:
                starts.append((running, shot))
                running += shot.duration_seconds
            for offset in scene.emphasis_offsets:
                on_offset = [s for start, s in starts if abs(start - offset) <= _DURATION_EPSILON]
                if len(on_offset) != 1 or not on_offset[0].beat:
                    raise PlanningError(
                        f"{scene.scene_id} emphasis offset has no single beat shot"
                    )
            for start, shot in starts:
                if shot.beat and not any(
                    abs(start - o) <= _DURATION_EPSILON for o in scene.emphasis_offsets
                ):
                    raise PlanningError(
                        f"{shot.shot_id} is marked beat but starts off every emphasis offset"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "scene_plan_id": self.scene_plan_id,
            "script_id": self.script_id,
            "seed": self.seed,
            "policy": self.policy.to_dict(),
            "shots": [s.to_dict() for s in self.shots],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ShotPlan":
        _keys(
            data,
            required={
                "schema_version", "plan_id", "scene_plan_id", "script_id",
                "seed", "policy", "shots",
            },
            optional=set(),
        )
        return cls(
            schema_version=data["schema_version"],
            plan_id=data["plan_id"],
            scene_plan_id=data["scene_plan_id"],
            script_id=data["script_id"],
            seed=data["seed"],
            policy=RhythmPolicy.from_dict(data["policy"]),
            shots=tuple(Shot.from_dict(s) for s in data["shots"]),
        )


# --------------------------------------------------------------------------- #
# 2.5 AssetRequirements / AssetRequirement
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AssetRequirement:
    asset_id: str
    type: str
    query: str
    duration_needed_seconds: float
    orientation: str
    purpose: str
    used_by: tuple[str, ...]
    min_count: int = 1
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        if self.type not in ASSET_TYPES:
            raise PlanningError("asset type must be image or video")
        object.__setattr__(self, "query", _text(self.query, "query"))
        object.__setattr__(
            self,
            "duration_needed_seconds",
            _positive_number(self.duration_needed_seconds, "duration_needed_seconds"),
        )
        if self.orientation not in ORIENTATIONS:
            raise PlanningError("orientation must be landscape, portrait or square")
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        used = tuple(self.used_by)
        if not used or len(set(used)) != len(used):
            raise PlanningError("used_by must be a non-empty set of shot ids")
        object.__setattr__(self, "used_by", used)
        _int(self.min_count, "min_count", minimum=1)
        object.__setattr__(self, "notes", _optional_text(self.notes, "notes"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "type": self.type,
            "query": self.query,
            "duration_needed_seconds": self.duration_needed_seconds,
            "orientation": self.orientation,
            "purpose": self.purpose,
            "used_by": list(self.used_by),
            "min_count": self.min_count,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRequirement":
        _keys(
            data,
            required={
                "asset_id", "type", "query", "duration_needed_seconds",
                "orientation", "purpose", "used_by",
            },
            optional={"min_count", "notes"},
        )
        return cls(
            asset_id=data["asset_id"],
            type=data["type"],
            query=data["query"],
            duration_needed_seconds=data["duration_needed_seconds"],
            orientation=data["orientation"],
            purpose=data["purpose"],
            used_by=tuple(data["used_by"]),
            min_count=data.get("min_count", 1),
            notes=data.get("notes"),
        )


@dataclass(frozen=True, slots=True)
class AssetRequirements:
    plan_id: str
    shot_plan_id: str
    script_id: str
    orientation: str
    requirements: tuple[AssetRequirement, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "shot_plan_id", _text(self.shot_plan_id, "shot_plan_id"))
        object.__setattr__(self, "script_id", _text(self.script_id, "script_id"))
        if self.orientation not in ORIENTATIONS:
            raise PlanningError("orientation must be landscape, portrait or square")
        reqs = tuple(self.requirements)
        if not reqs or not all(isinstance(r, AssetRequirement) for r in reqs):
            raise PlanningError("requirements must contain at least one AssetRequirement")
        object.__setattr__(self, "requirements", reqs)
        if len({r.asset_id for r in reqs}) != len(reqs):
            raise PlanningError("asset_id values must be unique")
        if any(r.orientation != self.orientation for r in reqs):
            raise PlanningError("every requirement orientation must match the top orientation")
        _schema_version(self.schema_version)

    def validate_against(self, shot_plan: "ShotPlan") -> None:
        if not isinstance(shot_plan, ShotPlan):
            raise PlanningError("validate_against needs a ShotPlan")
        all_shot_ids = {s.shot_id for s in shot_plan.shots}
        covered: set[str] = set()
        for req in self.requirements:
            if covered & set(req.used_by):
                raise PlanningError("used_by sets must be pairwise disjoint")
            covered |= set(req.used_by)
        if covered != all_shot_ids:
            raise PlanningError(
                "the union of used_by must equal the set of every shot_id"
            )
        by_asset: dict[str, AssetRequirement] = {r.asset_id: r for r in self.requirements}
        shots_by_id = {s.shot_id: s for s in shot_plan.shots}
        for req in self.requirements:
            shots = [shots_by_id[sid] for sid in req.used_by]
            if any(s.asset_type != req.type for s in shots):
                raise PlanningError(
                    f"{req.asset_id} type disagrees with a shot that uses it"
                )
            longest = max(s.duration_seconds for s in shots)
            if req.duration_needed_seconds < longest - 1e-9:
                raise PlanningError(
                    f"{req.asset_id} duration_needed_seconds is below its longest shot"
                )
        distinct_asset_ids = {s.asset_id for s in shot_plan.shots}
        if set(by_asset) != distinct_asset_ids:
            raise PlanningError(
                "every distinct shot asset_id needs exactly one matching requirement"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "shot_plan_id": self.shot_plan_id,
            "script_id": self.script_id,
            "orientation": self.orientation,
            "requirements": [r.to_dict() for r in self.requirements],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRequirements":
        _keys(
            data,
            required={
                "schema_version", "plan_id", "shot_plan_id", "script_id",
                "orientation", "requirements",
            },
            optional=set(),
        )
        return cls(
            schema_version=data["schema_version"],
            plan_id=data["plan_id"],
            shot_plan_id=data["shot_plan_id"],
            script_id=data["script_id"],
            orientation=data["orientation"],
            requirements=tuple(
                AssetRequirement.from_dict(r) for r in data["requirements"]
            ),
        )


# --------------------------------------------------------------------------- #
# plan_shots — bounded deterministic densification
# --------------------------------------------------------------------------- #
def _shot_count(length: float, policy: RhythmPolicy) -> int:
    if length < 2 * policy.min_shot_seconds:
        return 1
    target = (policy.ideal_shot_seconds_low + policy.ideal_shot_seconds_high) / 2.0
    lo_n = math.ceil(length / policy.soft_max_shot_seconds - 1e-9)
    hi_n = math.floor(length / policy.min_shot_seconds + 1e-9)
    raw = max(1, round(length / target))
    if lo_n <= hi_n:
        return min(max(raw, lo_n), hi_n)
    return max(1, hi_n)


def _redistribute(
    length: float, n: int, rng: random.Random, policy: RhythmPolicy
) -> list[float]:
    """Split ``length`` into ``n`` shot durations with bounded seeded jitter,
    keeping the exact sum and every shot inside [min, soft_max] when feasible.
    No single shot absorbs the residual (correction 5)."""

    if n <= 1:
        return [length]
    lo, hi = policy.min_shot_seconds, policy.soft_max_shot_seconds
    jitter = policy.duration_jitter_fraction
    weights = [1.0 + rng.uniform(-jitter, jitter) for _ in range(n)]
    total_weight = sum(weights)
    durations = [length * w / total_weight for w in weights]

    for _ in range(64):
        excess = sum(max(0.0, d - hi) for d in durations)
        deficit = sum(max(0.0, lo - d) for d in durations)
        durations = [min(hi, max(lo, d)) for d in durations]
        net = excess - deficit  # > 0: remove from slack; < 0: add to slack
        if abs(net) < 1e-12:
            break
        if net > 0:
            slack = [d - lo for d in durations]
        else:
            slack = [hi - d for d in durations]
        pool = sum(slack)
        if pool <= 1e-12:
            break
        durations = [d + (-net) * (slack[i] / pool) for i, d in enumerate(durations)]

    for _ in range(32):
        residual = length - sum(durations)
        if abs(residual) < 1e-9:
            break
        slack = [
            (hi - d) if residual > 0 else (d - lo) for d in durations
        ]
        pool = sum(slack)
        if pool <= 1e-12:
            break
        durations = [d + residual * (slack[i] / pool) for i, d in enumerate(durations)]

    residual = length - sum(durations)
    if abs(residual) > 1e-9:
        # tiny leftover: hand it to the shot with the most room, still bounded
        idx = max(
            range(n),
            key=lambda k: (hi - durations[k]) if residual > 0 else (durations[k] - lo),
        )
        durations[idx] += residual
    return durations


def _weighted_choice(rng: random.Random, weights: Mapping[str, float]) -> str:
    items = sorted((k, v) for k, v in weights.items() if v > 0)
    total = sum(v for _, v in items)
    if total <= 0:
        # every candidate was zeroed by a run limit; fall back to the lot
        items = sorted(weights.items())
        total = sum(v for _, v in items) or 1.0
    mark = rng.random() * total
    upto = 0.0
    for key, value in items:
        upto += value
        if mark <= upto:
            return key
    return items[-1][0]


def _emphasis_segments(
    scene: Scene, policy: RhythmPolicy
) -> list[tuple[float, float, bool]]:
    """Return (start, end, starts_on_emphasis) segments for a scene."""

    bounds: list[float] = [0.0]
    is_beat: list[bool] = [False]
    for offset in scene.emphasis_offsets:
        floor_pos = bounds[-1] + policy.min_shot_seconds
        pos = max(offset, floor_pos)
        if pos >= scene.duration_seconds - policy.min_shot_seconds:
            continue  # cannot fit a min-length shot on either side; drop it
        bounds.append(pos)
        is_beat.append(True)
    bounds.append(scene.duration_seconds)
    is_beat.append(False)
    return [
        (bounds[i], bounds[i + 1], is_beat[i]) for i in range(len(bounds) - 1)
    ]


def _slice_text(text: str, proportions: list[float]) -> list[str]:
    words = text.split()
    if not words:
        return ["" for _ in proportions]
    out: list[str] = []
    cursor = 0
    total = sum(proportions) or 1.0
    for i, weight in enumerate(proportions):
        take = round(len(words) * weight / total) if i < len(proportions) - 1 else len(words) - cursor
        take = max(0, take)
        out.append(" ".join(words[cursor:cursor + take]))
        cursor += take
    return out


def _visual_query(scale: str, shot_type: str, slice_text: str, policy: RhythmPolicy) -> str:
    keywords = [_decliche(k, policy) for k in _keywords(slice_text, limit=3)]
    phrase = ", ".join(keywords) if keywords else "detalhe da cena"
    return f"{scale} {shot_type.replace('_', ' ')}, {phrase}"


def _motion_for_scale(scale: str, rng: random.Random) -> tuple[str | None, str]:
    if scale == "wide":
        return "zoom_in", "center"
    if scale == "medium":
        pick = rng.choice(("pan_left", "pan_right"))
        return pick, "left" if pick == "pan_left" else "right"
    if scale == "close":
        return "zoom_out", "center"
    if scale == "detail":
        return None, rng.choice(("top", "bottom", "left", "right"))
    return None, "center"


def plan_shots(
    scene_plan: "ScenePlan",
    *,
    policy: RhythmPolicy | None = None,
    seed: int = 0,
    orientation: str = "landscape",
    plan_id: str | None = None,
    asset_plan_id: str | None = None,
) -> tuple["ShotPlan", "AssetRequirements"]:
    """Densify a ScenePlan into many short shots plus the assets they need."""

    if not isinstance(scene_plan, ScenePlan):
        raise PlanningError("plan_shots needs a ScenePlan")
    policy = policy or scene_plan.policy
    if orientation not in ORIENTATIONS:
        raise PlanningError("orientation must be landscape, portrait or square")
    rng = random.Random(seed)

    # first pass: how many shots total (for the distinct-asset floor)
    scene_layouts: list[tuple[Scene, list[tuple[float, float, bool, int]]]] = []
    total_shots = 0
    for scene in scene_plan.scenes:
        layout: list[tuple[float, float, bool, int]] = []
        for start, end, on_emphasis in _emphasis_segments(scene, policy):
            count = _shot_count(end - start, policy)
            layout.append((start, end, on_emphasis, count))
            total_shots += count
        scene_layouts.append((scene, layout))
    distinct_floor = math.ceil(policy.min_distinct_asset_ratio * total_shots)

    flat_scales: list[str] = []
    flat_types: list[str] = []
    shots: list[Shot] = []
    # requirement bookkeeping keyed by asset_id, kept in insertion order
    reqs: "dict[str, dict[str, Any]]" = {}
    distinct = 0
    shots_emitted = 0

    for scene, layout in scene_layouts:
        scene_shot_count = sum(count for *_rest, count in layout)
        scene_shots: list[Shot] = []
        scale_pos = rng.randrange(len(policy.scale_cycle))
        segment_durations: list[float] = []
        segment_beat_flags: list[bool] = []
        for start, end, on_emphasis, count in layout:
            durs = _redistribute(end - start, count, rng, policy)
            for j, d in enumerate(durs):
                segment_durations.append(d)
                segment_beat_flags.append(on_emphasis and j == 0)
        text_slices = _slice_text(scene.narration, segment_durations)

        for local_index, (dur, is_beat, slice_text) in enumerate(
            zip(segment_durations, segment_beat_flags, text_slices), start=1
        ):
            # scale: advance the cycle, skip a value that would break the run
            for _ in range(len(policy.scale_cycle)):
                candidate = policy.scale_cycle[scale_pos % len(policy.scale_cycle)]
                scale_pos += 1
                if _runs_ok(
                    flat_scales + [candidate], policy.max_consecutive_same_scale
                ):
                    scale = candidate
                    break
            else:
                scale = policy.scale_cycle[scale_pos % len(policy.scale_cycle)]

            # shot type: weighted draw, zero out anything that would break the run
            weights = dict(policy.shot_type_weights)
            for key in list(weights):
                if not _runs_ok(flat_types + [key], policy.max_consecutive_same_shot_type):
                    weights[key] = 0.0
            if is_beat:
                for key, bonus in _BEAT_SHOT_TYPE_BONUS.items():
                    if weights.get(key, 0.0) > 0.0:
                        weights[key] += bonus
            shot_type = _weighted_choice(rng, weights)
            asset_type = policy.asset_type_for(shot_type)

            motion, crop_bias = _motion_for_scale(scale, rng)
            framing = {"motion": motion, "crop_bias": crop_bias}

            visual_query = _visual_query(scale, shot_type, slice_text, policy)
            purpose = f"{scene.visual_intent} — beat {local_index}/{scene_shot_count}"

            # asset + reuse
            shots_left = total_shots - shots_emitted  # includes this one
            fresh_id = f"asset_{scene.scene_id}_{local_index:02d}"
            reuse_target: str | None = None
            for asset_id, meta in reversed(list(reqs.items())):
                if meta["shot_type"] != shot_type or meta["scale"] != scale:
                    continue
                if len(meta["used_by"]) >= policy.reuse_max_per_source:
                    continue
                ordinal = int(scene.scene_id.split("_")[1])
                if ordinal - meta["first_ordinal"] < policy.reuse_min_scene_gap:
                    continue
                # only reuse if the distinct floor can still be met afterwards
                if distinct + (shots_left - 1) >= distinct_floor:
                    reuse_target = asset_id
                break

            if reuse_target is not None:
                asset_id = reuse_target
                reqs[asset_id]["used_by"].append(_shot_id(scene.scene_id, local_index))
                reqs[asset_id]["durations"].append(dur)
                reuse_of = asset_id
            else:
                asset_id = fresh_id
                reqs[asset_id] = {
                    "type": asset_type,
                    "shot_type": shot_type,
                    "scale": scale,
                    "query": visual_query,
                    "purpose": purpose,
                    "used_by": [_shot_id(scene.scene_id, local_index)],
                    "durations": [dur],
                    "first_ordinal": int(scene.scene_id.split("_")[1]),
                }
                distinct += 1
                reuse_of = None

            over_soft = dur > policy.soft_max_shot_seconds + 1e-9
            justification = (
                "segment shorter than policy allows for min-length shots; densified to floor"
                if over_soft
                else None
            )

            shot = Shot(
                shot_id=_shot_id(scene.scene_id, local_index),
                scene_id=scene.scene_id,
                index=local_index,
                duration_seconds=dur,
                asset_type=asset_type,
                shot_type=shot_type,
                scale=scale,
                visual_query=visual_query,
                purpose=purpose,
                beat=is_beat,
                asset_id=asset_id,
                reuse_of=reuse_of,
                framing=framing,
                justification=justification,
                provenance={
                    "visual_query": "derived",
                    "purpose": "derived",
                    "shot_type": "derived",
                },
            )
            scene_shots.append(shot)
            flat_scales.append(scale)
            flat_types.append(shot_type)
            shots_emitted += 1

        shots.extend(scene_shots)

    shot_plan = ShotPlan(
        plan_id=plan_id or f"{scene_plan.script_id}-shot-plan",
        scene_plan_id=scene_plan.plan_id,
        script_id=scene_plan.script_id,
        seed=seed,
        policy=policy,
        shots=tuple(shots),
    )
    shot_plan.validate_against(scene_plan)

    asset_requirements = _asset_requirements_from_shots(
        shot_plan,
        orientation=orientation,
        plan_id=asset_plan_id or f"{scene_plan.script_id}-asset-requirements",
    )
    asset_requirements.validate_against(shot_plan)
    return shot_plan, asset_requirements


def _asset_requirements_from_shots(
    shot_plan: "ShotPlan", *, orientation: str, plan_id: str
) -> "AssetRequirements":
    """Group a shot plan's shots by their (immutable) asset_id into requirements.

    The reuse graph lives on the shots; this only reads it. Representative
    ``query`` / ``purpose`` come from each asset's first user in timeline order.
    """

    order: list[str] = []
    grouped: dict[str, list[Shot]] = {}
    for shot in shot_plan.shots:
        if shot.asset_id not in grouped:
            grouped[shot.asset_id] = []
            order.append(shot.asset_id)
        grouped[shot.asset_id].append(shot)
    headroom = shot_plan.policy.duration_needed_headroom_seconds
    requirements: list[AssetRequirement] = []
    for asset_id in order:
        users = grouped[asset_id]
        types = {u.asset_type for u in users}
        if len(types) != 1:
            raise PlanningError(
                f"{asset_id} is shared by shots of different asset types"
            )
        requirements.append(
            AssetRequirement(
                asset_id=asset_id,
                type=users[0].asset_type,
                query=users[0].visual_query,
                duration_needed_seconds=max(u.duration_seconds for u in users) + headroom,
                orientation=orientation,
                purpose=users[0].purpose,
                used_by=tuple(u.shot_id for u in users),
            )
        )
    return AssetRequirements(
        plan_id=plan_id,
        shot_plan_id=shot_plan.plan_id,
        script_id=shot_plan.script_id,
        orientation=orientation,
        requirements=tuple(requirements),
    )


# --------------------------------------------------------------------------- #
# 5. apply_overrides — the hybrid half (heuristic draft -> agent refines)
# --------------------------------------------------------------------------- #
_WRITABLE_SHOT_KEYS = ("visual_query", "purpose", "shot_type", "motion")
_WRITABLE_SCENE_KEYS = ("visual_intent",)


def apply_overrides(
    scene_plan: "ScenePlan",
    shot_plan: "ShotPlan",
    assets: "AssetRequirements",
    overrides: Mapping[str, Any],
    *,
    script: "NarrativeScript | None" = None,
) -> tuple["ScenePlan", "ShotPlan", "AssetRequirements"]:
    """Apply editorial overrides to all three planning documents and return them
    rebuilt and re-validated. Only editorial fields are writable; ids, timing,
    structure and the reuse graph are immutable."""

    for obj, name in (
        (scene_plan, "scene_plan"),
        (shot_plan, "shot_plan"),
        (assets, "assets"),
    ):
        expected = {"scene_plan": ScenePlan, "shot_plan": ShotPlan, "assets": AssetRequirements}[name]
        if not isinstance(obj, expected):
            raise PlanningError(f"{name} must be a {expected.__name__}")
    if not isinstance(overrides, Mapping) or set(overrides) - {"shots", "scenes"}:
        raise PlanningError("overrides may only contain 'shots' and 'scenes'")

    scene_overrides = dict(overrides.get("scenes", {}))
    shot_overrides = dict(overrides.get("shots", {}))

    known_scene_ids = {s.scene_id for s in scene_plan.scenes}
    known_shot_ids = {s.shot_id for s in shot_plan.shots}

    # --- scenes ---------------------------------------------------------------
    patched_intent: dict[str, str] = {}
    for scene_id, patch in scene_overrides.items():
        if scene_id not in known_scene_ids:
            raise PlanningError(f"override for unknown scene_id: {scene_id}")
        if not isinstance(patch, Mapping) or set(patch) - set(_WRITABLE_SCENE_KEYS):
            raise PlanningError(
                f"scene override keys must be a subset of {_WRITABLE_SCENE_KEYS}"
            )
        if "visual_intent" in patch:
            patched_intent[scene_id] = _text(patch["visual_intent"], "visual_intent")

    new_scenes = []
    for scene in scene_plan.scenes:
        if scene.scene_id in patched_intent:
            new_scenes.append(
                Scene(
                    scene_id=scene.scene_id,
                    narration=scene.narration,
                    duration_seconds=scene.duration_seconds,
                    visual_intent=patched_intent[scene.scene_id],
                    visual_intent_provenance="authored",
                    source_block_indices=scene.source_block_indices,
                    emphasis_offsets=scene.emphasis_offsets,
                )
            )
        else:
            new_scenes.append(scene)
    new_scene_plan = ScenePlan(
        plan_id=scene_plan.plan_id,
        script_id=scene_plan.script_id,
        seed=scene_plan.seed,
        policy=scene_plan.policy,
        scenes=tuple(new_scenes),
        total_duration_seconds=scene_plan.total_duration_seconds,
    )
    if script is not None:
        new_scene_plan.validate_against(script)

    # --- shots -------------------------------------------------------------- #
    for shot_id, patch in shot_overrides.items():
        if shot_id not in known_shot_ids:
            raise PlanningError(f"override for unknown shot_id: {shot_id}")
        if not isinstance(patch, Mapping) or set(patch) - set(_WRITABLE_SHOT_KEYS):
            raise PlanningError(
                f"shot override keys must be a subset of {_WRITABLE_SHOT_KEYS}"
            )

    # per scene, how many shots (for the "beat i/n" purpose text)
    counts: dict[str, int] = {}
    for shot in shot_plan.shots:
        counts[shot.scene_id] = counts.get(shot.scene_id, 0) + 1

    new_shots = []
    for shot in shot_plan.shots:
        patch = dict(shot_overrides.get(shot.shot_id, {}))
        provenance = dict(shot.provenance)
        visual_query = shot.visual_query
        purpose = shot.purpose
        shot_type = shot.shot_type
        asset_type = shot.asset_type
        framing = dict(shot.framing)

        # scene visual_intent change flows into derived purposes of that scene
        if (
            shot.scene_id in patched_intent
            and provenance.get("purpose", "derived") == "derived"
            and "purpose" not in patch
        ):
            purpose = (
                f"{patched_intent[shot.scene_id]} — beat {shot.index}/{counts[shot.scene_id]}"
            )

        if "visual_query" in patch:
            visual_query = _text(patch["visual_query"], "visual_query")
            provenance["visual_query"] = "authored"
        if "purpose" in patch:
            purpose = _text(patch["purpose"], "purpose")
            provenance["purpose"] = "authored"
        if "shot_type" in patch:
            shot_type = _text(patch["shot_type"], "shot_type")
            if shot_type not in shot_plan.policy.shot_type_weights:
                raise PlanningError(f"override shot_type is not in the policy: {shot_type}")
            asset_type = shot_plan.policy.asset_type_for(shot_type)
            provenance["shot_type"] = "authored"
        if "motion" in patch:
            motion = patch["motion"]
            if motion is not None and motion not in IMAGE_MOTIONS:
                raise PlanningError("override motion must be null or an IMAGE_MOTIONS value")
            framing["motion"] = motion

        new_shots.append(
            Shot(
                shot_id=shot.shot_id,
                scene_id=shot.scene_id,
                index=shot.index,
                duration_seconds=shot.duration_seconds,
                asset_type=asset_type,
                shot_type=shot_type,
                scale=shot.scale,
                visual_query=visual_query,
                purpose=purpose,
                beat=shot.beat,
                asset_id=shot.asset_id,
                reuse_of=shot.reuse_of,
                framing=framing,
                justification=shot.justification,
                provenance=provenance,
            )
        )

    new_shot_plan = ShotPlan(
        plan_id=shot_plan.plan_id,
        scene_plan_id=shot_plan.scene_plan_id,
        script_id=shot_plan.script_id,
        seed=shot_plan.seed,
        policy=shot_plan.policy,
        shots=tuple(new_shots),
    )
    new_shot_plan.validate_against(new_scene_plan)

    new_assets = _asset_requirements_from_shots(
        new_shot_plan, orientation=assets.orientation, plan_id=assets.plan_id
    )
    new_assets.validate_against(new_shot_plan)
    return new_scene_plan, new_shot_plan, new_assets


# --------------------------------------------------------------------------- #
# 6. shot_plan_to_edit_plan — bridge to the existing renderer (no I/O)
# --------------------------------------------------------------------------- #
_COVER_SCALES = ("close", "detail")
_CONTAIN_SHOT_TYPES = ("on_screen_text", "document", "simple_graphic")


def _fit_for(shot: "Shot") -> str | None:
    if shot.scale in _COVER_SCALES:
        return "cover"
    if shot.shot_type in _CONTAIN_SHOT_TYPES:
        return "contain"
    return None


def shot_plan_to_edit_plan(
    shot_plan: "ShotPlan",
    asset_bindings: Mapping[str, str],
    *,
    plan_id: str,
    brief_id: str,
    output_path: str,
    target_format: TargetFormat,
    extra_operations: tuple[EditOperation, ...] = (),
) -> EditPlan:
    """Translate a resolved shot plan into a valid ``EditPlan`` for the
    ``video-sequence`` renderer.

    ``asset_bindings`` maps every ``asset_id`` in the plan to a local file path.
    The converter does no I/O: it assumes each bound video asset is long enough
    for the cumulative windows placed on it. Validating real asset lengths is
    the later asset-acquisition stage's job.
    """

    if not isinstance(shot_plan, ShotPlan):
        raise PlanningError("shot_plan_to_edit_plan needs a ShotPlan")
    if not isinstance(target_format, TargetFormat):
        raise PlanningError("target_format must be a TargetFormat")
    needed = {s.asset_id for s in shot_plan.shots}
    missing = needed - set(asset_bindings)
    if missing:
        raise PlanningError(f"asset_bindings is missing: {', '.join(sorted(missing))}")

    cursor: dict[str, float] = {}
    sources: list[str] = []
    operations: list[EditOperation] = []
    for shot in shot_plan.shots:
        path = asset_bindings[shot.asset_id]
        if path not in sources:
            sources.append(path)
        fit = _fit_for(shot)
        if shot.asset_type == "image":
            params: dict[str, Any] = {"duration_seconds": shot.duration_seconds}
            if fit is not None:
                params["fit"] = fit
            motion = shot.framing.get("motion")
            if motion in IMAGE_MOTIONS:
                params["motion"] = motion
            operations.append(
                EditOperation(
                    operation_id=shot.shot_id,
                    kind="image_clip",
                    source=path,
                    parameters=params,
                )
            )
        else:
            start = cursor.get(path, 0.0)
            end = start + shot.duration_seconds
            cursor[path] = end
            params = {}
            if fit is not None:
                params["fit"] = fit
            operations.append(
                EditOperation(
                    operation_id=shot.shot_id,
                    kind="sequence_clip",
                    source=path,
                    start_seconds=start,
                    end_seconds=end,
                    parameters=params,
                )
            )

    for extra in extra_operations:
        if not isinstance(extra, EditOperation):
            raise PlanningError("extra_operations must be EditOperation values")
        if extra.source is not None and extra.source not in sources:
            sources.append(extra.source)

    return EditPlan(
        plan_id=plan_id,
        brief_id=brief_id,
        sources=tuple(sources),
        output_path=output_path,
        operations=tuple(operations) + tuple(extra_operations),
        target_format=target_format,
    )
