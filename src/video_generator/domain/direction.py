"""Visual Direction v1 — how a chosen asset is allowed to appear on screen.

Semantic Visual Relevance (:mod:`.relevance`) answers *which* picture stands
for a beat. Nothing answered *how* that picture is presented, so every shot
arrived the same way: the whole frame, a stock Ken Burns move, untouched
colour. Sixty good photographs presented identically read as a slideshow of
good photographs, not as a film.

This module is the missing half of the chain::

    beat -> visual intent -> visual role -> asset
         -> composition -> motion -> grade -> emphasis -> render

Four decisions per shot, each one a small closed vocabulary rather than free
text:

* :data:`COMPOSITIONS` — how much of the frame the asset occupies and what
  surrounds it;
* :data:`MOTIONS` — an editorial move, including the deliberate absence of one;
* :data:`GRADE_INTENSITIES` — how hard the channel's colour treatment is
  applied to *this* asset, so a photograph that is already black is not crushed
  and one that arrived bright is pulled into the same world;
* an emphasis flag, so the composition can be built to *hold* on-screen text
  instead of fighting it.

Everything is a deterministic function of the shot plan, the policy and the
seed: the same three inputs always produce the same :class:`VisualDirection`.
The channel's identity lives in the policy (a JSON file in the project), never
in this module — the domain knows the grammar, the project knows the accent.

Pure stdlib. Depends only on :mod:`.relevance` for the two label vocabularies;
nothing here imports :mod:`.planning`, an adapter, or any I/O.
"""

from __future__ import annotations

import json
import math
import random
import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from video_generator.domain.relevance import VISUAL_INTENTS, VISUAL_ROLES

SCHEMA_VERSION = 1


class DirectionError(ValueError):
    """Raised when a visual direction contract or policy is invalid."""


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #
# How the asset occupies the frame. Six entries, no more: a grammar a viewer
# cannot learn in ninety seconds is decoration, not language.
COMPOSITIONS = (
    # the asset fills the canvas — the neutral statement
    "fullscreen",
    # a hard push into the picture: tension, detail, discomfort
    "extreme_crop",
    # the asset held small inside a treated derivation of itself — editorial
    # distance, an object placed on a page
    "inset",
    # the asset at its own aspect ratio over a blurred, darkened copy — the
    # answer to material that fullscreen would butcher
    "layered",
    # two regions of the same frame side by side — only for a beat that is
    # itself a comparison
    "split",
    # a composition built to carry on-screen type: a scrim where the words go
    "text_focus",
)

# Compositions that need more than one pass over the source. Moving footage is
# already a composition and a second blurred copy of it is expensive for very
# little, so video segments are held to the cheap half of the grammar.
_STILL_ONLY_COMPOSITIONS = ("inset", "layered", "split")

# An editorial move, or the decision not to move.
MOTIONS = (
    "static_hold",
    "slow_push_in",
    "slow_pull_out",
    "lateral_drift",
    "detail_push",
)

# How hard the channel grade is applied to one asset.
GRADE_INTENSITIES = ("none", "subtle", "standard", "strong")

# Where a crop or a scrim is biased.
CROP_BIASES = ("center", "top", "bottom", "left", "right")

# Where emphasis type sits, mirroring the text-event vocabulary.
TEXT_ZONES = ("top", "middle", "lower")


