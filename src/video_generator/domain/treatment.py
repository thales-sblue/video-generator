"""Editorial Image Editing v1 — how a shot is *cut*, not only how it is framed.

Visual Direction (:mod:`.direction`) gives every shot one framing, one move and
one grade for its whole length. That is enough to stop sixty photographs reading
as a slideshow of photographs, but a still asset held for five or six seconds
still has to earn that time, and one static framing plus on-screen type is not an
edit — it is a caption over a slide.

This module is the missing beat between direction and the renderer::

    beat -> asset -> visual_direction -> editorial_treatment -> render

An :class:`EditorialTreatment` says what an editor would have *done* to this
shot: hold it, push into it slowly, punch in hard, cut inside it from a wide
plate to a detail, freeze it on the number that lands. A treatment is one to
three :class:`TreatmentState` values — successive editorial states of the *same*
asset — plus the timing that splits the shot's window between them. The renderer
never invents any of this: :func:`treatment_segments` materialises the states as
ordinary consecutive segment operations, each with its own composition, crop,
motion and grade, and the existing ``video-sequence`` path renders them.

The grammar is deliberately small — nine treatments — and every one of them is a
deterministic function of the shot's editorial *intensity*, its *role* in the
argument, the *kind* of asset and its *duration*. Stability is a treatment, not
the absence of one: ``static_hold`` is drawn on purpose and the planner holds a
floor of deliberately still shots so the cut never turns into a zoom festival.

Pure stdlib. Depends on :mod:`.direction` for the composition / motion / grade
vocabularies (a treatment state is expressed in exactly those terms, so the
renderer needs no new primitive) and on :mod:`.typography` for the shared
``EDITORIAL_INTENSITIES`` scale. Nothing here imports :mod:`.planning`, an
adapter or any I/O.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from video_generator.domain.direction import (
    COMPOSITIONS,
    CROP_BIASES,
    GRADE_INTENSITIES,
    MOTIONS,
)
from video_generator.domain.editorial import EDITORIAL_ROLES
from video_generator.domain.typography import EDITORIAL_INTENSITIES

SCHEMA_VERSION = 1


class TreatmentError(ValueError):
    """Raised when an editorial-treatment contract or policy is invalid."""


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #
# The nine treatments. Each is a shape an editor cuts, not an effect a filter
# offers: the test for adding a tenth is "would an editor name this as a
# distinct move", not "can ffmpeg do it".
TREATMENTS = (
    # one state, no move — deliberate stability, the floor the variety guard
    # keeps a share of
    "static_hold",
    # one state, a slow push or pull — legible movement for an explanation
    "slow_push",
    # one state, a hard crop into the frame — a strong claim, discomfort
    "punch_in",
    # two states, same asset, the framing changed on a hard internal cut
    "reframe",
    # two states: a wide plate held, then a cut to a detail that pushes in
    "detail_reveal",
    # two states: an approach, then a held tighter frame — a fact, a number,
    # synced to a typographic peak
    "freeze_emphasis",
    # two states: wide and close (either order), hard internal cut, both held
    "two_state_cut",
    # one state, two regions of the same frame held against each other
    "split_compare",
    # three states: normal, a very short hard cut-in, a reframed return —
    # a graphic interruption, coordinated with the type layer
    "graphic_interrupt",
)

# Treatments that need more than one editorial state.
_MULTI_STATE = ("reframe", "detail_reveal", "freeze_emphasis", "two_state_cut", "graphic_interrupt")

# Treatments a still frame can carry but moving footage cannot without fighting
# the camera that is already in the shot.
_STILL_ONLY = ("slow_push", "punch_in", "reframe", "detail_reveal", "freeze_emphasis", "split_compare", "graphic_interrupt")

# What a video shot is allowed to do: hold, or cut once between two windows of
# its own footage. Never a synthetic zoom over a moving picture.
_VIDEO_TREATMENTS = ("static_hold", "two_state_cut")

# The treatments that are always renderable, in order of preference: the planner
# degrades along this list rather than failing.
_FALLBACK_ORDER = ("static_hold", "slow_push", "punch_in")

# The coarse scale a state sits at. Mirrors the shot-plan scale vocabulary so a
# state reads in the same terms as the shot it belongs to.
STATE_SCALES = ("wide", "medium", "close", "detail")

# Which shots read as "calm" for the variety floor: a held or slowly moving
# single state.
_CALM_TREATMENTS = ("static_hold", "slow_push")


# --------------------------------------------------------------------------- #
# small helpers (kept local, as every domain module does)
# --------------------------------------------------------------------------- #
def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TreatmentError(f"{name} must be a non-empty string")
    return value.strip()


def _number(value: object, name: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TreatmentError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise TreatmentError(f"{name} must lie in [{low}, {high}]")
    return value


def _positive_int(value: object, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TreatmentError(f"{name} must be an integer")
    if value < minimum:
        raise TreatmentError(f"{name} must be at least {minimum}")
    return value


def _to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


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
    """A seeded weighted draw over a *sorted* vocabulary."""

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


# --------------------------------------------------------------------------- #
# 1. one editorial state of a shot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TreatmentState:
    """One state a shot passes through: a framing, a move, a slice of time.

    ``grade`` is ``None`` when the state keeps the shot's Visual Direction grade;
    a value overrides it (a graphic interruption pulls its cut-in frame down
    hard, for one beat only). ``duration_fraction`` is this state's share of the
    shot's own window — the fractions of a treatment sum to 1.
    """

    composition: str
    motion: str
    scale: str
    duration_fraction: float
    crop_bias: str = "center"
    grade: str | None = None
    hold: bool = False
    reading: bool = False
    # A deliberately sub-second state — the cut-in of a graphic interruption.
    # It is held to ``min_interrupt_seconds`` instead of ``min_state_seconds``.
    brief: bool = False
    rationale: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.composition not in COMPOSITIONS:
            raise TreatmentError("composition must be one of " + ", ".join(COMPOSITIONS))
        if self.motion not in MOTIONS:
            raise TreatmentError("motion must be one of " + ", ".join(MOTIONS))
        if self.scale not in STATE_SCALES:
            raise TreatmentError("scale must be one of " + ", ".join(STATE_SCALES))
        object.__setattr__(
            self,
            "duration_fraction",
            _number(self.duration_fraction, "duration_fraction", low=0.02, high=1.0),
        )
        if self.crop_bias not in CROP_BIASES:
            raise TreatmentError("crop_bias must be one of " + ", ".join(CROP_BIASES))
        if self.grade is not None and self.grade not in GRADE_INTENSITIES:
            raise TreatmentError("grade must be null or one of " + ", ".join(GRADE_INTENSITIES))
        for name in ("hold", "reading", "brief"):
            if not isinstance(getattr(self, name), bool):
                raise TreatmentError(f"{name} must be a boolean")
        if self.motion == "detail_push" and self.composition != "extreme_crop":
            raise TreatmentError("detail_push only exists inside an extreme_crop")
        if self.hold and self.motion != "static_hold":
            raise TreatmentError("a held state cannot also carry a move")
        if not isinstance(self.rationale, str):
            raise TreatmentError("rationale must be a string")
        object.__setattr__(self, "rationale", self.rationale.strip())
        if self.schema_version != SCHEMA_VERSION:
            raise TreatmentError(f"schema_version must be {SCHEMA_VERSION}")

    @property
    def moves(self) -> bool:
        return self.motion != "static_hold"

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition": self.composition,
            "motion": self.motion,
            "scale": self.scale,
            "duration_fraction": self.duration_fraction,
            "crop_bias": self.crop_bias,
            "grade": self.grade,
            "hold": self.hold,
            "reading": self.reading,
            "brief": self.brief,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TreatmentState":
        if not isinstance(data, Mapping):
            raise TreatmentError("a state payload must be an object")
        required = {"composition", "motion", "scale", "duration_fraction"}
        optional = {"crop_bias", "grade", "hold", "reading", "brief", "rationale", "schema_version"}
        missing = required - set(data)
        unknown = set(data) - required - optional
        if missing:
            raise TreatmentError(f"state missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise TreatmentError(f"state has unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            composition=data["composition"],
            motion=data["motion"],
            scale=data["scale"],
            duration_fraction=data["duration_fraction"],
            crop_bias=data.get("crop_bias", "center"),
            grade=data.get("grade"),
            hold=bool(data.get("hold", False)),
            reading=bool(data.get("reading", False)),
            brief=bool(data.get("brief", False)),
            rationale=data.get("rationale", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


# --------------------------------------------------------------------------- #
# 2. the per-shot treatment
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EditorialTreatment:
    """How one shot is cut: a named move and the states it passes through."""

    shot_id: str
    treatment: str
    intensity: str
    states: tuple[TreatmentState, ...]
    fallback: str = "static_hold"
    rationale: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        if self.treatment not in TREATMENTS:
            raise TreatmentError("treatment must be one of " + ", ".join(TREATMENTS))
        if self.intensity not in EDITORIAL_INTENSITIES:
            raise TreatmentError(
                "intensity must be one of " + ", ".join(EDITORIAL_INTENSITIES)
            )
        states = tuple(self.states)
        if not states or len(states) > 3:
            raise TreatmentError("a treatment holds between one and three states")
        if not all(isinstance(s, TreatmentState) for s in states):
            raise TreatmentError("states must be TreatmentState values")
        object.__setattr__(self, "states", states)
        want_multi = self.treatment in _MULTI_STATE
        if want_multi and len(states) < 2:
            raise TreatmentError(f"{self.treatment} needs at least two states")
        if not want_multi and len(states) != 1:
            raise TreatmentError(f"{self.treatment} is a single-state treatment")
        total = sum(s.duration_fraction for s in states)
        if abs(total - 1.0) > 1e-6:
            raise TreatmentError("state duration fractions must sum to 1")
        if self.fallback not in TREATMENTS:
            raise TreatmentError("fallback must be one of " + ", ".join(TREATMENTS))
        if self.fallback in _MULTI_STATE:
            raise TreatmentError("fallback must be a single-state treatment")
        if not isinstance(self.rationale, str):
            raise TreatmentError("rationale must be a string")
        object.__setattr__(self, "rationale", self.rationale.strip())
        if self.schema_version != SCHEMA_VERSION:
            raise TreatmentError(f"schema_version must be {SCHEMA_VERSION}")

    @property
    def is_static(self) -> bool:
        return self.treatment == "static_hold"

    @property
    def is_calm(self) -> bool:
        return self.treatment in _CALM_TREATMENTS

    @property
    def internal_cuts(self) -> int:
        return len(self.states) - 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shot_id": self.shot_id,
            "treatment": self.treatment,
            "intensity": self.intensity,
            "fallback": self.fallback,
            "rationale": self.rationale,
            "states": [s.to_dict() for s in self.states],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialTreatment":
        if not isinstance(data, Mapping):
            raise TreatmentError("a treatment payload must be an object")
        required = {"shot_id", "treatment", "intensity", "states"}
        optional = {"schema_version", "fallback", "rationale"}
        missing = required - set(data)
        unknown = set(data) - required - optional
        if missing:
            raise TreatmentError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise TreatmentError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            shot_id=data["shot_id"],
            treatment=data["treatment"],
            intensity=data["intensity"],
            states=tuple(TreatmentState.from_dict(s) for s in data["states"]),
            fallback=data.get("fallback", "static_hold"),
            rationale=data.get("rationale", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class EditorialTreatmentPlan:
    """Every shot's treatment, plus the policy and seed that produced them."""

    plan_id: str
    shot_plan_id: str
    script_id: str
    seed: int
    policy: "EditorialTreatmentPolicy"
    treatments: tuple[EditorialTreatment, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("plan_id", "shot_plan_id", "script_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise TreatmentError("seed must be a non-negative integer")
        if not isinstance(self.policy, EditorialTreatmentPolicy):
            raise TreatmentError("policy must be an EditorialTreatmentPolicy")
        treatments = tuple(self.treatments)
        if not treatments or not all(
            isinstance(t, EditorialTreatment) for t in treatments
        ):
            raise TreatmentError("treatments must contain at least one EditorialTreatment")
        ids = [t.shot_id for t in treatments]
        if len(set(ids)) != len(ids):
            raise TreatmentError("shot_id values must be unique")
        object.__setattr__(self, "treatments", treatments)
        if self.schema_version != SCHEMA_VERSION:
            raise TreatmentError(f"schema_version must be {SCHEMA_VERSION}")

    def by_shot(self) -> dict[str, EditorialTreatment]:
        return {t.shot_id: t for t in self.treatments}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "shot_plan_id": self.shot_plan_id,
            "script_id": self.script_id,
            "seed": self.seed,
            "policy": self.policy.to_dict(),
            "treatments": [t.to_dict() for t in self.treatments],
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialTreatmentPlan":
        if not isinstance(data, Mapping):
            raise TreatmentError("a treatment plan payload must be an object")
        required = {
            "schema_version", "plan_id", "shot_plan_id", "script_id", "seed",
            "policy", "treatments",
        }
        missing = required - set(data)
        unknown = set(data) - required
        if missing:
            raise TreatmentError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise TreatmentError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            plan_id=data["plan_id"],
            shot_plan_id=data["shot_plan_id"],
            script_id=data["script_id"],
            seed=data["seed"],
            policy=EditorialTreatmentPolicy.from_dict(data["policy"]),
            treatments=tuple(
                EditorialTreatment.from_dict(t) for t in data["treatments"]
            ),
            schema_version=data["schema_version"],
        )


# --------------------------------------------------------------------------- #
# 3. the policy — the channel's editing hand
# --------------------------------------------------------------------------- #
# The bias per editorial intensity. Weights on a seeded draw, not rules: the
# gates below still forbid what an asset cannot carry, and the variety guard
# still breaks a run. Read down a column to see how the hand hardens as the beat
# gets louder — ``low`` is almost all stillness, ``peak`` is almost all cutting.
_DEFAULT_INTENSITY_WEIGHTS: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "low": MappingProxyType(
            {
                "static_hold": 6.0, "slow_push": 3.0, "reframe": 0.6,
                "punch_in": 0.3, "detail_reveal": 0.2, "two_state_cut": 0.2,
                "freeze_emphasis": 0.0, "split_compare": 0.0, "graphic_interrupt": 0.0,
            }
        ),
        "medium": MappingProxyType(
            {
                "slow_push": 3.0, "reframe": 3.0, "static_hold": 1.6,
                "detail_reveal": 1.8, "punch_in": 1.6, "two_state_cut": 1.3,
                "freeze_emphasis": 0.6, "split_compare": 0.4, "graphic_interrupt": 0.2,
            }
        ),
        "high": MappingProxyType(
            {
                "detail_reveal": 3.2, "punch_in": 2.8, "reframe": 2.4,
                "two_state_cut": 2.0, "freeze_emphasis": 1.6, "static_hold": 0.7,
                "slow_push": 1.2, "split_compare": 0.8, "graphic_interrupt": 0.7,
            }
        ),
        "peak": MappingProxyType(
            {
                "detail_reveal": 2.6, "two_state_cut": 2.4, "punch_in": 2.4,
                "graphic_interrupt": 2.4, "freeze_emphasis": 2.0, "reframe": 1.6,
                "split_compare": 0.9, "static_hold": 0.6, "slow_push": 0.5,
            }
        ),
    }
)