# --------------------------------------------------------------------------- #
# small helpers (kept local: the domain modules do not share a utility module)
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_fold(m.group(0)) for m in _WORD.finditer(text or ""))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DirectionError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _number(value: object, name: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DirectionError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise DirectionError(f"{name} must lie in [{low}, {high}]")
    return value


def _positive_int(value: object, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DirectionError(f"{name} must be an integer")
    if value < minimum:
        raise DirectionError(f"{name} must be at least {minimum}")
    return value


def _weights(
    value: object, name: str, vocabulary: Sequence[str]
) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise DirectionError(f"{name} must be an object")
    out: dict[str, float] = {}
    for key, weight in value.items():
        if key not in vocabulary:
            raise DirectionError(f"{name} has an unknown key: {key}")
        out[key] = _number(weight, f"{name}[{key}]", low=0.0, high=100.0)
    return MappingProxyType(out)


def _hex_colour(value: object, name: str) -> str:
    text = _text(value, name)
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise DirectionError(f"{name} must be a #RRGGBB colour")
    return text.upper()


def _to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- #
# 1. the grade — the one treatment that touches the picture itself
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GradePolicy:
    """The channel's colour treatment, as numbers rather than a look-up table.

    Every value is the amount applied at intensity ``standard``; the other
    intensities scale all of them by one factor, so "subtle" and "strong" are
    the same grade quieter and louder, never a different grade.

    The defaults describe the look this project asked for — desaturated,
    slightly contrastier, denser shadows, controlled highlights, a cool cast,
    a little grain and a vignette you should not be able to name.
    """

    saturation: float = 0.80
    shadow_density: float = 0.060
    highlight_gain: float = 0.030
    highlight_ceiling: float = 0.045
    cool_shift: float = 0.035
    grain: float = 4.0
    vignette_angle: float = 0.42
    # A multiplicative pull on the whole tonal range. Trimming highlights can
    # control a bright sky; only this can bring a photograph that is mostly
    # white paper into a dark piece.
    luminance_pull: float = 0.0

    _RANGES = MappingProxyType(
        {
            "saturation": (0.0, 2.0),
            "shadow_density": (0.0, 0.20),
            "highlight_gain": (0.0, 0.20),
            "highlight_ceiling": (0.0, 0.30),
            "cool_shift": (0.0, 0.40),
            "grain": (0.0, 40.0),
            "vignette_angle": (0.0, 1.2),
            "luminance_pull": (0.0, 0.60),
        }
    )

    def __post_init__(self) -> None:
        for name, (low, high) in self._RANGES.items():
            object.__setattr__(
                self, name, _number(getattr(self, name), name, low=low, high=high)
            )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self._RANGES}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GradePolicy":
        if not isinstance(data, Mapping):
            raise DirectionError("grade must be an object")
        unknown = set(data) - set(cls._RANGES)
        if unknown:
            raise DirectionError(f"unknown grade fields: {', '.join(sorted(unknown))}")
        merged = cls().to_dict()
        merged.update(data)
        return cls(**merged)


# The scale factor each intensity applies to every number in the GradePolicy.
DEFAULT_GRADE_INTENSITIES: Mapping[str, float] = MappingProxyType(
    {"none": 0.0, "subtle": 0.55, "standard": 1.0, "strong": 1.35}
)


# --------------------------------------------------------------------------- #
# 2. motifs — recurrence of subject, not repetition of asset
# --------------------------------------------------------------------------- #
# A motif is a thing the channel keeps coming back to. It is a *label*: naming
# it lets a later pass measure whether the piece has a visual vocabulary, and
# lets the composition planner vary the framing when the same motif returns.
# It never forces a subject onto a beat that has nothing to do with it.
_DEFAULT_MOTIFS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "corridor": ("corridor", "hallway", "tunnel", "passage", "stairwell", "subway"),
        "empty_space": (
            "empty", "abandoned", "deserted", "vacant", "alone", "isolated", "lonely",
        ),
        "shadow": ("shadow", "shadows", "dark", "darkness", "night", "silhouette", "backlit"),
        "reflection": ("reflection", "mirror", "glass", "window", "puddle", "reflected"),
        "hands": ("hand", "hands", "fingers", "palm", "grip", "holding"),
        "writing": ("writing", "pen", "pencil", "notebook", "handwriting", "ink", "signature"),
        "documents": ("document", "documents", "paper", "papers", "file", "archive", "letter", "form"),
        "screens": ("screen", "monitor", "phone", "display", "television", "laptop", "feed"),
        "information": ("data", "chart", "graph", "numbers", "statistics", "records", "code"),
        "crowd": ("crowd", "crowded", "pedestrians", "commuters", "queue", "market", "street"),
        "hidden_face": ("hidden", "obscured", "blurred", "faceless", "covered", "behind", "veil"),
        "architecture": ("building", "facade", "concrete", "architecture", "columns", "brutalist"),
        "isolated_object": ("object", "chair", "door", "clock", "key", "bottle", "still"),
        "human_detail": ("eyes", "eye", "face", "portrait", "closeup", "close", "skin", "mouth"),
    }
)


# --------------------------------------------------------------------------- #
# 3. the policy
# --------------------------------------------------------------------------- #
# The default bias per visual role. These are *weights on a draw*, not rules:
# the repetition limits below still forbid a run, and a gate can zero any of
# them for a shot that cannot carry it.
_DEFAULT_COMPOSITION_WEIGHTS: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "build_tension": MappingProxyType(
            {"fullscreen": 2.0, "extreme_crop": 5.0, "inset": 1.0, "layered": 1.5, "split": 0.5, "text_focus": 2.0}
        ),
        "shock": MappingProxyType(
            {"fullscreen": 4.0, "extreme_crop": 4.0, "inset": 0.5, "layered": 1.0, "split": 1.5, "text_focus": 2.5}
        ),
        "humanize": MappingProxyType(
            {"fullscreen": 3.0, "extreme_crop": 3.5, "inset": 1.0, "layered": 2.0, "split": 0.3, "text_focus": 1.0}
        ),
        "symbolize": MappingProxyType(
            {"fullscreen": 2.5, "extreme_crop": 2.0, "inset": 3.0, "layered": 2.5, "split": 0.8, "text_focus": 1.5}
        ),
        "explain": MappingProxyType(
            {"fullscreen": 3.0, "extreme_crop": 1.5, "inset": 2.0, "layered": 2.5, "split": 1.2, "text_focus": 1.5}
        ),
        "contextualize": MappingProxyType(
            {"fullscreen": 4.0, "extreme_crop": 1.0, "inset": 1.5, "layered": 2.0, "split": 0.5, "text_focus": 1.0}
        ),
        "support_claim": MappingProxyType(
            {"fullscreen": 2.5, "extreme_crop": 2.0, "inset": 2.5, "layered": 2.0, "split": 1.5, "text_focus": 2.5}
        ),
    }
)

_DEFAULT_MOTION_WEIGHTS: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "build_tension": MappingProxyType(
            {"static_hold": 3.0, "slow_push_in": 4.0, "slow_pull_out": 0.8, "lateral_drift": 1.0, "detail_push": 3.0}
        ),
        "shock": MappingProxyType(
            {"static_hold": 4.0, "slow_push_in": 2.0, "slow_pull_out": 1.0, "lateral_drift": 0.5, "detail_push": 2.0}
        ),
        "humanize": MappingProxyType(
            {"static_hold": 2.5, "slow_push_in": 3.0, "slow_pull_out": 1.5, "lateral_drift": 2.0, "detail_push": 2.0}
        ),
        "symbolize": MappingProxyType(
            {"static_hold": 3.0, "slow_push_in": 1.5, "slow_pull_out": 2.5, "lateral_drift": 2.5, "detail_push": 1.0}
        ),
        "explain": MappingProxyType(
            {"static_hold": 2.0, "slow_push_in": 2.0, "slow_pull_out": 1.5, "lateral_drift": 3.0, "detail_push": 1.5}
        ),
        "contextualize": MappingProxyType(
            {"static_hold": 2.0, "slow_push_in": 1.5, "slow_pull_out": 3.0, "lateral_drift": 3.0, "detail_push": 0.8}
        ),
        "support_claim": MappingProxyType(
            {"static_hold": 3.0, "slow_push_in": 2.0, "slow_pull_out": 1.5, "lateral_drift": 1.5, "detail_push": 2.0}
        ),
    }
)

# A beat that is explicitly a turn in the argument is the only place a split
# screen earns its keep: two things held against each other.
_SPLIT_ROLES = ("contrast",)