# A nudge added to the intensity weight for a shot whose beat plays a particular
# role in the argument. Small numbers: the role bends the draw, it does not
# decide it.
_DEFAULT_ROLE_NUDGES: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "hook": MappingProxyType({"punch_in": 1.4, "detail_reveal": 1.8, "reframe": 1.0}),
        "open_loop": MappingProxyType({"slow_push": 1.0, "detail_reveal": 1.0, "reframe": 0.8}),
        "claim": MappingProxyType({"punch_in": 1.6, "reframe": 1.2, "detail_reveal": 1.0}),
        "evidence": MappingProxyType({"freeze_emphasis": 1.8, "detail_reveal": 1.6}),
        "contrast": MappingProxyType({"split_compare": 2.2, "two_state_cut": 1.4}),
        "consequence": MappingProxyType({"punch_in": 1.2, "detail_reveal": 1.0, "two_state_cut": 0.8}),
        "payoff": MappingProxyType({"two_state_cut": 1.6, "punch_in": 1.6, "detail_reveal": 1.2, "graphic_interrupt": 1.0}),
        "transition": MappingProxyType({"static_hold": 2.5, "slow_push": 1.5}),
        "close": MappingProxyType({"static_hold": 1.5, "slow_push": 1.2, "two_state_cut": 0.8}),
    }
)


@dataclass(frozen=True, slots=True)
class EditorialTreatmentPolicy:
    """The channel's editing hand: what the grammar may do, and how hard.

    Lives in the project (``editorial-treatment-v1.json``), never in the domain,
    so a second channel is a second file rather than a second code path.
    """

    style_id: str = "editorial-treatment-v1"
    intensity_weights: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: _DEFAULT_INTENSITY_WEIGHTS
    )
    role_nudges: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: _DEFAULT_ROLE_NUDGES
    )
    # Below this a shot is one state whatever the draw wanted: a cut inside a
    # window this short reads as a glitch, not an edit.
    min_multi_state_seconds: float = 3.0
    # No editorial state may be shorter than this once the fractions are applied
    # to real seconds.
    min_state_seconds: float = 1.0
    # The very short cut-in of a graphic interruption, as a fraction of the shot.
    interrupt_fraction: float = 0.16
    min_interrupt_seconds: float = 0.32
    max_interrupt_seconds: float = 0.7
    # Variety. A run longer than this of the same treatment, or of any cutting
    # (non-calm) treatment, is broken by zeroing those weights for the shot.
    max_consecutive_same_treatment: int = 2
    max_consecutive_cutting: int = 4
    # The share of shots that must stay deliberately calm (held or slow). When
    # the trailing window drops below it, only calm treatments are drawn.
    min_calm_fraction: float = 0.22
    calm_window: int = 7
    # A shot carrying a large typographic reading moment pulls its motion back:
    # aggressive treatments downgrade unless the beat is a peak.
    soften_on_reading: bool = True
    # Geometry of a state, as the zoom applied to a "close" / "detail" state.
    close_zoom: float = 1.28
    detail_zoom: float = 1.6
    schema_version: int = SCHEMA_VERSION

    _FRACTIONS = MappingProxyType(
        {
            "min_multi_state_seconds": (1.5, 12.0),
            "min_state_seconds": (0.4, 4.0),
            "interrupt_fraction": (0.04, 0.4),
            "min_interrupt_seconds": (0.1, 1.5),
            "max_interrupt_seconds": (0.3, 2.0),
            "min_calm_fraction": (0.0, 0.8),
            "close_zoom": (1.05, 2.0),
            "detail_zoom": (1.2, 3.0),
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "style_id", _text(self.style_id, "style_id"))
        object.__setattr__(
            self,
            "intensity_weights",
            _weight_table(
                self.intensity_weights, "intensity_weights", EDITORIAL_INTENSITIES
            ),
        )
        object.__setattr__(
            self,
            "role_nudges",
            _nudge_table(self.role_nudges, "role_nudges"),
        )
        for name, (low, high) in self._FRACTIONS.items():
            object.__setattr__(
                self, name, _number(getattr(self, name), name, low=low, high=high)
            )
        if self.min_state_seconds * 2 > self.min_multi_state_seconds + 1e-9:
            raise TreatmentError(
                "min_multi_state_seconds must fit at least two min_state_seconds"
            )
        if self.min_interrupt_seconds > self.max_interrupt_seconds:
            raise TreatmentError("min_interrupt_seconds must not exceed max_interrupt_seconds")
        if self.detail_zoom <= self.close_zoom:
            raise TreatmentError("detail_zoom must exceed close_zoom")
        _positive_int(
            self.max_consecutive_same_treatment, "max_consecutive_same_treatment"
        )
        _positive_int(self.max_consecutive_cutting, "max_consecutive_cutting")
        _positive_int(self.calm_window, "calm_window")
        if not isinstance(self.soften_on_reading, bool):
            raise TreatmentError("soften_on_reading must be a boolean")
        if self.schema_version != SCHEMA_VERSION:
            raise TreatmentError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "style_id": self.style_id,
            "intensity_weights": {
                level: dict(weights)
                for level, weights in self.intensity_weights.items()
            },
            "role_nudges": {
                role: dict(weights) for role, weights in self.role_nudges.items()
            },
            "min_multi_state_seconds": self.min_multi_state_seconds,
            "min_state_seconds": self.min_state_seconds,
            "interrupt_fraction": self.interrupt_fraction,
            "min_interrupt_seconds": self.min_interrupt_seconds,
            "max_interrupt_seconds": self.max_interrupt_seconds,
            "max_consecutive_same_treatment": self.max_consecutive_same_treatment,
            "max_consecutive_cutting": self.max_consecutive_cutting,
            "min_calm_fraction": self.min_calm_fraction,
            "calm_window": self.calm_window,
            "soften_on_reading": self.soften_on_reading,
            "close_zoom": self.close_zoom,
            "detail_zoom": self.detail_zoom,
        }

    def to_json(self) -> str:
        return _to_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialTreatmentPolicy":
        if not isinstance(data, Mapping):
            raise TreatmentError("policy payload must be an object")
        defaults = cls()
        allowed = set(defaults.to_dict())
        unknown = set(data) - allowed
        if unknown:
            raise TreatmentError(f"unknown policy fields: {', '.join(sorted(unknown))}")
        if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise TreatmentError(f"schema_version must be {SCHEMA_VERSION}")
        merged: dict[str, Any] = defaults.to_dict()
        merged.pop("schema_version", None)
        for key, value in data.items():
            if key == "schema_version":
                continue
            merged[key] = value
        # Weight tables merge per level / per role so a project may retune one
        # without restating the rest.
        merged_iw = {
            level: dict(weights)
            for level, weights in defaults.intensity_weights.items()
        }
        given_iw = merged["intensity_weights"]
        if not isinstance(given_iw, Mapping):
            raise TreatmentError("intensity_weights must be an object")
        for level, weights in given_iw.items():
            if level not in EDITORIAL_INTENSITIES:
                raise TreatmentError(f"intensity_weights has an unknown level: {level}")
            if not isinstance(weights, Mapping):
                raise TreatmentError(f"intensity_weights[{level}] must be an object")
            for key, weight in weights.items():
                if key not in TREATMENTS:
                    raise TreatmentError(
                        f"intensity_weights[{level}] has an unknown treatment: {key}"
                    )
                merged_iw.setdefault(level, {})[key] = weight
        merged["intensity_weights"] = merged_iw

        merged_rn = {
            role: dict(weights) for role, weights in defaults.role_nudges.items()
        }
        given_rn = merged["role_nudges"]
        if not isinstance(given_rn, Mapping):
            raise TreatmentError("role_nudges must be an object")
        for role, weights in given_rn.items():
            if role not in EDITORIAL_ROLES:
                raise TreatmentError(f"role_nudges has an unknown role: {role}")
            if not isinstance(weights, Mapping):
                raise TreatmentError(f"role_nudges[{role}] must be an object")
            for key, weight in weights.items():
                if key not in TREATMENTS:
                    raise TreatmentError(
                        f"role_nudges[{role}] has an unknown treatment: {key}"
                    )
                merged_rn.setdefault(role, {})[key] = weight
        merged["role_nudges"] = merged_rn
        return cls(**merged)