@dataclass(frozen=True, slots=True)
class VisualDirectionPolicy:
    """The channel's visual identity: what the grammar is allowed to do here.

    Lives in the project (``visual-direction-v1.json``), not in the domain, so
    a second channel is a second file rather than a second code path.
    """

    style_id: str = "visual-direction-v1"
    accent: str = "#E5A33C"
    grade: GradePolicy = field(default_factory=GradePolicy)
    grade_intensities: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_GRADE_INTENSITIES
    )
    default_grade: str = "standard"
    # Mean luma (0..1) below which an asset is already dark enough that the
    # full grade would crush it, and above which it is bright enough to need
    # pulling into the piece.
    dark_luma: float = 0.24
    bright_luma: float = 0.58
    composition_weights: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: _DEFAULT_COMPOSITION_WEIGHTS
    )
    motion_weights: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: _DEFAULT_MOTION_WEIGHTS
    )
    max_consecutive_same_composition: int = 2
    max_consecutive_same_motion: int = 2
    # Below this a move is not read as a move, only as instability.
    min_motion_seconds: float = 1.7
    min_split_seconds: float = 2.2
    # Geometry of the compositions, as fractions of the canvas.
    extreme_crop_zoom: float = 1.55
    inset_scale: float = 0.70
    background_blur_sigma: float = 26.0
    background_darkening: float = 0.45
    split_gap_fraction: float = 0.008
    scrim_opacity: float = 0.55
    scrim_height_fraction: float = 0.34
    # Travel of each move over the whole clip, as a fraction of the frame.
    push_travel: float = 0.075
    detail_push_travel: float = 0.115
    drift_travel: float = 0.090
    motifs: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: _DEFAULT_MOTIFS)
    schema_version: int = SCHEMA_VERSION

    _FRACTIONS = MappingProxyType(
        {
            "dark_luma": (0.0, 1.0),
            "bright_luma": (0.0, 1.0),
            "min_motion_seconds": (0.0, 20.0),
            "min_split_seconds": (0.0, 20.0),
            "extreme_crop_zoom": (1.05, 3.0),
            "inset_scale": (0.30, 0.95),
            "background_blur_sigma": (0.0, 100.0),
            "background_darkening": (0.0, 0.95),
            "split_gap_fraction": (0.0, 0.10),
            "scrim_opacity": (0.0, 0.95),
            "scrim_height_fraction": (0.10, 0.80),
            "push_travel": (0.0, 0.40),
            "detail_push_travel": (0.0, 0.40),
            "drift_travel": (0.0, 0.40),
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "style_id", _text(self.style_id, "style_id"))
        object.__setattr__(self, "accent", _hex_colour(self.accent, "accent"))
        if not isinstance(self.grade, GradePolicy):
            raise DirectionError("grade must be a GradePolicy; parse mappings via from_dict")
        intensities = dict(self.grade_intensities)
        if set(intensities) != set(GRADE_INTENSITIES):
            raise DirectionError(
                "grade_intensities must define exactly " + ", ".join(GRADE_INTENSITIES)
            )
        for name, value in intensities.items():
            intensities[name] = _number(
                value, f"grade_intensities[{name}]", low=0.0, high=3.0
            )
        if intensities["none"] != 0.0:
            raise DirectionError('grade_intensities["none"] must be 0')
        object.__setattr__(self, "grade_intensities", MappingProxyType(intensities))
        if self.default_grade not in GRADE_INTENSITIES:
            raise DirectionError(
                "default_grade must be one of " + ", ".join(GRADE_INTENSITIES)
            )
        for name, (low, high) in self._FRACTIONS.items():
            object.__setattr__(
                self, name, _number(getattr(self, name), name, low=low, high=high)
            )
        if self.dark_luma >= self.bright_luma:
            raise DirectionError("dark_luma must be below bright_luma")
        object.__setattr__(
            self,
            "composition_weights",
            _role_weight_table(self.composition_weights, "composition_weights", COMPOSITIONS),
        )
        object.__setattr__(
            self,
            "motion_weights",
            _role_weight_table(self.motion_weights, "motion_weights", MOTIONS),
        )
        _positive_int(
            self.max_consecutive_same_composition, "max_consecutive_same_composition"
        )
        _positive_int(self.max_consecutive_same_motion, "max_consecutive_same_motion")
        motifs: dict[str, tuple[str, ...]] = {}
        if not isinstance(self.motifs, Mapping):
            raise DirectionError("motifs must be an object")
        for name, terms in self.motifs.items():
            key = _text(name, "motif name")
            if isinstance(terms, str) or not isinstance(terms, (list, tuple)):
                raise DirectionError(f"motifs[{key}] must be an array of terms")
            folded = tuple(_fold(_text(term, f"motifs[{key}] term")) for term in terms)
            if not folded:
                raise DirectionError(f"motifs[{key}] must contain at least one term")
            motifs[key] = folded
        object.__setattr__(self, "motifs", MappingProxyType(motifs))
        if self.schema_version != SCHEMA_VERSION:
            raise DirectionError(f"schema_version must be {SCHEMA_VERSION}")

    # --- grade resolution --------------------------------------------------- #
    def grade_for_luma(self, luma: float | None) -> tuple[str, str]:
        """The grade intensity this asset should get, and why.

        A photograph that is already black does not need the full treatment —
        applying it anyway is how shadows become a flat block. One that arrived
        bright needs more than the default, or it will read as belonging to a
        different video.
        """

        if luma is None:
            return self.default_grade, "no luma measurement"
        if luma <= self.dark_luma:
            return "subtle", f"already dark (luma {luma:.2f})"
        if luma >= self.bright_luma:
            return "strong", f"bright source pulled in (luma {luma:.2f})"
        return self.default_grade, f"mid luma {luma:.2f}"

    def motif_for(self, *texts: str) -> str | None:
        """The first motif whose vocabulary this shot's own words touch."""

        bag = set()
        for text in texts:
            bag.update(_tokens(text))
        if not bag:
            return None
        for name in sorted(self.motifs):
            if bag & set(self.motifs[name]):
                return name
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "style_id": self.style_id,
            "accent": self.accent,
            "grade": self.grade.to_dict(),
            "grade_intensities": dict(self.grade_intensities),
            "default_grade": self.default_grade,
            "dark_luma": self.dark_luma,
            "bright_luma": self.bright_luma,
            "composition_weights": {
                role: dict(weights) for role, weights in self.composition_weights.items()
            },
            "motion_weights": {
                role: dict(weights) for role, weights in self.motion_weights.items()
            },
            "max_consecutive_same_composition": self.max_consecutive_same_composition,
            "max_consecutive_same_motion": self.max_consecutive_same_motion,
            "min_motion_seconds": self.min_motion_seconds,
            "min_split_seconds": self.min_split_seconds,
            "extreme_crop_zoom": self.extreme_crop_zoom,
            "inset_scale": self.inset_scale,
            "background_blur_sigma": self.background_blur_sigma,
            "background_darkening": self.background_darkening,
            "split_gap_fraction": self.split_gap_fraction,
            "scrim_opacity": self.scrim_opacity,
            "scrim_height_fraction": self.scrim_height_fraction,
            "push_travel": self.push_travel,
            "detail_push_travel": self.detail_push_travel,
            "drift_travel": self.drift_travel,
            "motifs": {name: list(terms) for name, terms in self.motifs.items()},
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualDirectionPolicy":
        """Merge a partial policy over the defaults, like every other policy here."""

        if not isinstance(data, Mapping):
            raise DirectionError("policy payload must be an object")
        defaults = cls()
        allowed = set(defaults.to_dict())
        unknown = set(data) - allowed
        if unknown:
            raise DirectionError(f"unknown policy fields: {', '.join(sorted(unknown))}")
        if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise DirectionError(f"schema_version must be {SCHEMA_VERSION}")
        merged: dict[str, Any] = defaults.to_dict()
        merged.pop("schema_version", None)
        for key, value in data.items():
            if key == "schema_version":
                continue
            merged[key] = value
        merged["grade"] = (
            merged["grade"]
            if isinstance(merged["grade"], GradePolicy)
            else GradePolicy.from_dict(merged["grade"])
        )
        # Weight tables merge per role, so a project may retune one role
        # without restating the other six.
        for name, vocabulary in (
            ("composition_weights", COMPOSITIONS),
            ("motion_weights", MOTIONS),
        ):
            table = {
                role: dict(weights)
                for role, weights in getattr(defaults, name).items()
            }
            given = merged[name]
            if not isinstance(given, Mapping):
                raise DirectionError(f"{name} must be an object")
            for role, weights in given.items():
                if role not in VISUAL_ROLES:
                    raise DirectionError(f"{name} has an unknown visual role: {role}")
                if not isinstance(weights, Mapping):
                    raise DirectionError(f"{name}[{role}] must be an object")
                table.setdefault(role, {})
                for key, weight in weights.items():
                    if key not in vocabulary:
                        raise DirectionError(f"{name}[{role}] has an unknown key: {key}")
                    table[role][key] = weight
            merged[name] = table
        return cls(**merged)


def _role_weight_table(
    value: object, name: str, vocabulary: Sequence[str]
) -> Mapping[str, Mapping[str, float]]:
    if not isinstance(value, Mapping):
        raise DirectionError(f"{name} must be an object")
    table: dict[str, Mapping[str, float]] = {}
    for role, weights in value.items():
        if role not in VISUAL_ROLES:
            raise DirectionError(f"{name} has an unknown visual role: {role}")
        table[role] = _weights(weights, f"{name}[{role}]", vocabulary)
    missing = set(VISUAL_ROLES) - set(table)
    if missing:
        raise DirectionError(f"{name} is missing roles: {', '.join(sorted(missing))}")
    return MappingProxyType(table)


DEFAULT_DIRECTION_POLICY = VisualDirectionPolicy()


# --------------------------------------------------------------------------- #
# 4. the per-shot contract
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VisualDirection:
    """How one shot appears: framing, movement, treatment, on-screen type.

    Serialisable and auditable by design — reading the JSON should answer "why
    does this shot look like this?" without opening the planner.
    """

    shot_id: str
    composition: str
    motion: str
    grade: str
    emphasis: bool = False
    crop_bias: str = "center"
    text_zone: str | None = None
    visual_motif: str | None = None
    visual_role: str | None = None
    visual_intent_class: str | None = None
    rationale: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        if self.composition not in COMPOSITIONS:
            raise DirectionError("composition must be one of " + ", ".join(COMPOSITIONS))
        if self.motion not in MOTIONS:
            raise DirectionError("motion must be one of " + ", ".join(MOTIONS))
        if self.grade not in GRADE_INTENSITIES:
            raise DirectionError("grade must be one of " + ", ".join(GRADE_INTENSITIES))
        if not isinstance(self.emphasis, bool):
            raise DirectionError("emphasis must be a boolean")
        if self.crop_bias not in CROP_BIASES:
            raise DirectionError("crop_bias must be one of " + ", ".join(CROP_BIASES))
        if self.text_zone is not None and self.text_zone not in TEXT_ZONES:
            raise DirectionError("text_zone must be one of " + ", ".join(TEXT_ZONES))
        if self.composition == "text_focus" and self.text_zone is None:
            raise DirectionError("a text_focus composition must state its text_zone")
        if self.motion == "detail_push" and self.composition != "extreme_crop":
            raise DirectionError("detail_push only exists inside an extreme_crop")
        object.__setattr__(
            self, "visual_motif", _optional_text(self.visual_motif, "visual_motif")
        )
        if self.visual_role is not None and self.visual_role not in VISUAL_ROLES:
            raise DirectionError("visual_role must be a known visual role")
        if (
            self.visual_intent_class is not None
            and self.visual_intent_class not in VISUAL_INTENTS
        ):
            raise DirectionError("visual_intent_class must be a known visual intent")
        if not isinstance(self.rationale, str):
            raise DirectionError("rationale must be a string")
        object.__setattr__(self, "rationale", self.rationale.strip())
        if self.schema_version != SCHEMA_VERSION:
            raise DirectionError(f"schema_version must be {SCHEMA_VERSION}")

    @property
    def moves(self) -> bool:
        return self.motion != "static_hold"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shot_id": self.shot_id,
            "composition": self.composition,
            "motion": self.motion,
            "grade": self.grade,
            "emphasis": self.emphasis,
            "crop_bias": self.crop_bias,
            "text_zone": self.text_zone,
            "visual_motif": self.visual_motif,
            "visual_role": self.visual_role,
            "visual_intent_class": self.visual_intent_class,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualDirection":
        if not isinstance(data, Mapping):
            raise DirectionError("a direction payload must be an object")
        required = {"shot_id", "composition", "motion", "grade"}
        optional = {
            "schema_version", "emphasis", "crop_bias", "text_zone", "visual_motif",
            "visual_role", "visual_intent_class", "rationale",
        }
        missing = required - set(data)
        unknown = set(data) - required - optional
        if missing:
            raise DirectionError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise DirectionError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            shot_id=data["shot_id"],
            composition=data["composition"],
            motion=data["motion"],
            grade=data["grade"],
            emphasis=data.get("emphasis", False),
            crop_bias=data.get("crop_bias", "center"),
            text_zone=data.get("text_zone"),
            visual_motif=data.get("visual_motif"),
            visual_role=data.get("visual_role"),
            visual_intent_class=data.get("visual_intent_class"),
            rationale=data.get("rationale", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class VisualDirectionPlan:
    """Every shot's direction, plus the policy that produced them."""

    plan_id: str
    shot_plan_id: str
    script_id: str
    seed: int
    policy: VisualDirectionPolicy
    directions: tuple[VisualDirection, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("plan_id", "shot_plan_id", "script_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise DirectionError("seed must be a non-negative integer")
        if not isinstance(self.policy, VisualDirectionPolicy):
            raise DirectionError("policy must be a VisualDirectionPolicy")
        directions = tuple(self.directions)
        if not directions or not all(
            isinstance(d, VisualDirection) for d in directions
        ):
            raise DirectionError("directions must contain at least one VisualDirection")
        ids = [d.shot_id for d in directions]
        if len(set(ids)) != len(ids):
            raise DirectionError("shot_id values must be unique")
        object.__setattr__(self, "directions", directions)
        if self.schema_version != SCHEMA_VERSION:
            raise DirectionError(f"schema_version must be {SCHEMA_VERSION}")

    def by_shot(self) -> dict[str, VisualDirection]:
        return {d.shot_id: d for d in self.directions}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "shot_plan_id": self.shot_plan_id,
            "script_id": self.script_id,
            "seed": self.seed,
            "policy": self.policy.to_dict(),
            "directions": [d.to_dict() for d in self.directions],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualDirectionPlan":
        if not isinstance(data, Mapping):
            raise DirectionError("a direction plan payload must be an object")
        required = {
            "schema_version", "plan_id", "shot_plan_id", "script_id", "seed",
            "policy", "directions",
        }
        missing = required - set(data)
        unknown = set(data) - required
        if missing:
            raise DirectionError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise DirectionError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            plan_id=data["plan_id"],
            shot_plan_id=data["shot_plan_id"],
            script_id=data["script_id"],
            seed=data["seed"],
            policy=VisualDirectionPolicy.from_dict(data["policy"]),
            directions=tuple(VisualDirection.from_dict(d) for d in data["directions"]),
            schema_version=data["schema_version"],
        )


# --------------------------------------------------------------------------- #
# 5. the planner
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DirectionInput:
    """One shot, reduced to exactly what the direction planner may read.

    A deliberate seam: the planner never imports the shot plan, so the same
    decisions can be replayed from a table in a test without building a whole
    plan first.
    """

    shot_id: str
    duration_seconds: float
    asset_type: str
    scale: str
    visual_role: str | None = None
    visual_intent_class: str | None = None
    editorial_role: str | None = None
    crop_bias: str = "center"
    emphasis: bool = False
    text_zone: str | None = None
    asset_luma: float | None = None
    text: str = ""
    # True when nothing in the frame may be cut: a document, a graphic, a
    # screen. Cropping such a shot loses the information it exists to show,
    # so the grammar answers with a treated band instead.
    preserve_frame: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        object.__setattr__(
            self,
            "duration_seconds",
            _number(self.duration_seconds, "duration_seconds", low=0.0, high=3600.0),
        )
        if self.asset_type not in ("image", "video"):
            raise DirectionError("asset_type must be image or video")
        if self.crop_bias not in CROP_BIASES:
            raise DirectionError("crop_bias must be one of " + ", ".join(CROP_BIASES))
        if self.asset_luma is not None:
            object.__setattr__(
                self, "asset_luma", _number(self.asset_luma, "asset_luma", low=0.0, high=1.0)
            )


_FALLBACK_ROLE = "explain"


def _runs_ok(values: Sequence[str], candidate: str, limit: int) -> bool:
    run = 1
    for value in reversed(values):
        if value != candidate:
            break
        run += 1
        if run > limit:
            return False
    return run <= limit


def _pick(rng: random.Random, weights: Mapping[str, float]) -> str:
    """A seeded weighted draw over a *sorted* vocabulary.

    Sorted so the draw depends only on the weights and the seed, never on the
    insertion order of a dict that a policy file happened to write.
    """

    items = sorted((key, value) for key, value in weights.items() if value > 0.0)
    if not items:
        return ""
    total = sum(value for _, value in items)
    mark = rng.random() * total
    running = 0.0
    for key, value in items:
        running += value
        if mark <= running:
            return key
    return items[-1][0]


def _composition_gates(
    shot: DirectionInput, policy: VisualDirectionPolicy
) -> dict[str, str]:
    """Why each composition is or is not available to this shot.

    Returns the *blocked* ones with a reason; anything absent is allowed.
    """

    blocked: dict[str, str] = {}
    if shot.asset_type == "video":
        for name in _STILL_ONLY_COMPOSITIONS:
            blocked[name] = "moving footage is already a composition"
    if not shot.emphasis:
        blocked["text_focus"] = "no emphasis text on this shot"
    if shot.editorial_role not in _SPLIT_ROLES:
        blocked.setdefault("split", "beat is not a comparison")
    if shot.duration_seconds < policy.min_split_seconds:
        blocked.setdefault("split", "too short to read two frames")
    if shot.preserve_frame:
        blocked["extreme_crop"] = "nothing in this frame may be cut"
        blocked.setdefault("split", "nothing in this frame may be cut")
    return blocked


def _motion_gates(
    shot: DirectionInput, composition: str, policy: VisualDirectionPolicy
) -> dict[str, str]:
    blocked: dict[str, str] = {}
    if shot.asset_type == "video":
        for name in MOTIONS:
            if name != "static_hold":
                blocked[name] = "the footage supplies its own movement"
        return blocked
    if shot.duration_seconds < policy.min_motion_seconds:
        for name in MOTIONS:
            if name != "static_hold":
                blocked[name] = "too short for a move to read"
        return blocked
    if composition != "extreme_crop":
        blocked["detail_push"] = "detail_push only exists inside an extreme_crop"
    if composition in ("inset", "split"):
        # a move inside a framed inset reads as a wobble, not as direction
        for name in ("lateral_drift", "slow_pull_out"):
            blocked[name] = f"{composition} holds its frame"
    return blocked


_SCALE_CROP_BIAS = MappingProxyType(
    {"wide": "center", "medium": "center", "close": "center", "detail": "center"}
)


def plan_visual_direction(
    shots: Sequence[DirectionInput],
    *,
    policy: VisualDirectionPolicy | None = None,
    seed: int = 0,
    plan_id: str = "visual-direction",
    shot_plan_id: str = "shot-plan",
    script_id: str = "script",
) -> VisualDirectionPlan:
    """Decide composition, motion, grade and motif for every shot.

    The algorithm is the same three steps for each decision: take the policy's
    weights for this shot's *visual role*, zero out anything a gate forbids or
    a repetition limit would break, then draw. What survives is a plan where
    every value is explainable — the rationale on each direction names the
    weight table, the gate and the limit that produced it.
    """

    policy = policy or DEFAULT_DIRECTION_POLICY
    if not shots:
        raise DirectionError("plan_visual_direction needs at least one shot")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise DirectionError("seed must be a non-negative integer")
    rng = random.Random(seed)

    compositions: list[str] = []
    motions: list[str] = []
    recent_motifs: list[str] = []
    out: list[VisualDirection] = []

    for shot in shots:
        if not isinstance(shot, DirectionInput):
            raise DirectionError("shots must contain DirectionInput values")
        role = shot.visual_role or _FALLBACK_ROLE
        if role not in VISUAL_ROLES:
            raise DirectionError(f"unknown visual_role: {role}")

        # --- composition ---------------------------------------------------- #
        gates = _composition_gates(shot, policy)
        weights = {
            name: (0.0 if name in gates else weight)
            for name, weight in policy.composition_weights[role].items()
        }
        limited: list[str] = []
        for name in list(weights):
            if weights[name] > 0.0 and not _runs_ok(
                compositions, name, policy.max_consecutive_same_composition
            ):
                weights[name] = 0.0
                limited.append(name)
        # A returning motif gets a different framing, so recurrence of subject
        # never becomes recurrence of shot.
        motif = policy.motif_for(shot.text)
        if motif is not None and motif in recent_motifs:
            for name in ("fullscreen",):
                if weights.get(name, 0.0) > 0.0:
                    weights[name] *= 0.5
        if shot.preserve_frame:
            # what fullscreen would letterbox, a treated band holds instead
            for name in ("layered", "inset"):
                if weights.get(name, 0.0) > 0.0:
                    weights[name] *= 2.5
        composition = _pick(rng, weights)
        if not composition:
            # Every option was gated or run-limited: fullscreen is always
            # renderable, so the plan degrades instead of failing.
            composition = "fullscreen"
            limited.append("all")

        # --- motion --------------------------------------------------------- #
        motion_gates = _motion_gates(shot, composition, policy)
        motion_weights = {
            name: (0.0 if name in motion_gates else weight)
            for name, weight in policy.motion_weights[role].items()
        }
        motion_limited: list[str] = []
        for name in list(motion_weights):
            if motion_weights[name] > 0.0 and not _runs_ok(
                motions, name, policy.max_consecutive_same_motion
            ):
                motion_weights[name] = 0.0
                motion_limited.append(name)
        motion = _pick(rng, motion_weights) or "static_hold"

        # --- grade ---------------------------------------------------------- #
        grade, grade_reason = policy.grade_for_luma(shot.asset_luma)

        # --- rationale ------------------------------------------------------ #
        bits = [
            f"role:{role}",
            f"comp:{composition}",
            f"motion:{motion}",
            f"grade:{grade} ({grade_reason})",
        ]
        if shot.visual_intent_class:
            bits.insert(1, f"intent:{shot.visual_intent_class}")
        if motif:
            bits.append(f"motif:{motif}")
        if gates:
            bits.append("gated:" + ",".join(sorted(gates)))
        if limited:
            bits.append("comp-run-limited:" + ",".join(sorted(set(limited))))
        if motion_limited:
            bits.append("motion-run-limited:" + ",".join(sorted(set(motion_limited))))

        out.append(
            VisualDirection(
                shot_id=shot.shot_id,
                composition=composition,
                motion=motion,
                grade=grade,
                emphasis=shot.emphasis,
                crop_bias=shot.crop_bias or _SCALE_CROP_BIAS.get(shot.scale, "center"),
                text_zone=(shot.text_zone or "top") if composition == "text_focus" else None,
                visual_motif=motif,
                visual_role=shot.visual_role,
                visual_intent_class=shot.visual_intent_class,
                rationale=" · ".join(bits),
            )
        )
        compositions.append(composition)
        motions.append(motion)
        if motif:
            recent_motifs.append(motif)
            del recent_motifs[:-3]

    return VisualDirectionPlan(
        plan_id=plan_id,
        shot_plan_id=shot_plan_id,
        script_id=script_id,
        seed=seed,
        policy=policy,
        directions=tuple(out),
    )


# --------------------------------------------------------------------------- #
# 6. the renderer-facing projection
# --------------------------------------------------------------------------- #
def grade_parameters(policy: VisualDirectionPolicy) -> dict[str, Any]:
    """The grade half of the policy, flattened for an EditPlan operation.

    The renderer needs numbers, not a domain object; this is the one place
    that translation happens, so the operation payload and the policy can
    never drift apart.
    """

    return {
        "style_id": policy.style_id,
        "grade": policy.grade.to_dict(),
        "intensities": dict(policy.grade_intensities),
        "background_blur_sigma": policy.background_blur_sigma,
        "background_darkening": policy.background_darkening,
        "inset_scale": policy.inset_scale,
        "extreme_crop_zoom": policy.extreme_crop_zoom,
        "split_gap_fraction": policy.split_gap_fraction,
        "scrim_opacity": policy.scrim_opacity,
        "scrim_height_fraction": policy.scrim_height_fraction,
        "push_travel": policy.push_travel,
        "detail_push_travel": policy.detail_push_travel,
        "drift_travel": policy.drift_travel,
    }