def _weight_table(
    value: object, name: str, levels: Sequence[str]
) -> Mapping[str, Mapping[str, float]]:
    if not isinstance(value, Mapping):
        raise TreatmentError(f"{name} must be an object")
    table: dict[str, Mapping[str, float]] = {}
    for level, weights in value.items():
        if level not in levels:
            raise TreatmentError(f"{name} has an unknown level: {level}")
        if not isinstance(weights, Mapping):
            raise TreatmentError(f"{name}[{level}] must be an object")
        row: dict[str, float] = {}
        for key, weight in weights.items():
            if key not in TREATMENTS:
                raise TreatmentError(f"{name}[{level}] has an unknown treatment: {key}")
            row[key] = _number(weight, f"{name}[{level}][{key}]", low=0.0, high=100.0)
        table[level] = MappingProxyType(row)
    missing = set(levels) - set(table)
    if missing:
        raise TreatmentError(f"{name} is missing levels: {', '.join(sorted(missing))}")
    return MappingProxyType(table)


def _nudge_table(value: object, name: str) -> Mapping[str, Mapping[str, float]]:
    if not isinstance(value, Mapping):
        raise TreatmentError(f"{name} must be an object")
    table: dict[str, Mapping[str, float]] = {}
    for role, weights in value.items():
        if role not in EDITORIAL_ROLES:
            raise TreatmentError(f"{name} has an unknown role: {role}")
        if not isinstance(weights, Mapping):
            raise TreatmentError(f"{name}[{role}] must be an object")
        row: dict[str, float] = {}
        for key, weight in weights.items():
            if key not in TREATMENTS:
                raise TreatmentError(f"{name}[{role}] has an unknown treatment: {key}")
            row[key] = _number(weight, f"{name}[{role}][{key}]", low=0.0, high=100.0)
        table[role] = MappingProxyType(row)
    return MappingProxyType(table)


DEFAULT_TREATMENT_POLICY = EditorialTreatmentPolicy()


# --------------------------------------------------------------------------- #
# 4. the planner input
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TreatmentInput:
    """One shot, reduced to exactly what the treatment planner may read.

    A deliberate seam: the planner never imports the shot plan, so the same
    decisions replay from a table in a test without building a whole plan.
    """

    shot_id: str
    duration_seconds: float
    asset_type: str
    scale: str
    intensity: str
    editorial_role: str | None = None
    visual_role: str | None = None
    visual_intent_class: str | None = None
    base_composition: str = "fullscreen"
    base_motion: str = "static_hold"
    base_grade: str = "standard"
    crop_bias: str = "center"
    # A large typographic block is read over this shot.
    reading_moment: bool = False
    # The type layer cut a graphic interruption in on this shot.
    graphic_interruption: bool = False
    # Nothing in the frame may be cropped away (a document, a graphic, a screen).
    preserve_frame: bool = False
    # The neighbouring shot is the other half of a contrast.
    contrast_neighbor: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        object.__setattr__(
            self,
            "duration_seconds",
            _number(self.duration_seconds, "duration_seconds", low=0.0, high=3600.0),
        )
        if self.asset_type not in ("image", "video"):
            raise TreatmentError("asset_type must be image or video")
        if self.scale not in ("wide", "medium", "close", "detail"):
            # the shot-plan scale vocabulary; a foreign value is a bug upstream
            raise TreatmentError("scale must be wide, medium, close or detail")
        if self.intensity not in EDITORIAL_INTENSITIES:
            raise TreatmentError(
                "intensity must be one of " + ", ".join(EDITORIAL_INTENSITIES)
            )
        if self.editorial_role is not None and self.editorial_role not in EDITORIAL_ROLES:
            raise TreatmentError("editorial_role must be a known editorial role")
        if self.base_composition not in COMPOSITIONS:
            raise TreatmentError("base_composition must be a known composition")
        if self.base_motion not in MOTIONS:
            raise TreatmentError("base_motion must be a known motion")
        if self.base_grade not in GRADE_INTENSITIES:
            raise TreatmentError("base_grade must be a known grade intensity")
        if self.crop_bias not in CROP_BIASES:
            raise TreatmentError("crop_bias must be one of " + ", ".join(CROP_BIASES))
        for name in (
            "reading_moment", "graphic_interruption", "preserve_frame",
            "contrast_neighbor",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TreatmentError(f"{name} must be a boolean")


# --------------------------------------------------------------------------- #
# 5. eligibility
# --------------------------------------------------------------------------- #
_AGGRESSIVE = ("punch_in", "detail_reveal", "graphic_interrupt")


def _gates(shot: TreatmentInput, policy: EditorialTreatmentPolicy) -> dict[str, str]:
    """Why each treatment is or is not available to this shot.

    Returns the *blocked* treatments with a reason; anything absent is allowed.
    """

    blocked: dict[str, str] = {}
    if shot.asset_type == "video":
        for name in TREATMENTS:
            if name not in _VIDEO_TREATMENTS:
                blocked[name] = "moving footage supplies its own camera"
    if shot.preserve_frame:
        # anything that crops or re-scales loses the information a document,
        # graphic or screen exists to show — only a hold or a gentle push
        for name in (
            "punch_in", "detail_reveal", "graphic_interrupt", "split_compare",
            "reframe", "freeze_emphasis", "two_state_cut",
        ):
            blocked.setdefault(name, "nothing in this frame may be cropped")
    if shot.duration_seconds < policy.min_multi_state_seconds:
        for name in _MULTI_STATE:
            blocked.setdefault(name, "too short to cut inside")
    if not shot.contrast_neighbor and shot.editorial_role != "contrast":
        blocked.setdefault("split_compare", "beat is not a comparison")
    if not shot.graphic_interruption:
        blocked.setdefault("graphic_interrupt", "type layer cut no interruption here")
    if shot.reading_moment and policy.soften_on_reading and shot.intensity != "peak":
        for name in _AGGRESSIVE:
            blocked.setdefault(name, "a reading moment holds the frame still")
    return blocked


# --------------------------------------------------------------------------- #
# 6. state materialisation
# --------------------------------------------------------------------------- #
_SCALE_ORDER = {name: index for index, name in enumerate(STATE_SCALES)}


def _tighter(scale: str) -> str:
    return STATE_SCALES[min(_SCALE_ORDER[scale] + 2, len(STATE_SCALES) - 1)]


def _one_step_tighter(scale: str) -> str:
    return STATE_SCALES[min(_SCALE_ORDER[scale] + 1, len(STATE_SCALES) - 1)]


def _wider(scale: str) -> str:
    return STATE_SCALES[max(_SCALE_ORDER[scale] - 1, 0)]


def _hold_state(shot: TreatmentInput, *, scale: str, why: str, reading: bool) -> TreatmentState:
    return TreatmentState(
        composition="fullscreen" if scale in ("wide", "medium") else "extreme_crop",
        motion="static_hold",
        scale=scale,
        duration_fraction=1.0,
        crop_bias=shot.crop_bias,
        hold=scale in ("close", "detail"),
        reading=reading,
        rationale=why,
    )


def _split_fraction_pair(policy: EditorialTreatmentPolicy, duration: float) -> tuple[float, float]:
    """A two-state split biased slightly toward the first state, both above
    the minimum."""

    floor = policy.min_state_seconds / duration if duration > 0 else 0.4
    first = max(0.5, min(0.62, 1.0 - floor))
    first = max(first, floor)
    second = 1.0 - first
    if second < floor:
        first, second = 1.0 - floor, floor
    return first, second


def _build_states(
    treatment: str,
    shot: TreatmentInput,
    policy: EditorialTreatmentPolicy,
) -> tuple[TreatmentState, ...]:
    reading = shot.reading_moment
    base_scale = shot.scale
    if treatment == "static_hold":
        return (_hold_state(shot, scale=base_scale, why="deliberate stability", reading=reading),)
    if treatment == "slow_push":
        pull = shot.visual_role == "context" or shot.editorial_role in ("close", "transition")
        return (
            TreatmentState(
                composition="fullscreen",
                motion="slow_pull_out" if pull else "slow_push_in",
                scale=base_scale,
                duration_fraction=1.0,
                crop_bias=shot.crop_bias,
                reading=reading,
                rationale="legible single move for an explanation",
            ),
        )
    if treatment == "punch_in":
        return (
            TreatmentState(
                composition="extreme_crop",
                motion="static_hold" if reading else "detail_push",
                scale=_tighter(base_scale),
                duration_fraction=1.0,
                crop_bias=shot.crop_bias,
                hold=reading,
                reading=reading,
                rationale="a hard crop into the frame for a strong claim",
            ),
        )
    if treatment == "reframe":
        first, second = _split_fraction_pair(policy, shot.duration_seconds)
        other_bias = _reframe_bias(shot.crop_bias)
        return (
            TreatmentState(
                composition="fullscreen",
                motion="static_hold",
                scale=base_scale,
                duration_fraction=first,
                crop_bias=shot.crop_bias,
                reading=reading,
                rationale="the frame as planned",
            ),
            TreatmentState(
                # extreme_crop, not fullscreen: a reframe has to be *visible*
                # on 16:9 material, where a fullscreen crop bias moves nothing
                composition="extreme_crop",
                motion="static_hold",
                scale=_one_step_tighter(base_scale),
                duration_fraction=second,
                crop_bias=other_bias,
                rationale="hard cut to a new framing of the same asset",
            ),
        )
    if treatment == "detail_reveal":
        first, second = _split_fraction_pair(policy, shot.duration_seconds)
        return (
            TreatmentState(
                composition="fullscreen",
                motion="static_hold",
                scale=_wider(base_scale),
                duration_fraction=first,
                crop_bias=shot.crop_bias,
                reading=reading,
                rationale="the wide plate, held",
            ),
            TreatmentState(
                composition="extreme_crop",
                motion="static_hold" if reading else "detail_push",
                scale="detail",
                duration_fraction=second,
                crop_bias=shot.crop_bias,
                hold=reading,
                rationale="cut to the detail the beat is about",
            ),
        )
    if treatment == "freeze_emphasis":
        first, second = _split_fraction_pair(policy, shot.duration_seconds)
        return (
            TreatmentState(
                composition="fullscreen",
                motion="slow_push_in",
                scale=base_scale,
                duration_fraction=first,
                crop_bias=shot.crop_bias,
                rationale="the approach",
            ),
            TreatmentState(
                # always a small punch as it locks, so the freeze reads as a
                # decision and not just the push running out
                composition="extreme_crop",
                motion="static_hold",
                scale=_one_step_tighter(base_scale),
                duration_fraction=second,
                crop_bias=shot.crop_bias,
                hold=True,
                reading=True,
                rationale="held on the fact, synced to the type",
            ),
        )
    if treatment == "two_state_cut":
        first, second = _split_fraction_pair(policy, shot.duration_seconds)
        # one state open, one clearly tight — and the tight one is always an
        # extreme_crop so the internal cut is visible even on 16:9 material.
        close_first = _SCALE_ORDER[base_scale] >= _SCALE_ORDER["close"]
        open_state = TreatmentState(
            composition="fullscreen",
            motion="static_hold",
            scale=_wider(base_scale) if close_first else base_scale,
            duration_fraction=second if close_first else first,
            crop_bias=shot.crop_bias,
            reading=reading,
            rationale="the open framing, held",
        )
        tight_state = TreatmentState(
            composition="extreme_crop",
            motion="static_hold",
            scale="detail" if close_first else "close",
            duration_fraction=first if close_first else second,
            crop_bias=shot.crop_bias if close_first else _reframe_bias(shot.crop_bias),
            hold=True,
            rationale="hard internal cut to a tight framing of the same asset",
        )
        return (tight_state, open_state) if close_first else (open_state, tight_state)
    if treatment == "split_compare":
        return (
            TreatmentState(
                composition="split",
                motion="static_hold",
                scale=base_scale,
                duration_fraction=1.0,
                crop_bias=shot.crop_bias,
                reading=reading,
                rationale="two regions of the frame held against each other",
            ),
        )
    if treatment == "graphic_interrupt":
        interrupt = max(
            policy.min_interrupt_seconds,
            min(policy.max_interrupt_seconds, policy.interrupt_fraction * shot.duration_seconds),
        )
        frac_mid = interrupt / shot.duration_seconds if shot.duration_seconds > 0 else policy.interrupt_fraction
        frac_mid = min(frac_mid, 0.4)
        rest = (1.0 - frac_mid) / 2.0
        return (
            TreatmentState(
                composition="fullscreen",
                motion="static_hold",
                scale=base_scale,
                duration_fraction=rest,
                crop_bias=shot.crop_bias,
                reading=reading,
                rationale="the shot before the interruption",
            ),
            TreatmentState(
                composition="extreme_crop",
                motion="static_hold",
                scale="detail",
                duration_fraction=frac_mid,
                crop_bias=shot.crop_bias,
                grade="strong",
                hold=True,
                brief=True,
                rationale="a hard graphic cut-in, one beat only",
            ),
            TreatmentState(
                composition="fullscreen",
                motion="static_hold",
                scale=_one_step_tighter(base_scale),
                duration_fraction=rest,
                crop_bias=_reframe_bias(shot.crop_bias),
                rationale="reframed return after the interruption",
            ),
        )
    raise TreatmentError(f"no state recipe for treatment {treatment!r}")


_OPPOSITE_BIAS = MappingProxyType(
    {"center": "left", "left": "right", "right": "left", "top": "bottom", "bottom": "top"}
)


def _reframe_bias(bias: str) -> str:
    return _OPPOSITE_BIAS.get(bias, "center")


def _viable(states: Sequence[TreatmentState], duration: float, policy: EditorialTreatmentPolicy) -> bool:
    for state in states:
        floor = policy.min_interrupt_seconds if state.brief else policy.min_state_seconds
        if state.duration_fraction * duration < floor - 1e-6:
            return False
    return True


# --------------------------------------------------------------------------- #
# 7. the planner
# --------------------------------------------------------------------------- #
def plan_editorial_treatment(
    shots: Sequence[TreatmentInput],
    *,
    policy: EditorialTreatmentPolicy | None = None,
    seed: int = 0,
    plan_id: str = "editorial-treatment",
    shot_plan_id: str = "shot-plan",
    script_id: str = "script",
) -> EditorialTreatmentPlan:
    """Decide the editorial treatment of every shot.

    For each shot: take the policy's weights for its intensity, add the role
    nudge, zero anything a gate forbids or a repetition / calm guard would
    break, draw, then materialise the states and check they fit in real
    seconds. A treatment that cannot be built degrades along ``_FALLBACK_ORDER``
    rather than failing. Every value is explainable: the rationale on each
    treatment names the intensity, the role, the gates and the guards that
    produced it.
    """

    policy = policy or DEFAULT_TREATMENT_POLICY
    if not shots:
        raise TreatmentError("plan_editorial_treatment needs at least one shot")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise TreatmentError("seed must be a non-negative integer")
    rng = random.Random(seed)

    chosen: list[str] = []
    calm_flags: list[bool] = []
    out: list[EditorialTreatment] = []

    for shot in shots:
        if not isinstance(shot, TreatmentInput):
            raise TreatmentError("shots must contain TreatmentInput values")

        weights = {
            name: float(policy.intensity_weights[shot.intensity].get(name, 0.0))
            for name in TREATMENTS
        }
        role = shot.editorial_role
        nudged: list[str] = []
        if role and role in policy.role_nudges:
            for name, bonus in policy.role_nudges[role].items():
                if weights.get(name, 0.0) > 0.0 or bonus > 0.0:
                    weights[name] = weights.get(name, 0.0) + bonus
                    nudged.append(name)
        if shot.contrast_neighbor:
            weights["split_compare"] = weights.get("split_compare", 0.0) + 1.5
            weights["two_state_cut"] = weights.get("two_state_cut", 0.0) + 1.0

        gates = _gates(shot, policy)
        for name in list(weights):
            if name in gates:
                weights[name] = 0.0

        # recent-use penalty: a treatment seen in the last few shots is not
        # forbidden, only made less likely, so the cut varies without a rule
        recent = chosen[-3:]
        for name in list(weights):
            if weights[name] <= 0.0:
                continue
            if name in _CALM_TREATMENTS:
                # calm may repeat, but two in a row still gets a light nudge
                if recent[-2:] == [name, name]:
                    weights[name] *= 0.7
                continue
            if recent and name == recent[-1]:
                weights[name] *= 0.3
            elif name in recent:
                weights[name] *= 0.55

        # repetition guard
        run_limited: list[str] = []
        for name in list(weights):
            if weights[name] > 0.0 and not _runs_ok(
                chosen, name, policy.max_consecutive_same_treatment
            ):
                weights[name] = 0.0
                run_limited.append(name)

        # calm floor: if the trailing window is short on calm shots, only calm
        # treatments may be drawn here
        calm_forced = False
        window = calm_flags[-policy.calm_window :]
        if len(window) >= policy.calm_window:
            calm_share = sum(1 for flag in window if flag) / len(window)
            if calm_share < policy.min_calm_fraction:
                calm_forced = True
                for name in list(weights):
                    if name not in _CALM_TREATMENTS:
                        weights[name] = 0.0
        # never allow a longer run of pure cutting than the policy tolerates
        cutting_run = 0
        for flag in reversed(calm_flags):
            if flag:
                break
            cutting_run += 1
        if cutting_run >= policy.max_consecutive_cutting:
            for name in list(weights):
                if name not in _CALM_TREATMENTS:
                    weights[name] = 0.0
            calm_forced = True

        treatment = _pick(rng, weights)
        degraded: list[str] = []
        if not treatment:
            treatment = "static_hold"
            degraded.append("all-gated")

        states = _build_states(treatment, shot, policy)
        while not _viable(states, shot.duration_seconds, policy) and treatment not in _FALLBACK_ORDER:
            degraded.append(treatment)
            treatment = next(
                (
                    name
                    for name in _FALLBACK_ORDER
                    if name not in gates and name not in run_limited
                ),
                "static_hold",
            )
            states = _build_states(treatment, shot, policy)
        if not _viable(states, shot.duration_seconds, policy):
            treatment = "static_hold"
            states = _build_states(treatment, shot, policy)
            degraded.append("state-too-short")

        bits = [f"intensity:{shot.intensity}", f"treatment:{treatment}"]
        if role:
            bits.insert(1, f"role:{role}")
        if nudged:
            bits.append("nudged:" + ",".join(sorted(set(nudged))))
        if gates:
            bits.append("gated:" + ",".join(sorted(gates)))
        if run_limited:
            bits.append("run-limited:" + ",".join(sorted(set(run_limited))))
        if calm_forced:
            bits.append("calm-floor")
        if degraded:
            bits.append("degraded-from:" + ",".join(degraded))

        fallback = next(
            (name for name in _FALLBACK_ORDER if name not in gates),
            "static_hold",
        )
        editorial = EditorialTreatment(
            shot_id=shot.shot_id,
            treatment=treatment,
            intensity=shot.intensity,
            states=states,
            fallback=fallback,
            rationale=" · ".join(bits),
        )
        out.append(editorial)
        chosen.append(treatment)
        calm_flags.append(editorial.is_calm)

    return EditorialTreatmentPlan(
        plan_id=plan_id,
        shot_plan_id=shot_plan_id,
        script_id=script_id,
        seed=seed,
        policy=policy,
        treatments=tuple(out),
    )


# --------------------------------------------------------------------------- #
# 8. the renderer-facing projection
# --------------------------------------------------------------------------- #
def treatment_segments(
    treatment: EditorialTreatment,
    *,
    asset_type: str,
    total_seconds: float,
    base_composition: str,
    base_crop_bias: str,
    base_grade: str,
    base_motion: str,
    base_text_zone: str | None = None,
    fit: str | None = None,
    window_start: float = 0.0,
) -> tuple[dict[str, Any], ...]:
    """Materialise a treatment as consecutive segment-operation parameter sets.

    Each returned dict is what one ``image_clip`` / ``sequence_clip`` operation
    needs: for an image, ``duration_seconds`` plus the framing keys; for a
    video, an absolute ``start_seconds`` / ``end_seconds`` window into the
    source. A state that leaves a field unset inherits the shot's Visual
    Direction value, so the projection can never emit a partial framing.
    """

    if not isinstance(treatment, EditorialTreatment):
        raise TreatmentError("treatment_segments needs an EditorialTreatment")
    if asset_type not in ("image", "video"):
        raise TreatmentError("asset_type must be image or video")
    total = _number(total_seconds, "total_seconds", low=1e-3, high=3600.0)

    # real seconds per state, kept at full float precision so the expanded
    # segments sum to exactly the shot's window — the type layer and the
    # manifest both check event end against the summed timeline, and a rounded
    # split there is how a whole render fails validation on a millisecond.
    fractions = [state.duration_fraction for state in treatment.states]
    seconds = [frac * total for frac in fractions[:-1]]
    seconds.append(total - sum(seconds))

    out: list[dict[str, Any]] = []
    cursor = window_start
    for state, secs in zip(treatment.states, seconds):
        composition = _state_composition(state, base_composition, asset_type)
        params: dict[str, Any] = {
            "composition": composition,
            "crop_bias": state.crop_bias or base_crop_bias,
            "grade": state.grade or base_grade,
        }
        motion = _state_motion(state, base_motion, asset_type)
        if base_text_zone is not None and composition == "text_focus":
            params["text_zone"] = base_text_zone
        if fit is not None:
            params["fit"] = fit
        if asset_type == "image":
            params["duration_seconds"] = secs
            params["motion"] = motion
        else:
            params["start_seconds"] = cursor
            params["end_seconds"] = cursor + secs
            cursor += secs
        out.append(params)
    return tuple(out)


def _state_composition(state: TreatmentState, base: str, asset_type: str) -> str:
    if asset_type == "video":
        # a video sub-window keeps the cheap half of the grammar
        if state.composition in ("inset", "layered", "split"):
            return "fullscreen"
        return state.composition if state.composition in ("fullscreen", "extreme_crop", "text_focus") else base
    return state.composition


def _state_motion(state: TreatmentState, base: str, asset_type: str) -> str | None:
    if asset_type == "video":
        return None
    if state.reading and state.moves:
        # a reading state keeps only the gentlest move
        return "slow_push_in" if state.motion in ("detail_push", "slow_push_in") else "static_hold"
    return state.motion


# --------------------------------------------------------------------------- #
# 9. plan-level metrics
# --------------------------------------------------------------------------- #
def treatment_metrics(
    plan: EditorialTreatmentPlan,
    *,
    shot_seconds: Mapping[str, float] | None = None,
    static_image_seconds_threshold: float = 4.0,
    asset_types: Mapping[str, str] | None = None,
    reuse_of: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Plan-level numbers for judging the cut, not for dressing it up.

    ``shot_seconds`` / ``asset_types`` / ``reuse_of`` are optional; passing them
    fills in the duration-aware metrics (untouched long stills, editorial reuse
    of the same source), otherwise those read ``None``.
    """

    treatments = plan.treatments
    total = len(treatments)
    by_treatment: dict[str, int] = {name: 0 for name in TREATMENTS}
    by_intensity: dict[str, int] = {name: 0 for name in EDITORIAL_INTENSITIES}
    intensity_by_treatment: dict[str, dict[str, int]] = {
        name: {level: 0 for level in EDITORIAL_INTENSITIES} for name in TREATMENTS
    }
    internal_cuts = 0
    treated = 0
    deliberately_static = 0
    names = [t.treatment for t in treatments]
    consecutive_same = sum(1 for a, b in zip(names, names[1:]) if a == b)

    longest_untouched_run = 0
    run = 0
    for t in treatments:
        by_treatment[t.treatment] += 1
        by_intensity[t.intensity] += 1
        intensity_by_treatment[t.treatment][t.intensity] += 1
        internal_cuts += t.internal_cuts
        if t.treatment == "static_hold":
            deliberately_static += 1
        if t.treatment != "static_hold":
            treated += 1
        if t.is_calm:
            run += 1
            longest_untouched_run = max(longest_untouched_run, run)
        else:
            run = 0

    seconds = dict(shot_seconds or {})
    total_seconds = sum(seconds.values()) if seconds else 0.0
    editorial_events = sum(1 + t.internal_cuts for t in treatments if not t.is_static)
    events_per_minute = (
        round(editorial_events / (total_seconds / 60.0), 2) if total_seconds else None
    )

    static_long = None
    if seconds and asset_types:
        static_long = [
            t.shot_id
            for t in treatments
            if asset_types.get(t.shot_id) == "image"
            and t.treatment == "static_hold"
            and seconds.get(t.shot_id, 0.0) > static_image_seconds_threshold
        ]

    editorial_reuse = None
    if reuse_of is not None:
        editorial_reuse = sum(
            1
            for t in treatments
            if reuse_of.get(t.shot_id) is None and t.internal_cuts > 0
        )

    longest_static_seconds = None
    if seconds:
        longest_static_seconds = 0.0
        acc = 0.0
        for t in treatments:
            if t.is_calm:
                acc += seconds.get(t.shot_id, 0.0)
                longest_static_seconds = max(longest_static_seconds, acc)
            else:
                acc = 0.0

    return {
        "shots": total,
        "treated_shots": treated,
        "treated_fraction": round(treated / total, 4) if total else 0.0,
        "deliberately_static_shots": deliberately_static,
        "deliberately_static_fraction": round(deliberately_static / total, 4) if total else 0.0,
        "internal_state_changes": internal_cuts,
        "editorial_events": editorial_events,
        "editorial_events_per_minute": events_per_minute,
        "by_treatment": {k: v for k, v in by_treatment.items() if v},
        "by_intensity": {k: v for k, v in by_intensity.items() if v},
        "intensity_by_treatment": {
            name: {lvl: n for lvl, n in levels.items() if n}
            for name, levels in intensity_by_treatment.items()
            if any(levels.values())
        },
        "consecutive_same_treatment": consecutive_same,
        "longest_calm_run_shots": longest_untouched_run,
        "longest_calm_run_seconds": (
            round(longest_static_seconds, 2) if longest_static_seconds is not None else None
        ),
        "static_image_shots_over_threshold": static_long,
        "editorial_reuse_of_same_asset": editorial_reuse,
    }
