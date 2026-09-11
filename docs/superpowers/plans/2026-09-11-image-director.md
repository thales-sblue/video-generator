# Reference-image creative direction ("image director") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-production stage that turns a reference image into an approved `Storyboard`, then compiles it into a `ShotPlan`/`AssetRequirements` pair the existing `resolve-assets` pipeline already consumes.

**Architecture:** Three new pure domain modules (`reference_dna.py`, `creative_brief.py`, `storyboard.py`) hold the contracts; a small FFmpeg-only probe adds deterministic image signals; a `planning.py` bridge function compiles an approved storyboard into `ShotPlan`; five new CLI checkpoint commands validate/render/lock/hand off — none of them "generate" creative content, since that is always the orchestrator's authored judgment, never a code path.

**Tech Stack:** Python 3.12 stdlib only (domain), FFmpeg/ffprobe via subprocess (adapter), argparse (CLI). No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-11-image-director-design.md](../specs/2026-09-11-image-director-design.md)

## Global Constraints

- Domain modules (`reference_dna.py`, `creative_brief.py`, `storyboard.py`) are stdlib-only, no I/O, no imports from adapters/cli.
- No vision-model/image-processing dependency (no PIL, OpenCV, CLIP). Only FFmpeg/ffprobe subprocess calls, matching the existing `measure_luma` pattern.
- No CLI command "analyzes" or "generates" creative content algorithmically — every command validates a JSON artifact I (the orchestrator) authored, renders it for human review, or bridges to the next stage.
- `additionalProperties: false` on every new/edited JSON Schema; `schema_version` is `const 1`.
- Every write refuses to overwrite an existing output file unless `--force` (except a freely-regenerated `.md` review file, which is always rewritten).
- `--project <slug>` / `--out-dir <dir>` stay mutually exclusive on every command that writes into a project directory, reusing the existing `_review_out_dir` helper.
- `json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"`, `encoding="utf-8"` for every JSON write.
- No changes to `resolve-assets`, `EditPlan`, `RenderManifest`, or the renderer. `Shot` gains two additive optional fields only (`visual_anchor`, `continuity_constraints`).
- Full test suite (`$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`) must stay green after every task.
- Anchored shots (using the reference image) are always `asset_type = "image"` — the reference is a still photo, never a video.

---

## Task 1: `domain/reference_dna.py` — VisualDNA, CharacterBible, ReferenceImageSignals

**Files:**
- Create: `src/video_generator/domain/reference_dna.py`
- Test: `tests/test_reference_dna.py`

**Interfaces:**
- Produces: `ReferenceDNAError(ValueError)`, `IMAGE_TYPES: tuple[str, ...]`, `CHARACTER_IMAGE_TYPES: tuple[str, ...]`, `MOVEMENT_POTENTIAL_KEYS: tuple[str, ...]`, `ReferenceImageSignals(width, height, orientation, avg_luma, palette=(), schema_version=1)` with `.to_dict()`/`.from_dict()`, `CharacterBible(anatomy, proportions, eyes, mouth, limbs, colors, texture, accessories, base_expression, deformation_limits, forbidden, schema_version=1)` with `.to_dict()`/`.from_dict()`, `VisualDNA(image_type, subject, color, light, material, composition, mood, movement_potential, required_elements, flexible_elements, signals, character_bible=None, source_image_sha256=None, source_image_path=None, schema_version=1)` with `.to_dict()`/`.from_dict()`, `reference_dna_template(signals, *, source_image_sha256, source_image_path) -> dict`, `render_reference_dna_checklist(payload: Mapping) -> str`, `render_reference_dna_markdown(dna: VisualDNA) -> str`.

- [ ] **Step 1: Write the failing test file**

```python
"""tests/test_reference_dna.py"""
from __future__ import annotations

import unittest

from video_generator.domain.reference_dna import (
    CharacterBible,
    ReferenceDNAError,
    ReferenceImageSignals,
    VisualDNA,
    reference_dna_template,
    render_reference_dna_checklist,
    render_reference_dna_markdown,
)


def _signals(**overrides) -> ReferenceImageSignals:
    defaults = dict(
        width=800, height=600, orientation="landscape", avg_luma=0.42,
        palette=("#AA1122", "#00FF00"),
    )
    defaults.update(overrides)
    return ReferenceImageSignals(**defaults)


def _character_bible(**overrides) -> CharacterBible:
    defaults = dict(
        anatomy="bipedal frog-dinosaur hybrid",
        proportions="large head, short limbs",
        eyes="two, round, black pupils",
        mouth="wide, closed by default",
        limbs="four, short, three-fingered",
        colors="green body, yellow belly",
        texture="smooth rubbery skin",
        accessories="none",
        base_expression="curious, mouth closed",
        deformation_limits="stylized but anatomically consistent",
        forbidden=("extra eyes", "extra limbs", "tongue behind head", "species change"),
    )
    defaults.update(overrides)
    return CharacterBible(**defaults)


def _dna(**overrides) -> VisualDNA:
    defaults = dict(
        image_type="character",
        subject={"main": "pererekossauro", "expression": "curious"},
        color={"dominant": "#2E7D32", "secondary": "#FDD835", "accent": "#000000"},
        light={"direction": "front", "intensity": "soft"},
        material={},
        composition={"focal_point": "center"},
        mood=("comical", "cute"),
        movement_potential={"character": ("hop",), "camera": ("push_in",)},
        required_elements=("green skin", "round eyes", "closed mouth"),
        flexible_elements=("background", "pose"),
        signals=_signals(),
        character_bible=_character_bible(),
    )
    defaults.update(overrides)
    return VisualDNA(**defaults)


class ReferenceImageSignalsTests(unittest.TestCase):
    def test_round_trip(self):
        signals = _signals()
        self.assertEqual(ReferenceImageSignals.from_dict(signals.to_dict()), signals)

    def test_rejects_bad_orientation(self):
        with self.assertRaises(ReferenceDNAError):
            _signals(orientation="diagonal")

    def test_rejects_luma_out_of_range(self):
        with self.assertRaises(ReferenceDNAError):
            _signals(avg_luma=1.5)

    def test_allows_null_luma_and_empty_palette(self):
        signals = _signals(avg_luma=None, palette=())
        self.assertIsNone(signals.avg_luma)
        self.assertEqual(signals.palette, ())

    def test_rejects_bad_hex_colour(self):
        with self.assertRaises(ReferenceDNAError):
            _signals(palette=("not-a-colour",))


class CharacterBibleTests(unittest.TestCase):
    def test_round_trip(self):
        bible = _character_bible()
        self.assertEqual(CharacterBible.from_dict(bible.to_dict()), bible)

    def test_forbidden_must_not_be_empty(self):
        with self.assertRaises(ReferenceDNAError):
            _character_bible(forbidden=())

    def test_rejects_blank_field(self):
        with self.assertRaises(ReferenceDNAError):
            _character_bible(anatomy="   ")


class VisualDNATests(unittest.TestCase):
    def test_round_trip_with_character_bible(self):
        dna = _dna()
        self.assertEqual(VisualDNA.from_dict(dna.to_dict()), dna)

    def test_round_trip_without_character_bible(self):
        dna = _dna(image_type="landscape", character_bible=None)
        self.assertEqual(VisualDNA.from_dict(dna.to_dict()), dna)

    def test_character_image_type_requires_character_bible(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(character_bible=None)

    def test_non_character_image_type_forbids_character_bible(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(image_type="landscape")

    def test_stylized_ip_also_requires_character_bible(self):
        dna = _dna(image_type="stylized_ip")
        self.assertIsNotNone(dna.character_bible)

    def test_rejects_unknown_image_type(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(image_type="cartoon", character_bible=None)

    def test_rejects_empty_mood(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(mood=())

    def test_rejects_empty_required_elements(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(required_elements=())

    def test_allows_empty_flexible_elements_and_material(self):
        dna = _dna(flexible_elements=(), material={})
        self.assertEqual(dna.flexible_elements, ())

    def test_rejects_unknown_movement_potential_key(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(movement_potential={"teleport": ("blink",)})

    def test_rejects_non_json_safe_subject(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(subject={"main": object()})

    def test_optional_source_image_fields(self):
        dna = _dna(source_image_sha256="a" * 64, source_image_path="assets/reference/x.png")
        self.assertEqual(dna.source_image_sha256, "a" * 64)

    def test_rejects_bad_sha256(self):
        with self.assertRaises(ReferenceDNAError):
            _dna(source_image_sha256="not-hex")


class TemplateAndRenderingTests(unittest.TestCase):
    def test_template_has_placeholder_fields(self):
        template = reference_dna_template(
            _signals(), source_image_sha256="a" * 64, source_image_path="p.png"
        )
        self.assertEqual(template["image_type"], "")
        self.assertEqual(template["signals"]["width"], 800)
        self.assertIsNone(template["character_bible"])

    def test_checklist_mentions_every_required_field(self):
        template = reference_dna_template(
            _signals(), source_image_sha256="a" * 64, source_image_path="p.png"
        )
        checklist = render_reference_dna_checklist(template)
        for field_name in ("image_type", "subject", "color", "mood", "required_elements"):
            self.assertIn(field_name, checklist)

    def test_markdown_includes_character_bible(self):
        markdown = render_reference_dna_markdown(_dna())
        self.assertIn("Character bible", markdown)
        self.assertIn("forbidden", markdown)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_reference_dna -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'video_generator.domain.reference_dna'`

- [ ] **Step 3: Write the implementation**

```python
"""src/video_generator/domain/reference_dna.py

Reference-image visual DNA: what must stay visually consistent across every
shot that uses a reference image as its anchor. Pure stdlib, no I/O, no
adapters. The orchestrator (Claude Code) authors the content of a VisualDNA
by hand, looking at the image; this module only validates structure and
closed vocabularies. See
docs/superpowers/specs/2026-09-11-image-director-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

SCHEMA_VERSION = 1

IMAGE_TYPES = (
    "character", "photograph", "illustration", "product", "space",
    "landscape", "poster", "advertising", "stylized_ip", "mixed",
)
CHARACTER_IMAGE_TYPES = ("character", "stylized_ip")
MOVEMENT_POTENTIAL_KEYS = (
    "character", "micro", "camera", "light", "ambient", "transitions",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReferenceDNAError(ValueError):
    """Raised when reference-image DNA data is invalid."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceDNAError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    text = _text(value, name)
    if not _SHA256_RE.fullmatch(text.lower()):
        raise ReferenceDNAError(f"{name} must be a 64-character hex sha256")
    return text.lower()


def _string_tuple(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ReferenceDNAError(f"{name} must be a list of strings")
    items = tuple(value)
    if not allow_empty and not items:
        raise ReferenceDNAError(f"{name} must not be empty")
    if any(not isinstance(v, str) or not v.strip() for v in items):
        raise ReferenceDNAError(f"{name} entries must be non-empty strings")
    return tuple(v.strip() for v in items)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _json_object(value: object, name: str, *, allow_empty: bool = False) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReferenceDNAError(f"{name} must be an object")
    if not value and not allow_empty:
        raise ReferenceDNAError(f"{name} must not be empty")
    try:
        json.dumps(value)
    except TypeError as exc:
        raise ReferenceDNAError(f"{name} must be JSON-serializable") from exc
    return _freeze(value)


def _hex_colour(value: object, name: str) -> str:
    text = _text(value, name)
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise ReferenceDNAError(f"{name} must be a #RRGGBB colour")
    return text.upper()
```

- [ ] **Step 4: Continue the implementation — commit after this task's final step**

Append to the same file:

```python
@dataclass(frozen=True, slots=True)
class ReferenceImageSignals:
    width: int
    height: int
    orientation: str
    avg_luma: float | None
    palette: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width <= 0:
            raise ReferenceDNAError("width must be a positive integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height <= 0:
            raise ReferenceDNAError("height must be a positive integer")
        if self.orientation not in ("landscape", "portrait", "square"):
            raise ReferenceDNAError("orientation must be landscape, portrait or square")
        if self.avg_luma is not None:
            if isinstance(self.avg_luma, bool) or not isinstance(self.avg_luma, (int, float)):
                raise ReferenceDNAError("avg_luma must be a number or null")
            luma = float(self.avg_luma)
            if not 0.0 <= luma <= 1.0:
                raise ReferenceDNAError("avg_luma must be in [0, 1]")
            object.__setattr__(self, "avg_luma", luma)
        palette = tuple(self.palette)
        for colour in palette:
            _hex_colour(colour, "palette entry")
        object.__setattr__(self, "palette", tuple(c.upper() for c in palette))
        if self.schema_version != SCHEMA_VERSION:
            raise ReferenceDNAError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation,
            "avg_luma": self.avg_luma,
            "palette": list(self.palette),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReferenceImageSignals":
        if not isinstance(data, Mapping):
            raise ReferenceDNAError("signals payload must be an object")
        required = {"width", "height", "orientation", "avg_luma"}
        optional = {"schema_version", "palette"}
        missing = required - set(data)
        unknown = set(data) - required - optional
        if missing:
            raise ReferenceDNAError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ReferenceDNAError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            width=data["width"],
            height=data["height"],
            orientation=data["orientation"],
            avg_luma=data["avg_luma"],
            palette=tuple(data.get("palette", ())),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
```

- [ ] **Step 5: Continue the implementation**

Append `CharacterBible`:

```python
_CHARACTER_BIBLE_FIELDS = (
    "anatomy", "proportions", "eyes", "mouth", "limbs", "colors", "texture",
    "accessories", "base_expression", "deformation_limits",
)


@dataclass(frozen=True, slots=True)
class CharacterBible:
    anatomy: str
    proportions: str
    eyes: str
    mouth: str
    limbs: str
    colors: str
    texture: str
    accessories: str
    base_expression: str
    deformation_limits: str
    forbidden: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in _CHARACTER_BIBLE_FIELDS:
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(self, "forbidden", _string_tuple(self.forbidden, "forbidden"))
        if self.schema_version != SCHEMA_VERSION:
            raise ReferenceDNAError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in _CHARACTER_BIBLE_FIELDS}
        payload["forbidden"] = list(self.forbidden)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterBible":
        if not isinstance(data, Mapping):
            raise ReferenceDNAError("a character bible must be an object")
        required = set(_CHARACTER_BIBLE_FIELDS) | {"forbidden"}
        unknown = set(data) - required
        missing = required - set(data)
        if missing:
            raise ReferenceDNAError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ReferenceDNAError(f"unknown fields: {', '.join(sorted(unknown))}")
        kwargs = {name: data[name] for name in _CHARACTER_BIBLE_FIELDS}
        kwargs["forbidden"] = tuple(data["forbidden"])
        return cls(**kwargs)
```

- [ ] **Step 6: Continue the implementation**

Append `VisualDNA`:

```python
@dataclass(frozen=True, slots=True)
class VisualDNA:
    image_type: str
    subject: Mapping[str, Any]
    color: Mapping[str, Any]
    light: Mapping[str, Any]
    material: Mapping[str, Any]
    composition: Mapping[str, Any]
    mood: tuple[str, ...]
    movement_potential: Mapping[str, tuple[str, ...]]
    required_elements: tuple[str, ...]
    flexible_elements: tuple[str, ...]
    signals: ReferenceImageSignals
    character_bible: CharacterBible | None = None
    source_image_sha256: str | None = None
    source_image_path: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.image_type not in IMAGE_TYPES:
            raise ReferenceDNAError("image_type must be one of " + ", ".join(IMAGE_TYPES))
        object.__setattr__(self, "subject", _json_object(self.subject, "subject"))
        object.__setattr__(self, "color", _json_object(self.color, "color"))
        object.__setattr__(self, "light", _json_object(self.light, "light"))
        object.__setattr__(self, "material", _json_object(self.material, "material", allow_empty=True))
        object.__setattr__(self, "composition", _json_object(self.composition, "composition"))
        object.__setattr__(self, "mood", _string_tuple(self.mood, "mood"))
        movement = dict(self.movement_potential)
        if set(movement) - set(MOVEMENT_POTENTIAL_KEYS):
            raise ReferenceDNAError(
                "movement_potential keys must be a subset of " + ", ".join(MOVEMENT_POTENTIAL_KEYS)
            )
        frozen_movement = {
            key: _string_tuple(val, f"movement_potential[{key}]", allow_empty=True)
            for key, val in movement.items()
        }
        object.__setattr__(self, "movement_potential", MappingProxyType(frozen_movement))
        object.__setattr__(
            self, "required_elements", _string_tuple(self.required_elements, "required_elements")
        )
        object.__setattr__(
            self,
            "flexible_elements",
            _string_tuple(self.flexible_elements, "flexible_elements", allow_empty=True),
        )
        if not isinstance(self.signals, ReferenceImageSignals):
            raise ReferenceDNAError("signals must be a ReferenceImageSignals")
        needs_character = self.image_type in CHARACTER_IMAGE_TYPES
        if needs_character and self.character_bible is None:
            raise ReferenceDNAError(f"image_type {self.image_type!r} requires a character_bible")
        if not needs_character and self.character_bible is not None:
            raise ReferenceDNAError(f"image_type {self.image_type!r} must not carry a character_bible")
        if self.character_bible is not None and not isinstance(self.character_bible, CharacterBible):
            raise ReferenceDNAError("character_bible must be a CharacterBible")
        object.__setattr__(
            self, "source_image_sha256", _optional_sha256(self.source_image_sha256, "source_image_sha256")
        )
        object.__setattr__(
            self, "source_image_path", _optional_text(self.source_image_path, "source_image_path")
        )
        if self.schema_version != SCHEMA_VERSION:
            raise ReferenceDNAError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "image_type": self.image_type,
            "subject": dict(self.subject),
            "color": dict(self.color),
            "light": dict(self.light),
            "material": dict(self.material),
            "composition": dict(self.composition),
            "mood": list(self.mood),
            "movement_potential": {k: list(v) for k, v in self.movement_potential.items()},
            "required_elements": list(self.required_elements),
            "flexible_elements": list(self.flexible_elements),
            "signals": self.signals.to_dict(),
            "character_bible": self.character_bible.to_dict() if self.character_bible else None,
            "source_image_sha256": self.source_image_sha256,
            "source_image_path": self.source_image_path,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualDNA":
        if not isinstance(data, Mapping):
            raise ReferenceDNAError("a visual DNA payload must be an object")
        required = {
            "image_type", "subject", "color", "light", "material", "composition",
            "mood", "movement_potential", "required_elements", "flexible_elements",
            "signals",
        }
        optional = {"schema_version", "character_bible", "source_image_sha256", "source_image_path"}
        missing = required - set(data)
        unknown = set(data) - required - optional
        if missing:
            raise ReferenceDNAError(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ReferenceDNAError(f"unknown fields: {', '.join(sorted(unknown))}")
        character_bible = data.get("character_bible")
        return cls(
            image_type=data["image_type"],
            subject=data["subject"],
            color=data["color"],
            light=data["light"],
            material=data["material"],
            composition=data["composition"],
            mood=tuple(data["mood"]),
            movement_potential={k: tuple(v) for k, v in dict(data["movement_potential"]).items()},
            required_elements=tuple(data["required_elements"]),
            flexible_elements=tuple(data["flexible_elements"]),
            signals=ReferenceImageSignals.from_dict(data["signals"]),
            character_bible=CharacterBible.from_dict(character_bible) if character_bible is not None else None,
            source_image_sha256=data.get("source_image_sha256"),
            source_image_path=data.get("source_image_path"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
```

- [ ] **Step 7: Continue the implementation**

Append the template + rendering functions:

```python
_TEMPLATE_FIELDS = (
    "image_type", "subject", "color", "light", "material", "composition",
    "mood", "movement_potential", "required_elements", "flexible_elements",
)


def reference_dna_template(
    signals: ReferenceImageSignals, *, source_image_sha256: str, source_image_path: str
) -> dict[str, Any]:
    """A scaffold payload for a human/agent to fill in by hand.

    Deliberately invalid as a VisualDNA (every semantic field is empty) — it
    is meant to be edited before ``analyze-reference-image --from-dna``
    validates it, never constructed as a VisualDNA directly.
    """

    return {
        "schema_version": SCHEMA_VERSION,
        "image_type": "",
        "subject": {},
        "color": {},
        "light": {},
        "material": {},
        "composition": {},
        "mood": [],
        "movement_potential": {},
        "required_elements": [],
        "flexible_elements": [],
        "signals": signals.to_dict(),
        "character_bible": None,
        "source_image_sha256": source_image_sha256,
        "source_image_path": source_image_path,
    }


def render_reference_dna_checklist(payload: Mapping[str, Any]) -> str:
    lines = ["# Reference image DNA - fill in, then re-run with --from-dna", ""]
    for field_name in _TEMPLATE_FIELDS:
        value = payload.get(field_name)
        filled = bool(value)
        marker = "x" if filled else " "
        lines.append(f"- [{marker}] {field_name}")
    signals = payload.get("signals", {})
    lines.append("")
    lines.append("## Computed signals (do not edit)")
    lines.append(f"- size: {signals.get('width')}x{signals.get('height')} ({signals.get('orientation')})")
    lines.append(f"- avg_luma: {signals.get('avg_luma')}")
    lines.append(f"- palette: {', '.join(signals.get('palette', []))}")
    lines.append("")
    lines.append(
        "If image_type is 'character' or 'stylized_ip', also fill in "
        "'character_bible' (anatomy, proportions, eyes, mouth, limbs, colors, "
        "texture, accessories, base_expression, deformation_limits, forbidden)."
    )
    return "\n".join(lines) + "\n"


def render_reference_dna_markdown(dna: VisualDNA) -> str:
    lines = [
        f"# Reference image DNA ({dna.image_type})",
        "",
        f"Signals: {dna.signals.width}x{dna.signals.height} "
        f"({dna.signals.orientation}), avg_luma={dna.signals.avg_luma}, "
        f"palette={', '.join(dna.signals.palette)}",
        "",
        f"Mood: {', '.join(dna.mood)}",
        f"Required elements: {', '.join(dna.required_elements)}",
        f"Flexible elements: {', '.join(dna.flexible_elements) or '(none)'}",
        "",
    ]
    if dna.character_bible is not None:
        bible = dna.character_bible
        lines.append("## Character bible")
        for field_name in _CHARACTER_BIBLE_FIELDS:
            lines.append(f"- {field_name}: {getattr(bible, field_name)}")
        lines.append(f"- forbidden: {', '.join(bible.forbidden)}")
        lines.append("")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_reference_dna -v`
Expected: PASS (all tests green)

- [ ] **Step 9: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
git add src/video_generator/domain/reference_dna.py tests/test_reference_dna.py
git commit -m "Add VisualDNA/CharacterBible contracts for reference-image DNA"
```

---

## Task 2: `adapters/ffmpeg.py::probe_reference_image` — deterministic image signals

**Files:**
- Modify: `src/video_generator/adapters/ffmpeg.py` (append near `measure_luma`)
- Create: `tests/fixtures/generate_reference_image_small.py` (one-off generator, committed for reproducibility)
- Create: `tests/fixtures/reference_image_small.png` (committed binary fixture, 4x4 px, 4 flat colour quadrants)
- Modify: `tests/test_ffmpeg_integration.py` (append tests; this file already skips the whole module when the locked FFmpeg toolchain is absent)

**Interfaces:**
- Consumes: `video_generator.domain.reference_dna.ReferenceImageSignals`, existing `measure_luma`, `resolve_media_tool`, `FFmpegError`, `_probe_video_width`/`_probe_video_height` (module-private, same file).
- Produces: `probe_reference_image(source_path: str | Path) -> ReferenceImageSignals`.

- [ ] **Step 1: Write the fixture generator and generate the fixture**

```python
"""tests/fixtures/generate_reference_image_small.py

Regenerates reference_image_small.png: a 4x4 PNG with four flat 2x2 colour
quadrants, written with the stdlib only (zlib + struct), so the fixture is
reproducible without Pillow/OpenCV. Run once from the repo root:
    python tests/fixtures/generate_reference_image_small.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_COLOURS = ((200, 30, 30), (30, 160, 30), (30, 30, 200), (230, 200, 40))


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def write_png(path: Path, width: int, height: int, colours: tuple[tuple[int, int, int], ...]) -> None:
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            index = (0 if x < width // 2 else 1) + (0 if y < height // 2 else 2)
            row.extend(colours[index])
        rows.append(bytes(row))
    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    write_png(Path(__file__).with_name("reference_image_small.png"), 4, 4, _COLOURS)
```

- [ ] **Step 2: Run the generator to produce the fixture**

Run: `.\.venv\Scripts\python.exe tests\fixtures\generate_reference_image_small.py`
Expected: `tests/fixtures/reference_image_small.png` is created (a few hundred bytes)

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_ffmpeg_integration.py` (it already guards the whole module on the locked FFmpeg install — no new skip logic needed):

```python
from video_generator.adapters.ffmpeg import probe_reference_image
from video_generator.domain.reference_dna import ReferenceImageSignals

_REFERENCE_IMAGE = Path(__file__).parent / "fixtures" / "reference_image_small.png"


class ProbeReferenceImageTests(unittest.TestCase):
    def test_dimensions_and_orientation(self):
        signals = probe_reference_image(_REFERENCE_IMAGE)
        self.assertIsInstance(signals, ReferenceImageSignals)
        self.assertEqual(signals.width, 4)
        self.assertEqual(signals.height, 4)
        self.assertEqual(signals.orientation, "square")

    def test_avg_luma_is_in_range(self):
        signals = probe_reference_image(_REFERENCE_IMAGE)
        self.assertIsNotNone(signals.avg_luma)
        self.assertGreaterEqual(signals.avg_luma, 0.0)
        self.assertLessEqual(signals.avg_luma, 1.0)

    def test_palette_is_non_empty_and_valid_hex(self):
        signals = probe_reference_image(_REFERENCE_IMAGE)
        self.assertGreater(len(signals.palette), 0)
        for colour in signals.palette:
            self.assertRegex(colour, r"^#[0-9A-F]{6}$")

    def test_missing_file_raises(self):
        from video_generator.adapters.ffmpeg import FFmpegError

        with self.assertRaises(FFmpegError):
            probe_reference_image(Path("nope") / "missing.png")
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_ffmpeg_integration.ProbeReferenceImageTests -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'probe_reference_image'`

- [ ] **Step 5: Implement `probe_reference_image`**

Append to `src/video_generator/adapters/ffmpeg.py`, near `measure_luma`:

```python
def probe_reference_image(source_path: "str | Path") -> "ReferenceImageSignals":
    """Deterministic, FFmpeg-only signals for a reference image.

    Dimensions come from ffprobe, average brightness reuses
    :func:`measure_luma`, and a small dominant-colour palette comes from
    FFmpeg's own ``palettegen`` filter read back as raw RGB - no
    image-processing dependency. Raises :class:`FFmpegError` only when the
    file itself or its dimensions cannot be read; the palette degrades to an
    empty tuple on any FFmpeg failure, since it only seeds a template a human
    completes.
    """

    from video_generator.domain.reference_dna import ReferenceImageSignals

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FFmpegError(f"reference image does not exist: {source}")
    width = _probe_video_width(source)
    height = _probe_video_height(source)
    if width is None or height is None:
        raise FFmpegError(f"could not probe reference image dimensions: {source}")
    if width > height:
        orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"
    avg_luma = measure_luma(source)
    palette = _probe_palette(source)
    return ReferenceImageSignals(
        width=width, height=height, orientation=orientation, avg_luma=avg_luma, palette=palette,
    )


def _probe_palette(
    source: Path, *, max_colors: int = 5, timeout_seconds: float = 30
) -> tuple[str, ...]:
    try:
        executable = resolve_media_tool("ffmpeg", path_lookup=shutil.which)
    except ToolResolutionError:
        return ()
    if executable is None:
        return ()
    command = [
        executable, "-v", "error", "-nostdin",
        "-i", str(source),
        "-vf", f"palettegen=max_colors={max_colors}:stats_mode=full",
        "-frames:v", "1",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, timeout=timeout_seconds, shell=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return ()
    if completed.returncode != 0 or not completed.stdout:
        return ()
    data = completed.stdout
    colours: list[str] = []
    for i in range(0, len(data) - 2, 3):
        triple = data[i:i + 3]
        colour = "#{:02X}{:02X}{:02X}".format(triple[0], triple[1], triple[2])
        if colour not in colours:
            colours.append(colour)
        if len(colours) >= max_colors:
            break
    return tuple(colours)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_ffmpeg_integration -v`
Expected: PASS (all tests in the file, including the new ones)

- [ ] **Step 7: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 8: Commit**

```bash
git add src/video_generator/adapters/ffmpeg.py tests/test_ffmpeg_integration.py tests/fixtures/generate_reference_image_small.py tests/fixtures/reference_image_small.png
git commit -m "Add probe_reference_image: deterministic FFmpeg-only image signals"
```

---

## Task 3: `domain/creative_brief.py` — category, structure, three directions, boundary

**Files:**
- Create: `src/video_generator/domain/creative_brief.py`
- Test: `tests/test_creative_brief.py`

**Interfaces:**
- Produces: `CreativeBriefError(ValueError)`, `EXPANSION_BOUNDARIES = ("conservative", "moderate", "bold")`, `VideoCategoryOption(category, why, suggested_structure, suggested_duration_seconds, narration, dialogue, media_format)`, `StructureBeat(name, start_seconds, end_seconds)`, `NarrativeStructureOption(beats)` with `.total_duration_seconds`, `CreativeDirection(name, concept, emotion, action, expansion, camera_strategy, hook, climax, ending, why_it_works)`, `CreativeBrief(category_options, selected_category, structure, directions, selected_direction, expansion_boundary="moderate", schema_version=1)`, `render_creative_brief_markdown(brief) -> str`. All with `.to_dict()`/`.from_dict()`.

- [ ] **Step 1: Write the failing test file**

```python
"""tests/test_creative_brief.py"""
from __future__ import annotations

import unittest

from video_generator.domain.creative_brief import (
    CreativeBrief,
    CreativeBriefError,
    CreativeDirection,
    NarrativeStructureOption,
    StructureBeat,
    VideoCategoryOption,
    render_creative_brief_markdown,
)


def _category(**overrides) -> VideoCategoryOption:
    defaults = dict(
        category="short narrativo",
        why="a imagem sugere uma cena de ação contínua",
        suggested_structure="hook, desenvolvimento, clímax, payoff",
        suggested_duration_seconds=15.0,
        narration=True,
        dialogue=False,
        media_format="vertical 9:16",
    )
    defaults.update(overrides)
    return VideoCategoryOption(**defaults)


def _structure() -> NarrativeStructureOption:
    return NarrativeStructureOption(
        beats=(
            StructureBeat(name="hook", start_seconds=0.0, end_seconds=3.0),
            StructureBeat(name="desenvolvimento", start_seconds=3.0, end_seconds=9.0),
            StructureBeat(name="payoff", start_seconds=9.0, end_seconds=15.0),
        )
    )


def _direction(**overrides) -> CreativeDirection:
    defaults = dict(
        name="Fuga no parque",
        concept="o personagem foge de algo maior que ele",
        emotion="susto cômico",
        action="corrida em círculos",
        expansion="novo cenário: parque público",
        camera_strategy="handheld seguindo por trás",
        hook="close no rosto assustado",
        climax="tropeço e queda cômica",
        ending="olha para a câmera, sem graça",
        why_it_works="contraste entre escala do personagem e do perigo",
    )
    defaults.update(overrides)
    return CreativeDirection(**defaults)


def _brief(**overrides) -> CreativeBrief:
    defaults = dict(
        category_options=(_category(),),
        selected_category="short narrativo",
        structure=_structure(),
        directions=(_direction(name="A"), _direction(name="B"), _direction(name="C")),
        selected_direction="A",
        expansion_boundary="moderate",
    )
    defaults.update(overrides)
    return CreativeBrief(**defaults)


class VideoCategoryOptionTests(unittest.TestCase):
    def test_round_trip(self):
        option = _category()
        self.assertEqual(VideoCategoryOption.from_dict(option.to_dict()), option)

    def test_rejects_non_positive_duration(self):
        with self.assertRaises(CreativeBriefError):
            _category(suggested_duration_seconds=0)

    def test_rejects_non_bool_narration(self):
        with self.assertRaises(CreativeBriefError):
            _category(narration="yes")


class NarrativeStructureOptionTests(unittest.TestCase):
    def test_round_trip(self):
        structure = _structure()
        self.assertEqual(NarrativeStructureOption.from_dict(structure.to_dict()), structure)

    def test_total_duration(self):
        self.assertEqual(_structure().total_duration_seconds, 15.0)

    def test_rejects_gap_between_beats(self):
        with self.assertRaises(CreativeBriefError):
            NarrativeStructureOption(
                beats=(
                    StructureBeat(name="a", start_seconds=0.0, end_seconds=3.0),
                    StructureBeat(name="b", start_seconds=4.0, end_seconds=10.0),
                )
            )

    def test_rejects_first_beat_not_at_zero(self):
        with self.assertRaises(CreativeBriefError):
            NarrativeStructureOption(beats=(StructureBeat(name="a", start_seconds=1.0, end_seconds=3.0),))


class CreativeDirectionTests(unittest.TestCase):
    def test_round_trip(self):
        direction = _direction()
        self.assertEqual(CreativeDirection.from_dict(direction.to_dict()), direction)

    def test_rejects_blank_field(self):
        with self.assertRaises(CreativeBriefError):
            _direction(hook="   ")


class CreativeBriefTests(unittest.TestCase):
    def test_round_trip(self):
        brief = _brief()
        self.assertEqual(CreativeBrief.from_dict(brief.to_dict()), brief)

    def test_requires_exactly_three_directions(self):
        with self.assertRaises(CreativeBriefError):
            _brief(directions=(_direction(), _direction()))

    def test_selected_category_must_match_an_option(self):
        with self.assertRaises(CreativeBriefError):
            _brief(selected_category="nao existe")

    def test_selected_direction_accepts_combo(self):
        brief = _brief(selected_direction="A+B")
        self.assertEqual(brief.selected_direction, "A+B")

    def test_selected_direction_rejects_bad_format(self):
        with self.assertRaises(CreativeBriefError):
            _brief(selected_direction="D")

    def test_selected_direction_rejects_repeated_letter(self):
        with self.assertRaises(CreativeBriefError):
            _brief(selected_direction="A+A")

    def test_expansion_boundary_defaults_to_moderate(self):
        brief = CreativeBrief(
            category_options=(_category(),),
            selected_category="short narrativo",
            structure=_structure(),
            directions=(_direction(), _direction(), _direction()),
            selected_direction="A",
        )
        self.assertEqual(brief.expansion_boundary, "moderate")

    def test_rejects_unknown_expansion_boundary(self):
        with self.assertRaises(CreativeBriefError):
            _brief(expansion_boundary="extreme")


class RenderCreativeBriefMarkdownTests(unittest.TestCase):
    def test_marks_selected_category_and_direction(self):
        markdown = render_creative_brief_markdown(_brief())
        self.assertIn("(chosen)", markdown)
        self.assertIn("Selected direction: A", markdown)
        self.assertIn("Expansion boundary: moderate", markdown)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_creative_brief -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'video_generator.domain.creative_brief'`

- [ ] **Step 3: Write the implementation**

```python
"""src/video_generator/domain/creative_brief.py

Category options, chosen narrative structure, exactly three creative
directions, and the chosen expansion boundary for a reference-image-driven
video. Every value here is authored by the orchestrator by hand, in
conversation with the human approving each choice; this module only
validates structure and closed vocabularies. Kept separate from
domain/storyboard.py on purpose: the two evolve independently. See
docs/superpowers/specs/2026-09-11-image-director-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA_VERSION = 1
EXPANSION_BOUNDARIES = ("conservative", "moderate", "bold")
_SELECTED_DIRECTION_RE = re.compile(r"^[ABC](\+[ABC])?$")


class CreativeBriefError(ValueError):
    """Raised when creative-brief data is invalid."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CreativeBriefError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CreativeBriefError(f"{name} must be a number")
    number = float(value)
    if number <= 0:
        raise CreativeBriefError(f"{name} must be greater than 0")
    return number


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise CreativeBriefError(f"{name} must be a boolean")
    return value


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(data, Mapping):
        raise CreativeBriefError("payload must be an object")
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise CreativeBriefError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise CreativeBriefError(f"unknown fields: {', '.join(sorted(unknown))}")
```

- [ ] **Step 4: Continue the implementation**

Append `VideoCategoryOption`:

```python
_CATEGORY_FIELDS = (
    "category", "why", "suggested_structure", "suggested_duration_seconds",
    "narration", "dialogue", "media_format",
)


@dataclass(frozen=True, slots=True)
class VideoCategoryOption:
    category: str
    why: str
    suggested_structure: str
    suggested_duration_seconds: float
    narration: bool
    dialogue: bool
    media_format: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _text(self.category, "category"))
        object.__setattr__(self, "why", _text(self.why, "why"))
        object.__setattr__(
            self, "suggested_structure", _text(self.suggested_structure, "suggested_structure")
        )
        object.__setattr__(
            self,
            "suggested_duration_seconds",
            _positive_number(self.suggested_duration_seconds, "suggested_duration_seconds"),
        )
        object.__setattr__(self, "narration", _bool(self.narration, "narration"))
        object.__setattr__(self, "dialogue", _bool(self.dialogue, "dialogue"))
        object.__setattr__(self, "media_format", _text(self.media_format, "media_format"))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _CATEGORY_FIELDS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VideoCategoryOption":
        _keys(data, required=set(_CATEGORY_FIELDS))
        return cls(**{name: data[name] for name in _CATEGORY_FIELDS})
```

- [ ] **Step 5: Continue the implementation**

Append `StructureBeat` and `NarrativeStructureOption`:

```python
@dataclass(frozen=True, slots=True)
class StructureBeat:
    name: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        if isinstance(self.start_seconds, bool) or not isinstance(self.start_seconds, (int, float)):
            raise CreativeBriefError("start_seconds must be a number")
        if isinstance(self.end_seconds, bool) or not isinstance(self.end_seconds, (int, float)):
            raise CreativeBriefError("end_seconds must be a number")
        object.__setattr__(self, "start_seconds", float(self.start_seconds))
        object.__setattr__(self, "end_seconds", float(self.end_seconds))
        if self.start_seconds < 0:
            raise CreativeBriefError("start_seconds must be >= 0")
        if self.end_seconds <= self.start_seconds:
            raise CreativeBriefError("end_seconds must be greater than start_seconds")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "start_seconds": self.start_seconds, "end_seconds": self.end_seconds}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StructureBeat":
        _keys(data, required={"name", "start_seconds", "end_seconds"})
        return cls(name=data["name"], start_seconds=data["start_seconds"], end_seconds=data["end_seconds"])


@dataclass(frozen=True, slots=True)
class NarrativeStructureOption:
    beats: tuple[StructureBeat, ...]

    def __post_init__(self) -> None:
        beats = tuple(self.beats)
        if not beats or not all(isinstance(b, StructureBeat) for b in beats):
            raise CreativeBriefError("beats must contain at least one StructureBeat")
        object.__setattr__(self, "beats", beats)
        if beats[0].start_seconds != 0.0:
            raise CreativeBriefError("the first beat must start at 0")
        for previous, current in zip(beats, beats[1:]):
            if abs(current.start_seconds - previous.end_seconds) > 1e-9:
                raise CreativeBriefError("beats must be contiguous (no gap or overlap)")

    @property
    def total_duration_seconds(self) -> float:
        return self.beats[-1].end_seconds

    def to_dict(self) -> dict[str, Any]:
        return {"beats": [b.to_dict() for b in self.beats]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrativeStructureOption":
        _keys(data, required={"beats"})
        beats_data = data["beats"]
        if not isinstance(beats_data, list):
            raise CreativeBriefError("beats must be a list")
        return cls(beats=tuple(StructureBeat.from_dict(b) for b in beats_data))
```

- [ ] **Step 6: Continue the implementation**

Append `CreativeDirection`:

```python
_DIRECTION_FIELDS = (
    "name", "concept", "emotion", "action", "expansion", "camera_strategy",
    "hook", "climax", "ending", "why_it_works",
)


@dataclass(frozen=True, slots=True)
class CreativeDirection:
    name: str
    concept: str
    emotion: str
    action: str
    expansion: str
    camera_strategy: str
    hook: str
    climax: str
    ending: str
    why_it_works: str

    def __post_init__(self) -> None:
        for field_name in _DIRECTION_FIELDS:
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _DIRECTION_FIELDS}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreativeDirection":
        _keys(data, required=set(_DIRECTION_FIELDS))
        return cls(**{name: data[name] for name in _DIRECTION_FIELDS})
```

- [ ] **Step 7: Continue the implementation**

Append `CreativeBrief` and `render_creative_brief_markdown`:

```python
@dataclass(frozen=True, slots=True)
class CreativeBrief:
    category_options: tuple[VideoCategoryOption, ...]
    selected_category: str
    structure: NarrativeStructureOption
    directions: tuple[CreativeDirection, ...]
    selected_direction: str
    expansion_boundary: str = "moderate"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        options = tuple(self.category_options)
        if not options or not all(isinstance(o, VideoCategoryOption) for o in options):
            raise CreativeBriefError("category_options must contain at least one VideoCategoryOption")
        object.__setattr__(self, "category_options", options)
        object.__setattr__(self, "selected_category", _text(self.selected_category, "selected_category"))
        if self.selected_category not in {o.category for o in options}:
            raise CreativeBriefError("selected_category must match one of category_options")
        if not isinstance(self.structure, NarrativeStructureOption):
            raise CreativeBriefError("structure must be a NarrativeStructureOption")
        directions = tuple(self.directions)
        if len(directions) != 3 or not all(isinstance(d, CreativeDirection) for d in directions):
            raise CreativeBriefError("directions must contain exactly 3 CreativeDirection entries")
        object.__setattr__(self, "directions", directions)
        object.__setattr__(self, "selected_direction", _text(self.selected_direction, "selected_direction"))
        if not _SELECTED_DIRECTION_RE.fullmatch(self.selected_direction):
            raise CreativeBriefError(
                "selected_direction must look like 'A', 'B', 'C', 'A+B', 'B+C' or 'A+C'"
            )
        object.__setattr__(self, "expansion_boundary", _text(self.expansion_boundary, "expansion_boundary"))
        if self.expansion_boundary not in EXPANSION_BOUNDARIES:
            raise CreativeBriefError(f"expansion_boundary must be one of {EXPANSION_BOUNDARIES}")
        if self.schema_version != SCHEMA_VERSION:
            raise CreativeBriefError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "category_options": [o.to_dict() for o in self.category_options],
            "selected_category": self.selected_category,
            "structure": self.structure.to_dict(),
            "directions": [d.to_dict() for d in self.directions],
            "selected_direction": self.selected_direction,
            "expansion_boundary": self.expansion_boundary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CreativeBrief":
        _keys(
            data,
            required={"category_options", "selected_category", "structure", "directions", "selected_direction"},
            optional={"schema_version", "expansion_boundary"},
        )
        category_options = data["category_options"]
        directions = data["directions"]
        if not isinstance(category_options, list):
            raise CreativeBriefError("category_options must be a list")
        if not isinstance(directions, list):
            raise CreativeBriefError("directions must be a list")
        return cls(
            category_options=tuple(VideoCategoryOption.from_dict(o) for o in category_options),
            selected_category=data["selected_category"],
            structure=NarrativeStructureOption.from_dict(data["structure"]),
            directions=tuple(CreativeDirection.from_dict(d) for d in directions),
            selected_direction=data["selected_direction"],
            expansion_boundary=data.get("expansion_boundary", "moderate"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


def render_creative_brief_markdown(brief: CreativeBrief) -> str:
    lines = ["# Creative brief", "", "## Category options"]
    for option in brief.category_options:
        marker = " (chosen)" if option.category == brief.selected_category else ""
        lines.append(f"- **{option.category}**{marker} - {option.why}")
        lines.append(
            f"  structure: {option.suggested_structure}; "
            f"duration: {option.suggested_duration_seconds:.0f}s; "
            f"narration: {option.narration}; dialogue: {option.dialogue}; "
            f"format: {option.media_format}"
        )
    lines.append("")
    lines.append("## Structure")
    for beat in brief.structure.beats:
        lines.append(f"- {beat.start_seconds:.0f}-{beat.end_seconds:.0f}s: {beat.name}")
    lines.append("")
    lines.append("## Directions")
    letters = ("A", "B", "C")
    for letter, direction in zip(letters, brief.directions):
        marker = " (chosen)" if letter in brief.selected_direction else ""
        lines.append(f"### {letter} - {direction.name}{marker}")
        for field_name in _DIRECTION_FIELDS[1:]:
            lines.append(f"- {field_name}: {getattr(direction, field_name)}")
        lines.append("")
    lines.append(f"Selected direction: {brief.selected_direction}")
    lines.append(f"Expansion boundary: {brief.expansion_boundary}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_creative_brief -v`
Expected: PASS (all tests green)

- [ ] **Step 9: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
git add src/video_generator/domain/creative_brief.py tests/test_creative_brief.py
git commit -m "Add CreativeBrief contract: category, structure, 3 directions, boundary"
```

---

## Task 4: `domain/storyboard.py` — StoryboardShot, Storyboard, StoryboardLock

**Files:**
- Create: `src/video_generator/domain/storyboard.py`
- Test: `tests/test_storyboard.py`

**Interfaces:**
- Consumes: `video_generator.domain.reference_dna.VisualDNA` (type only, for `validate_against`).
- Produces: `StoryboardError(ValueError)`, `StoryboardShot(shot_id, time_start, time_end, purpose, subject, action, camera, framing, environment, lighting, movement, transition, audio_intent, expression=None, visual_anchor=None, continuity_constraints=())`, `Storyboard(storyboard_id, shots, selected_direction, expansion_boundary, schema_version=1)` with `.validate_against(visual_dna)` and `.total_duration_seconds`, `default_continuity_constraints(visual_dna) -> tuple[str, ...]`, `StoryboardLock(lock_id, approved_by, approved_at, reference_image_sha256, reference_dna_sha256, creative_brief_sha256, storyboard_sha256, schema_version=1)`, `lock_violations(lock, *, digest_of, paths) -> tuple[str, ...]`, `render_storyboard_markdown(storyboard, visual_dna) -> str`. All contracts have `.to_dict()`/`.from_dict()`.

- [ ] **Step 1: Write the failing test file**

```python
"""tests/test_storyboard.py"""
from __future__ import annotations

import unittest

from video_generator.domain.reference_dna import CharacterBible, ReferenceImageSignals, VisualDNA
from video_generator.domain.storyboard import (
    Storyboard,
    StoryboardError,
    StoryboardLock,
    StoryboardShot,
    default_continuity_constraints,
    lock_violations,
    render_storyboard_markdown,
)


def _signals() -> ReferenceImageSignals:
    return ReferenceImageSignals(width=800, height=600, orientation="landscape", avg_luma=0.4)


def _bible(**overrides) -> CharacterBible:
    defaults = dict(
        anatomy="bipedal frog-dinosaur hybrid", proportions="large head, short limbs",
        eyes="two, round", mouth="wide, closed by default", limbs="four, short",
        colors="green body, yellow belly", texture="smooth rubbery skin",
        accessories="none", base_expression="curious",
        deformation_limits="stylized but consistent",
        forbidden=("extra eyes", "extra limbs", "species change"),
    )
    defaults.update(overrides)
    return CharacterBible(**defaults)


def _dna(**overrides) -> VisualDNA:
    defaults = dict(
        image_type="character",
        subject={"main": "pererekossauro"},
        color={"dominant": "#2E7D32"},
        light={"direction": "front"},
        material={},
        composition={"focal_point": "center"},
        mood=("comical",),
        movement_potential={"character": ("hop",)},
        required_elements=("green skin", "round eyes"),
        flexible_elements=("background",),
        signals=_signals(),
        character_bible=_bible(),
    )
    defaults.update(overrides)
    return VisualDNA(**defaults)


def _shot(**overrides) -> StoryboardShot:
    defaults = dict(
        shot_id="shot_01",
        time_start=0.0,
        time_end=3.0,
        purpose="hook",
        subject="pererekossauro",
        action="olha para a camera",
        camera="close",
        framing="centered",
        environment="parque vazio",
        lighting="luz difusa de manha",
        movement="static",
        transition="corte seco",
        audio_intent="silencio tenso",
    )
    defaults.update(overrides)
    return StoryboardShot(**defaults)


def _anchored_shot(**overrides) -> StoryboardShot:
    defaults = dict(
        visual_anchor="pererekossauro",
        continuity_constraints=("green skin", "round eyes"),
    )
    defaults.update(overrides)
    return _shot(**defaults)


class StoryboardShotTests(unittest.TestCase):
    def test_round_trip(self):
        shot = _shot()
        self.assertEqual(StoryboardShot.from_dict(shot.to_dict()), shot)

    def test_round_trip_anchored(self):
        shot = _anchored_shot()
        self.assertEqual(StoryboardShot.from_dict(shot.to_dict()), shot)

    def test_time_end_must_exceed_time_start(self):
        with self.assertRaises(StoryboardError):
            _shot(time_start=3.0, time_end=3.0)

    def test_anchor_requires_constraints(self):
        with self.assertRaises(StoryboardError):
            _shot(visual_anchor="pererekossauro", continuity_constraints=())

    def test_constraints_require_anchor(self):
        with self.assertRaises(StoryboardError):
            _shot(continuity_constraints=("green skin",))

    def test_expression_is_optional(self):
        shot = _shot(expression=None)
        self.assertIsNone(shot.expression)


class StoryboardTests(unittest.TestCase):
    def test_round_trip(self):
        storyboard = Storyboard(
            storyboard_id="pererekossauro_v1",
            shots=(_shot(), _shot(shot_id="shot_02", time_start=3.0, time_end=6.0)),
            selected_direction="A",
            expansion_boundary="moderate",
        )
        self.assertEqual(Storyboard.from_dict(storyboard.to_dict()), storyboard)

    def test_first_shot_must_start_at_zero(self):
        with self.assertRaises(StoryboardError):
            Storyboard(
                storyboard_id="x", shots=(_shot(time_start=1.0, time_end=4.0),),
                selected_direction="A", expansion_boundary="moderate",
            )

    def test_shots_must_be_contiguous(self):
        with self.assertRaises(StoryboardError):
            Storyboard(
                storyboard_id="x",
                shots=(_shot(), _shot(shot_id="shot_02", time_start=4.0, time_end=6.0)),
                selected_direction="A", expansion_boundary="moderate",
            )

    def test_shot_ids_must_be_sequential(self):
        with self.assertRaises(StoryboardError):
            Storyboard(
                storyboard_id="x",
                shots=(_shot(shot_id="shot_05"),),
                selected_direction="A", expansion_boundary="moderate",
            )

    def test_rejects_bad_expansion_boundary(self):
        with self.assertRaises(StoryboardError):
            Storyboard(
                storyboard_id="x", shots=(_shot(),), selected_direction="A",
                expansion_boundary="extreme",
            )

    def test_total_duration(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(_shot(), _shot(shot_id="shot_02", time_start=3.0, time_end=8.0)),
            selected_direction="A", expansion_boundary="moderate",
        )
        self.assertEqual(storyboard.total_duration_seconds, 8.0)

    def test_validate_against_requires_character_bible_for_anchor(self):
        storyboard = Storyboard(
            storyboard_id="x", shots=(_anchored_shot(),), selected_direction="A",
            expansion_boundary="moderate",
        )
        dna = _dna(image_type="landscape", character_bible=None)
        with self.assertRaises(StoryboardError):
            storyboard.validate_against(dna)

    def test_validate_against_rejects_constraint_not_in_dna(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(_anchored_shot(continuity_constraints=("invented trait",)),),
            selected_direction="A", expansion_boundary="moderate",
        )
        with self.assertRaises(StoryboardError):
            storyboard.validate_against(_dna())

    def test_validate_against_accepts_constraint_from_forbidden_or_required(self):
        storyboard = Storyboard(
            storyboard_id="x", shots=(_anchored_shot(),), selected_direction="A",
            expansion_boundary="moderate",
        )
        storyboard.validate_against(_dna())  # must not raise

    def test_validate_against_rejects_banned_term(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(_shot(subject="logo da marca"),),
            selected_direction="A", expansion_boundary="moderate",
        )
        with self.assertRaises(StoryboardError):
            storyboard.validate_against(_dna())


class DefaultContinuityConstraintsTests(unittest.TestCase):
    def test_combines_required_and_forbidden(self):
        constraints = default_continuity_constraints(_dna())
        self.assertIn("green skin", constraints)
        self.assertIn("species change", constraints)

    def test_empty_without_character_bible(self):
        dna = _dna(image_type="landscape", character_bible=None)
        self.assertEqual(default_continuity_constraints(dna), ())


class StoryboardLockTests(unittest.TestCase):
    def _lock(self, **overrides) -> StoryboardLock:
        defaults = dict(
            lock_id="x-lock", approved_by="Isadora", approved_at="2026-09-11T12:00:00Z",
            reference_image_sha256="a" * 64, reference_dna_sha256="b" * 64,
            creative_brief_sha256="c" * 64, storyboard_sha256="d" * 64,
        )
        defaults.update(overrides)
        return StoryboardLock(**defaults)

    def test_round_trip(self):
        lock = self._lock()
        self.assertEqual(StoryboardLock.from_dict(lock.to_dict()), lock)

    def test_rejects_bad_sha256(self):
        with self.assertRaises(StoryboardError):
            self._lock(storyboard_sha256="not-hex")

    def test_rejects_bad_timestamp(self):
        with self.assertRaises(StoryboardError):
            self._lock(approved_at="yesterday")

    def test_lock_violations_reports_missing_file(self):
        lock = self._lock()
        problems = lock_violations(
            lock, digest_of=lambda p: None,
            paths={"reference_image": "x", "reference_dna": "y", "creative_brief": "z", "storyboard": "w"},
        )
        self.assertEqual(len(problems), 4)

    def test_lock_violations_reports_changed_digest(self):
        lock = self._lock()
        problems = lock_violations(
            lock, digest_of=lambda p: "f" * 64,
            paths={"reference_image": "x", "reference_dna": "y", "creative_brief": "z", "storyboard": "w"},
        )
        self.assertEqual(len(problems), 4)

    def test_lock_violations_empty_when_all_match(self):
        lock = self._lock()
        digests = {
            "x": lock.reference_image_sha256, "y": lock.reference_dna_sha256,
            "z": lock.creative_brief_sha256, "w": lock.storyboard_sha256,
        }
        problems = lock_violations(
            lock, digest_of=lambda p: digests[p],
            paths={"reference_image": "x", "reference_dna": "y", "creative_brief": "z", "storyboard": "w"},
        )
        self.assertEqual(problems, ())


class RenderStoryboardMarkdownTests(unittest.TestCase):
    def test_reports_anchored_count(self):
        storyboard = Storyboard(
            storyboard_id="x", shots=(_shot(), _anchored_shot(shot_id="shot_02", time_start=3.0, time_end=6.0)),
            selected_direction="A", expansion_boundary="moderate",
        )
        markdown = render_storyboard_markdown(storyboard, _dna())
        self.assertIn("1/2 shot(s)", markdown)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'video_generator.domain.storyboard'`

- [ ] **Step 3: Write the implementation — header, helpers, StoryboardShot**

```python
"""src/video_generator/domain/storyboard.py

Turns an approved creative direction into time-boxed, executable-adjacent
shots. StoryboardShot fields are authored text (the orchestrator's
descriptive judgment); this module validates structure, contiguity, and
that any shot anchored to the reference subject only ever asks for
consistency the DNA actually promises. Kept separate from
domain/creative_brief.py on purpose: the two evolve independently. See
docs/superpowers/specs/2026-09-11-image-director-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from video_generator.domain.reference_dna import VisualDNA

SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_APPROVED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BANNED_CONTENT_TERMS = ("logo", "watermark", "marca d'agua", "marca dagua", "copyright", "trademark")

_SHOT_TEXT_FIELDS = (
    "purpose", "subject", "action", "camera", "framing", "environment",
    "lighting", "movement", "transition", "audio_intent",
)


class StoryboardError(ValueError):
    """Raised when storyboard or storyboard-lock data is invalid."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoryboardError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _string_tuple(value: object, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise StoryboardError(f"{name} must be a list of strings")
    items = tuple(value)
    if not allow_empty and not items:
        raise StoryboardError(f"{name} must not be empty")
    if any(not isinstance(v, str) or not v.strip() for v in items):
        raise StoryboardError(f"{name} entries must be non-empty strings")
    return tuple(v.strip() for v in items)


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(data, Mapping):
        raise StoryboardError("payload must be an object")
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise StoryboardError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise StoryboardError(f"unknown fields: {', '.join(sorted(unknown))}")


def _sha256_field(value: object, name: str) -> str:
    text = _text(value, name)
    if not _SHA256_RE.fullmatch(text.lower()):
        raise StoryboardError(f"{name} must be a 64-character hex sha256")
    return text.lower()
```

- [ ] **Step 4: Continue the implementation**

Append `StoryboardShot`:

```python
@dataclass(frozen=True, slots=True)
class StoryboardShot:
    shot_id: str
    time_start: float
    time_end: float
    purpose: str
    subject: str
    action: str
    camera: str
    framing: str
    environment: str
    lighting: str
    movement: str
    transition: str
    audio_intent: str
    expression: str | None = None
    visual_anchor: str | None = None
    continuity_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _text(self.shot_id, "shot_id"))
        if isinstance(self.time_start, bool) or not isinstance(self.time_start, (int, float)):
            raise StoryboardError("time_start must be a number")
        if isinstance(self.time_end, bool) or not isinstance(self.time_end, (int, float)):
            raise StoryboardError("time_end must be a number")
        object.__setattr__(self, "time_start", float(self.time_start))
        object.__setattr__(self, "time_end", float(self.time_end))
        if self.time_start < 0:
            raise StoryboardError("time_start must be >= 0")
        if self.time_end <= self.time_start:
            raise StoryboardError("time_end must be greater than time_start")
        for field_name in _SHOT_TEXT_FIELDS:
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        object.__setattr__(self, "expression", _optional_text(self.expression, "expression"))
        object.__setattr__(self, "visual_anchor", _optional_text(self.visual_anchor, "visual_anchor"))
        constraints = _string_tuple(self.continuity_constraints, "continuity_constraints")
        if self.visual_anchor is not None and not constraints:
            raise StoryboardError("a shot with visual_anchor needs non-empty continuity_constraints")
        if self.visual_anchor is None and constraints:
            raise StoryboardError("continuity_constraints requires visual_anchor to be set")
        object.__setattr__(self, "continuity_constraints", constraints)

    def to_dict(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in _SHOT_TEXT_FIELDS}
        payload.update(
            shot_id=self.shot_id,
            time_start=self.time_start,
            time_end=self.time_end,
            expression=self.expression,
            visual_anchor=self.visual_anchor,
            continuity_constraints=list(self.continuity_constraints),
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StoryboardShot":
        _keys(
            data,
            required={"shot_id", "time_start", "time_end", *_SHOT_TEXT_FIELDS},
            optional={"expression", "visual_anchor", "continuity_constraints"},
        )
        return cls(
            shot_id=data["shot_id"],
            time_start=data["time_start"],
            time_end=data["time_end"],
            **{name: data[name] for name in _SHOT_TEXT_FIELDS},
            expression=data.get("expression"),
            visual_anchor=data.get("visual_anchor"),
            continuity_constraints=tuple(data.get("continuity_constraints", ())),
        )
```

- [ ] **Step 5: Continue the implementation**

Append `Storyboard`:

```python
@dataclass(frozen=True, slots=True)
class Storyboard:
    storyboard_id: str
    shots: tuple[StoryboardShot, ...]
    selected_direction: str
    expansion_boundary: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "storyboard_id", _text(self.storyboard_id, "storyboard_id"))
        shots = tuple(self.shots)
        if not shots or not all(isinstance(s, StoryboardShot) for s in shots):
            raise StoryboardError("shots must contain at least one StoryboardShot")
        object.__setattr__(self, "shots", shots)
        ids = [s.shot_id for s in shots]
        if len(set(ids)) != len(ids):
            raise StoryboardError("shot_id values must be unique")
        for position, shot in enumerate(shots, start=1):
            expected_id = f"shot_{position:02d}"
            if shot.shot_id != expected_id:
                raise StoryboardError(f"shot {position} must be named {expected_id!r}")
        if shots[0].time_start != 0.0:
            raise StoryboardError("the first shot must start at time_start == 0")
        for previous, current in zip(shots, shots[1:]):
            if abs(current.time_start - previous.time_end) > 1e-9:
                raise StoryboardError(
                    f"{current.shot_id}.time_start must equal {previous.shot_id}.time_end"
                )
        object.__setattr__(self, "selected_direction", _text(self.selected_direction, "selected_direction"))
        if not re.fullmatch(r"[ABC](\+[ABC])?", self.selected_direction):
            raise StoryboardError("selected_direction must look like 'A', 'B', 'C', 'A+B', 'B+C' or 'A+C'")
        object.__setattr__(self, "expansion_boundary", _text(self.expansion_boundary, "expansion_boundary"))
        if self.expansion_boundary not in ("conservative", "moderate", "bold"):
            raise StoryboardError("expansion_boundary must be conservative, moderate or bold")
        if self.schema_version != SCHEMA_VERSION:
            raise StoryboardError(f"schema_version must be {SCHEMA_VERSION}")

    @property
    def total_duration_seconds(self) -> float:
        return self.shots[-1].time_end

    def validate_against(self, visual_dna: VisualDNA) -> None:
        for shot in self.shots:
            if shot.visual_anchor is not None:
                if visual_dna.character_bible is None:
                    raise StoryboardError(
                        f"{shot.shot_id} sets visual_anchor but the DNA has no character_bible"
                    )
                allowed = set(visual_dna.character_bible.forbidden) | set(visual_dna.required_elements)
                extra = set(shot.continuity_constraints) - allowed
                if extra:
                    raise StoryboardError(
                        f"{shot.shot_id} continuity_constraints not found in the DNA: "
                        + ", ".join(sorted(extra))
                    )
            for field_name in _SHOT_TEXT_FIELDS:
                lowered = getattr(shot, field_name).lower()
                for banned in BANNED_CONTENT_TERMS:
                    if banned in lowered:
                        raise StoryboardError(
                            f"{shot.shot_id}.{field_name} contains a banned term: {banned!r}"
                        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "storyboard_id": self.storyboard_id,
            "shots": [s.to_dict() for s in self.shots],
            "selected_direction": self.selected_direction,
            "expansion_boundary": self.expansion_boundary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Storyboard":
        _keys(
            data,
            required={"storyboard_id", "shots", "selected_direction", "expansion_boundary"},
            optional={"schema_version"},
        )
        shots_data = data["shots"]
        if not isinstance(shots_data, list):
            raise StoryboardError("shots must be a list")
        return cls(
            storyboard_id=data["storyboard_id"],
            shots=tuple(StoryboardShot.from_dict(s) for s in shots_data),
            selected_direction=data["selected_direction"],
            expansion_boundary=data["expansion_boundary"],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


def default_continuity_constraints(visual_dna: VisualDNA) -> tuple[str, ...]:
    """A ready-to-copy constraint list for a new anchored shot.

    A convenience for the author, not a validation fallback: ``Storyboard``
    still requires every shot to state its constraints explicitly.
    """

    if visual_dna.character_bible is None:
        return ()
    seen: list[str] = []
    for item in (*visual_dna.required_elements, *visual_dna.character_bible.forbidden):
        if item not in seen:
            seen.append(item)
    return tuple(seen)
```

- [ ] **Step 6: Continue the implementation**

Append `StoryboardLock`, `lock_violations`, and `render_storyboard_markdown`:

```python
@dataclass(frozen=True, slots=True)
class StoryboardLock:
    lock_id: str
    approved_by: str
    approved_at: str
    reference_image_sha256: str
    reference_dna_sha256: str
    creative_brief_sha256: str
    storyboard_sha256: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "lock_id", _text(self.lock_id, "lock_id"))
        object.__setattr__(self, "approved_by", _text(self.approved_by, "approved_by"))
        object.__setattr__(self, "approved_at", _text(self.approved_at, "approved_at"))
        if not _APPROVED_AT_RE.fullmatch(self.approved_at):
            raise StoryboardError("approved_at must be ISO-8601 UTC, e.g. 2026-09-11T12:00:00Z")
        object.__setattr__(
            self, "reference_image_sha256", _sha256_field(self.reference_image_sha256, "reference_image_sha256")
        )
        object.__setattr__(
            self, "reference_dna_sha256", _sha256_field(self.reference_dna_sha256, "reference_dna_sha256")
        )
        object.__setattr__(
            self, "creative_brief_sha256", _sha256_field(self.creative_brief_sha256, "creative_brief_sha256")
        )
        object.__setattr__(
            self, "storyboard_sha256", _sha256_field(self.storyboard_sha256, "storyboard_sha256")
        )
        if self.schema_version != SCHEMA_VERSION:
            raise StoryboardError(f"schema_version must be {SCHEMA_VERSION}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lock_id": self.lock_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "reference_image_sha256": self.reference_image_sha256,
            "reference_dna_sha256": self.reference_dna_sha256,
            "creative_brief_sha256": self.creative_brief_sha256,
            "storyboard_sha256": self.storyboard_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StoryboardLock":
        _keys(
            data,
            required={
                "lock_id", "approved_by", "approved_at", "reference_image_sha256",
                "reference_dna_sha256", "creative_brief_sha256", "storyboard_sha256",
            },
            optional={"schema_version"},
        )
        return cls(
            lock_id=data["lock_id"],
            approved_by=data["approved_by"],
            approved_at=data["approved_at"],
            reference_image_sha256=data["reference_image_sha256"],
            reference_dna_sha256=data["reference_dna_sha256"],
            creative_brief_sha256=data["creative_brief_sha256"],
            storyboard_sha256=data["storyboard_sha256"],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


def lock_violations(
    lock: StoryboardLock,
    *,
    digest_of: Callable[[str], "str | None"],
    paths: Mapping[str, str],
) -> tuple[str, ...]:
    """Fail-closed check of a lock against the files on disk.

    ``digest_of`` returns the SHA-256 of a path, or ``None`` when the file is
    gone. Mirrors :func:`domain.curation.lock_violations`'s injected-callable
    shape so the domain layer never touches a filesystem itself.
    """

    problems: list[str] = []
    checks = (
        ("reference_image", lock.reference_image_sha256),
        ("reference_dna", lock.reference_dna_sha256),
        ("creative_brief", lock.creative_brief_sha256),
        ("storyboard", lock.storyboard_sha256),
    )
    for key, expected in checks:
        path = paths.get(key)
        if not path:
            problems.append(f"{key}: no path given to verify against")
            continue
        actual = digest_of(path)
        if actual is None:
            problems.append(f"{key}: file is missing: {path}")
            continue
        if actual != expected:
            problems.append(f"{key}: sha256 changed since the lock was made: {path}")
    return tuple(problems)


def render_storyboard_markdown(storyboard: Storyboard, visual_dna: VisualDNA) -> str:
    anchored = sum(1 for s in storyboard.shots if s.visual_anchor is not None)
    lines = [
        f"# Storyboard {storyboard.storyboard_id}",
        "",
        f"Direction: {storyboard.selected_direction}  ·  Expansion boundary: {storyboard.expansion_boundary}",
        f"Total duration: {storyboard.total_duration_seconds:.1f}s over {len(storyboard.shots)} shot(s)",
        "",
        f"{anchored}/{len(storyboard.shots)} shot(s) anchor the reference subject.",
        "",
    ]
    for shot in storyboard.shots:
        lines.append(f"## {shot.shot_id}  ({shot.time_start:.1f}s - {shot.time_end:.1f}s)")
        lines.append(f"- purpose: {shot.purpose}")
        lines.append(f"- subject: {shot.subject}")
        lines.append(f"- action: {shot.action}")
        if shot.expression:
            lines.append(f"- expression: {shot.expression}")
        lines.append(f"- camera: {shot.camera} / {shot.framing}")
        lines.append(f"- environment: {shot.environment}")
        lines.append(f"- lighting: {shot.lighting}")
        lines.append(f"- movement: {shot.movement}")
        lines.append(f"- transition: {shot.transition}")
        lines.append(f"- audio_intent: {shot.audio_intent}")
        if shot.visual_anchor:
            lines.append(f"- visual_anchor: {shot.visual_anchor}")
            lines.append(f"- continuity_constraints: {', '.join(shot.continuity_constraints)}")
        lines.append("")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard -v`
Expected: PASS (all tests green)

- [ ] **Step 8: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 9: Commit**

```bash
git add src/video_generator/domain/storyboard.py tests/test_storyboard.py
git commit -m "Add Storyboard/StoryboardLock contracts with continuity validation"
```

---

## Task 5: `planning.py` — `Shot` continuity fields + `storyboard_to_shot_plan` bridge

**Files:**
- Modify: `src/video_generator/domain/planning.py` (the `Shot` dataclass and its `__post_init__`/`to_dict`/`from_dict`; append `storyboard_to_shot_plan` near `shot_plan_to_edit_plan`)
- Modify: `tests/test_planning.py` (append tests)

**Interfaces:**
- Consumes: `video_generator.domain.storyboard.Storyboard`, `video_generator.domain.reference_dna.VisualDNA` (new imports into `planning.py`).
- Produces: `Shot.visual_anchor: str | None = None`, `Shot.continuity_constraints: tuple[str, ...] | None = None` (additive), `storyboard_to_shot_plan(storyboard, visual_dna, *, plan_id=None) -> tuple[ShotPlan, AssetRequirements]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_planning.py`:

```python
from video_generator.domain.planning import storyboard_to_shot_plan
from video_generator.domain.reference_dna import CharacterBible, ReferenceImageSignals, VisualDNA
from video_generator.domain.storyboard import Storyboard, StoryboardShot


def _dna_for_bridge(**overrides):
    defaults = dict(
        image_type="character",
        subject={"main": "pererekossauro"},
        color={"dominant": "#2E7D32"},
        light={"direction": "front"},
        material={},
        composition={"focal_point": "center"},
        mood=("comical",),
        movement_potential={"character": ("hop",)},
        required_elements=("green skin", "round eyes"),
        flexible_elements=("background",),
        signals=ReferenceImageSignals(width=800, height=600, orientation="landscape", avg_luma=0.4),
        character_bible=CharacterBible(
            anatomy="bipedal frog-dinosaur hybrid", proportions="large head, short limbs",
            eyes="two, round", mouth="wide, closed", limbs="four, short",
            colors="green body", texture="smooth", accessories="none",
            base_expression="curious", deformation_limits="stylized but consistent",
            forbidden=("extra eyes", "species change"),
        ),
        source_image_sha256="a" * 64,
        source_image_path="assets/reference/aaaa_pererekossauro.png",
    )
    defaults.update(overrides)
    return VisualDNA(**defaults)


def _storyboard_shot(**overrides):
    defaults = dict(
        shot_id="shot_01", time_start=0.0, time_end=3.0, purpose="hook",
        subject="pererekossauro", action="olha para a camera", camera="close",
        framing="centered", environment="parque vazio", lighting="luz difusa",
        movement="static", transition="corte seco", audio_intent="silencio tenso",
    )
    defaults.update(overrides)
    return StoryboardShot(**defaults)


class ShotContinuityFieldsTests(unittest.TestCase):
    def _shot_kwargs(self, **overrides):
        base = dict(
            shot_id="scene_01_shot_01", scene_id="scene_01", index=1, duration_seconds=3.0,
            asset_type="image", shot_type="establishing", scale="wide", visual_query="x",
            purpose="p", beat=False, asset_id="asset_1", reuse_of=None, framing={},
            justification=None, provenance={},
        )
        base.update(overrides)
        return base

    def test_defaults_are_none(self):
        shot = Shot(**self._shot_kwargs())
        self.assertIsNone(shot.visual_anchor)
        self.assertIsNone(shot.continuity_constraints)

    def test_round_trip_with_anchor(self):
        shot = Shot(**self._shot_kwargs(
            visual_anchor="pererekossauro", continuity_constraints=("green skin",)
        ))
        self.assertEqual(Shot.from_dict(shot.to_dict()), shot)

    def test_constraints_require_anchor(self):
        with self.assertRaises(PlanningError):
            Shot(**self._shot_kwargs(continuity_constraints=("green skin",)))


class StoryboardToShotPlanTests(unittest.TestCase):
    def test_compiles_anchored_and_plain_shots(self):
        storyboard = Storyboard(
            storyboard_id="pererekossauro_v1",
            shots=(
                _storyboard_shot(
                    visual_anchor="pererekossauro", continuity_constraints=("green skin", "round eyes")
                ),
                _storyboard_shot(shot_id="shot_02", time_start=3.0, time_end=6.0, subject="parque vazio"),
            ),
            selected_direction="A", expansion_boundary="moderate",
        )
        shot_plan, asset_requirements = storyboard_to_shot_plan(storyboard, _dna_for_bridge())
        self.assertEqual(len(shot_plan.shots), 2)
        anchored = shot_plan.shots[0]
        self.assertEqual(anchored.visual_anchor, "pererekossauro")
        self.assertEqual(anchored.continuity_constraints, ("green skin", "round eyes"))
        self.assertEqual(anchored.asset_type, "image")
        plain = shot_plan.shots[1]
        self.assertIsNone(plain.visual_anchor)
        self.assertNotEqual(plain.asset_id, anchored.asset_id)
        asset_requirements.validate_against(shot_plan)

    def test_repeated_anchor_shares_one_asset_requirement(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(
                _storyboard_shot(visual_anchor="p", continuity_constraints=("green skin",)),
                _storyboard_shot(
                    shot_id="shot_02", time_start=3.0, time_end=6.0,
                    visual_anchor="p", continuity_constraints=("green skin",),
                ),
            ),
            selected_direction="A", expansion_boundary="moderate",
        )
        shot_plan, asset_requirements = storyboard_to_shot_plan(storyboard, _dna_for_bridge())
        self.assertEqual(shot_plan.shots[0].asset_id, shot_plan.shots[1].asset_id)
        self.assertEqual(shot_plan.shots[1].reuse_of, shot_plan.shots[0].asset_id)
        matching = [r for r in asset_requirements.requirements if r.asset_id == shot_plan.shots[0].asset_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(set(matching[0].used_by), {"scene_01_shot_01", "scene_01_shot_02"})

    def test_raises_when_anchor_used_without_character_bible(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(_storyboard_shot(visual_anchor="p", continuity_constraints=("x",)),),
            selected_direction="A", expansion_boundary="moderate",
        )
        dna = _dna_for_bridge(image_type="landscape", character_bible=None)
        with self.assertRaises(PlanningError):
            storyboard_to_shot_plan(storyboard, dna)

    def test_resulting_shot_plan_and_requirements_are_internally_valid(self):
        storyboard = Storyboard(
            storyboard_id="x", shots=(_storyboard_shot(),), selected_direction="A",
            expansion_boundary="moderate",
        )
        shot_plan, asset_requirements = storyboard_to_shot_plan(storyboard, _dna_for_bridge())
        # constructing them already ran every local invariant; this call
        # exercises the cross-document check too.
        asset_requirements.validate_against(shot_plan)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_planning.ShotContinuityFieldsTests tests.test_planning.StoryboardToShotPlanTests -v`
Expected: FAIL/ERROR — `Shot() got an unexpected keyword argument 'visual_anchor'` / `ImportError: cannot import name 'storyboard_to_shot_plan'`

- [ ] **Step 3: Add the two fields to `Shot`**

In `src/video_generator/domain/planning.py`, find the `Shot` dataclass field list (it currently ends with `filmable_concept: str | None = None`) and add two more fields right after it:

```python
    filmable_concept: str | None = None
    # --- Reference-image continuity (optional) ------------------------------ #
    # Present when this shot was compiled from a Storyboard anchored to a
    # reference image. ``visual_anchor`` names the subject (e.g. a character
    # id); ``continuity_constraints`` are the DNA traits that must not drift
    # for this shot. Absent on every plan built without the image-director
    # pipeline.
    visual_anchor: str | None = None
    continuity_constraints: tuple[str, ...] | None = None
```

- [ ] **Step 4: Validate the two fields in `Shot.__post_init__`**

Find the end of `Shot.__post_init__` (it currently ends with the `filmable_concept` assignment) and add:

```python
        object.__setattr__(
            self,
            "filmable_concept",
            _optional_text(self.filmable_concept, "filmable_concept"),
        )
        object.__setattr__(
            self, "visual_anchor", _optional_text(self.visual_anchor, "visual_anchor")
        )
        if self.continuity_constraints is not None:
            constraints = tuple(self.continuity_constraints)
            if any(not isinstance(c, str) or not c.strip() for c in constraints):
                raise PlanningError("continuity_constraints must be non-empty strings")
            if len(set(constraints)) != len(constraints):
                raise PlanningError("continuity_constraints must be unique")
            if self.visual_anchor is None:
                raise PlanningError("continuity_constraints requires visual_anchor to be set")
            object.__setattr__(self, "continuity_constraints", constraints)
```

(The `filmable_concept` assignment line already exists — only the block after it, from `visual_anchor` onward, is new.)

- [ ] **Step 5: Add the two fields to `Shot.to_dict` and `Shot.from_dict`**

In `Shot.to_dict`, find the dict literal's last entry (`"filmable_concept": self.filmable_concept,`) and add after it:

```python
            "filmable_concept": self.filmable_concept,
            "visual_anchor": self.visual_anchor,
            "continuity_constraints": (
                list(self.continuity_constraints) if self.continuity_constraints is not None else None
            ),
        }
```

In `Shot.from_dict`, find the `optional={...}` set passed to `_keys(...)` and add the two new names to it, and add two more keyword arguments to the `cls(...)` call:

```python
            optional={
                "editorial_role", "asset_queries", "beat_concept",
                "visual_intent_class", "visual_role", "refined_query",
                "filmable_concept", "visual_anchor", "continuity_constraints",
            },
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
            editorial_role=data.get("editorial_role"),
            asset_queries=tuple(data.get("asset_queries", ())),
            beat_concept=data.get("beat_concept"),
            visual_intent_class=data.get("visual_intent_class"),
            visual_role=data.get("visual_role"),
            refined_query=data.get("refined_query"),
            filmable_concept=data.get("filmable_concept"),
            visual_anchor=data.get("visual_anchor"),
            continuity_constraints=(
                tuple(data["continuity_constraints"]) if data.get("continuity_constraints") is not None else None
            ),
        )
```

- [ ] **Step 6: Run the field-level tests**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_planning.ShotContinuityFieldsTests -v`
Expected: PASS

- [ ] **Step 7: Add the two new imports to the top of `planning.py`**

Near the top of `src/video_generator/domain/planning.py`, alongside the existing imports, add:

```python
from video_generator.domain.reference_dna import VisualDNA
from video_generator.domain.storyboard import Storyboard
```

- [ ] **Step 8: Implement `storyboard_to_shot_plan`**

Append near `shot_plan_to_edit_plan` in `src/video_generator/domain/planning.py`:

```python
def storyboard_to_shot_plan(
    storyboard: Storyboard,
    visual_dna: VisualDNA,
    *,
    plan_id: str | None = None,
) -> tuple[ShotPlan, AssetRequirements]:
    """Compile an approved Storyboard into an executable ShotPlan.

    Every field on a StoryboardShot is already authored text; this function
    only compiles it into the shapes ``resolve-assets`` and the renderer
    already understand. It never derives new creative content. Every shot
    anchored to the reference subject shares one AssetRequirement pointed at
    the locked reference image (via its recorded path/sha256 in ``notes``) so
    every anchored shot uses the exact same picture -- this project's only
    mechanism for character consistency without image generation.
    """

    if any(shot.visual_anchor is not None for shot in storyboard.shots) and visual_dna.character_bible is None:
        raise PlanningError(
            "storyboard anchors a subject but the reference DNA has no character_bible"
        )

    plan_id = plan_id or f"{storyboard.storyboard_id}-shot-plan"
    reference_asset_id = f"{storyboard.storyboard_id}_reference"
    scene_id = "scene_01"
    reference_stem = (
        Path(visual_dna.source_image_path).stem if visual_dna.source_image_path else reference_asset_id
    )

    shots: list[Shot] = []
    requirements: dict[str, AssetRequirement] = {}

    for position, sb_shot in enumerate(storyboard.shots, start=1):
        shot_id = f"{scene_id}_shot_{position:02d}"
        duration = sb_shot.time_end - sb_shot.time_start
        visual_query = ", ".join(
            part for part in (sb_shot.subject, sb_shot.action, sb_shot.environment, sb_shot.lighting) if part
        )
        if sb_shot.visual_anchor is not None:
            asset_id = reference_asset_id
            reuse_of = requirements[asset_id].used_by[0] if asset_id in requirements else None
            existing = requirements.get(asset_id)
            requirements[asset_id] = AssetRequirement(
                asset_id=asset_id,
                type="image",
                query=reference_stem,
                duration_needed_seconds=max(duration, existing.duration_needed_seconds if existing else 0.0),
                orientation=visual_dna.signals.orientation,
                purpose=f"locked reference asset for {sb_shot.visual_anchor}",
                used_by=(*(existing.used_by if existing else ()), shot_id),
                min_count=1,
                notes=(
                    f"Must resolve to the locked reference image at "
                    f"{visual_dna.source_image_path} (sha256 {visual_dna.source_image_sha256}); "
                    "do not substitute a different picture for this asset."
                ),
            )
        else:
            asset_id = f"asset_{shot_id}"
            reuse_of = None
            requirements[asset_id] = AssetRequirement(
                asset_id=asset_id,
                type="image",
                query=visual_query or sb_shot.purpose,
                duration_needed_seconds=duration,
                orientation=visual_dna.signals.orientation,
                purpose=sb_shot.purpose,
                used_by=(shot_id,),
                min_count=1,
                notes=None,
            )

        shots.append(
            Shot(
                shot_id=shot_id,
                scene_id=scene_id,
                index=position,
                duration_seconds=duration,
                asset_type="image",
                shot_type="storyboard_shot",
                scale="medium",
                visual_query=visual_query or sb_shot.purpose,
                purpose=sb_shot.purpose,
                beat=False,
                asset_id=asset_id,
                reuse_of=reuse_of,
                framing={},
                justification=None,
                provenance={
                    "visual_query": "authored", "purpose": "authored", "shot_type": "authored",
                },
                visual_anchor=sb_shot.visual_anchor,
                continuity_constraints=(
                    sb_shot.continuity_constraints if sb_shot.visual_anchor is not None else None
                ),
            )
        )

    longest = max(s.duration_seconds for s in shots)
    policy = RhythmPolicy(
        shot_type_weights={"storyboard_shot": 1},
        scale_cycle=("medium",),
        asset_type_for_shot_type={"storyboard_shot": "image"},
        absolute_max_shot_seconds=longest + 1.0,
        soft_max_shot_seconds=longest + 1.0,
    )
    shot_plan = ShotPlan(
        plan_id=plan_id,
        scene_plan_id=f"{storyboard.storyboard_id}-scene-plan",
        script_id=storyboard.storyboard_id,
        seed=0,
        policy=policy,
        shots=tuple(shots),
    )
    asset_requirements = AssetRequirements(
        plan_id=f"{storyboard.storyboard_id}-asset-requirements",
        shot_plan_id=shot_plan.plan_id,
        script_id=storyboard.storyboard_id,
        orientation=visual_dna.signals.orientation,
        requirements=tuple(requirements.values()),
    )
    asset_requirements.validate_against(shot_plan)
    return shot_plan, asset_requirements
```

Confirm `Path` is already imported at the top of `planning.py` (it is, for other uses) — if not, add `from pathlib import Path`.

- [ ] **Step 9: Run the new bridge tests**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_planning.StoryboardToShotPlanTests -v`
Expected: PASS

- [ ] **Step 10: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 11: Commit**

```bash
git add src/video_generator/domain/planning.py tests/test_planning.py
git commit -m "Bridge an approved Storyboard into ShotPlan/AssetRequirements"
```

---

## Task 6: JSON Schemas — 4 new contracts + additive `shot-plan-v1` update

**Files:**
- Create: `schemas/reference-dna-v1.schema.json`
- Create: `schemas/creative-brief-v1.schema.json`
- Create: `schemas/storyboard-v1.schema.json`
- Create: `schemas/storyboard-lock-v1.schema.json`
- Modify: `schemas/shot-plan-v1.schema.json` (additive: two new optional shot properties)
- Test: `tests/test_schemas.py` if it exists (check first with `Test-Path tests\test_schemas.py`); otherwise create `tests/test_image_director_schemas.py`

- [ ] **Step 1: Check for an existing schema-test file**

Run: `Test-Path tests\test_schemas.py` (PowerShell) — note the result for Step 3.

- [ ] **Step 2: Write `schemas/reference-dna-v1.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/thales-sblue/video-generator/schemas/reference-dna-v1.schema.json",
  "title": "VisualDNA v1",
  "description": "Structured visual DNA of a reference image: what must stay consistent across every shot that uses it. Authored by the orchestrator; only structure and closed vocabularies are validated here.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "image_type", "subject", "color", "light", "material",
    "composition", "mood", "movement_potential", "required_elements",
    "flexible_elements", "signals"
  ],
  "properties": {
    "schema_version": { "const": 1 },
    "image_type": {
      "enum": ["character", "photograph", "illustration", "product", "space", "landscape", "poster", "advertising", "stylized_ip", "mixed"]
    },
    "subject": { "type": "object" },
    "color": { "type": "object" },
    "light": { "type": "object" },
    "material": { "type": "object" },
    "composition": { "type": "object" },
    "mood": { "type": "array", "minItems": 1, "items": { "type": "string", "minLength": 1 } },
    "movement_potential": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "character": { "type": "array", "items": { "type": "string", "minLength": 1 } },
        "micro": { "type": "array", "items": { "type": "string", "minLength": 1 } },
        "camera": { "type": "array", "items": { "type": "string", "minLength": 1 } },
        "light": { "type": "array", "items": { "type": "string", "minLength": 1 } },
        "ambient": { "type": "array", "items": { "type": "string", "minLength": 1 } },
        "transitions": { "type": "array", "items": { "type": "string", "minLength": 1 } }
      }
    },
    "required_elements": { "type": "array", "minItems": 1, "items": { "type": "string", "minLength": 1 } },
    "flexible_elements": { "type": "array", "items": { "type": "string", "minLength": 1 } },
    "signals": {
      "type": "object",
      "additionalProperties": false,
      "required": ["width", "height", "orientation", "avg_luma"],
      "properties": {
        "width": { "type": "integer", "exclusiveMinimum": 0 },
        "height": { "type": "integer", "exclusiveMinimum": 0 },
        "orientation": { "enum": ["landscape", "portrait", "square"] },
        "avg_luma": { "type": ["number", "null"], "minimum": 0, "maximum": 1 },
        "palette": { "type": "array", "items": { "type": "string", "pattern": "^#[0-9A-F]{6}$" } }
      }
    },
    "character_bible": {
      "type": ["object", "null"],
      "additionalProperties": false,
      "required": ["anatomy", "proportions", "eyes", "mouth", "limbs", "colors", "texture", "accessories", "base_expression", "deformation_limits", "forbidden"],
      "properties": {
        "anatomy": { "type": "string", "minLength": 1 },
        "proportions": { "type": "string", "minLength": 1 },
        "eyes": { "type": "string", "minLength": 1 },
        "mouth": { "type": "string", "minLength": 1 },
        "limbs": { "type": "string", "minLength": 1 },
        "colors": { "type": "string", "minLength": 1 },
        "texture": { "type": "string", "minLength": 1 },
        "accessories": { "type": "string", "minLength": 1 },
        "base_expression": { "type": "string", "minLength": 1 },
        "deformation_limits": { "type": "string", "minLength": 1 },
        "forbidden": { "type": "array", "minItems": 1, "items": { "type": "string", "minLength": 1 } }
      }
    },
    "source_image_sha256": { "type": ["string", "null"], "pattern": "^[0-9a-f]{64}$" },
    "source_image_path": { "type": ["string", "null"], "minLength": 1 }
  }
}
```

- [ ] **Step 3: Write `schemas/creative-brief-v1.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/thales-sblue/video-generator/schemas/creative-brief-v1.schema.json",
  "title": "CreativeBrief v1",
  "description": "Category options, chosen narrative structure, exactly three creative directions and the chosen expansion boundary for a reference-image-driven video. Authored by the orchestrator and approved by a human.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "category_options", "selected_category", "structure", "directions", "selected_direction"],
  "properties": {
    "schema_version": { "const": 1 },
    "category_options": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["category", "why", "suggested_structure", "suggested_duration_seconds", "narration", "dialogue", "media_format"],
        "properties": {
          "category": { "type": "string", "minLength": 1 },
          "why": { "type": "string", "minLength": 1 },
          "suggested_structure": { "type": "string", "minLength": 1 },
          "suggested_duration_seconds": { "type": "number", "exclusiveMinimum": 0 },
          "narration": { "type": "boolean" },
          "dialogue": { "type": "boolean" },
          "media_format": { "type": "string", "minLength": 1 }
        }
      }
    },
    "selected_category": { "type": "string", "minLength": 1 },
    "structure": {
      "type": "object",
      "additionalProperties": false,
      "required": ["beats"],
      "properties": {
        "beats": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["name", "start_seconds", "end_seconds"],
            "properties": {
              "name": { "type": "string", "minLength": 1 },
              "start_seconds": { "type": "number", "minimum": 0 },
              "end_seconds": { "type": "number", "exclusiveMinimum": 0 }
            }
          }
        }
      }
    },
    "directions": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "concept", "emotion", "action", "expansion", "camera_strategy", "hook", "climax", "ending", "why_it_works"],
        "properties": {
          "name": { "type": "string", "minLength": 1 },
          "concept": { "type": "string", "minLength": 1 },
          "emotion": { "type": "string", "minLength": 1 },
          "action": { "type": "string", "minLength": 1 },
          "expansion": { "type": "string", "minLength": 1 },
          "camera_strategy": { "type": "string", "minLength": 1 },
          "hook": { "type": "string", "minLength": 1 },
          "climax": { "type": "string", "minLength": 1 },
          "ending": { "type": "string", "minLength": 1 },
          "why_it_works": { "type": "string", "minLength": 1 }
        }
      }
    },
    "selected_direction": { "type": "string", "pattern": "^[ABC](\\+[ABC])?$" },
    "expansion_boundary": { "enum": ["conservative", "moderate", "bold"] }
  }
}
```

- [ ] **Step 4: Write `schemas/storyboard-v1.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/thales-sblue/video-generator/schemas/storyboard-v1.schema.json",
  "title": "Storyboard v1",
  "description": "Time-boxed, descriptive shots compiled from an approved creative direction, ahead of ShotPlan. Realizes the 'Storyboard' contract candidate from AGENTS.md for the reference-image pipeline.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "storyboard_id", "shots", "selected_direction", "expansion_boundary"],
  "properties": {
    "schema_version": { "const": 1 },
    "storyboard_id": { "type": "string", "minLength": 1 },
    "shots": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["shot_id", "time_start", "time_end", "purpose", "subject", "action", "camera", "framing", "environment", "lighting", "movement", "transition", "audio_intent"],
        "properties": {
          "shot_id": { "type": "string", "pattern": "^shot_[0-9]{2,}$" },
          "time_start": { "type": "number", "minimum": 0 },
          "time_end": { "type": "number", "exclusiveMinimum": 0 },
          "purpose": { "type": "string", "minLength": 1 },
          "subject": { "type": "string", "minLength": 1 },
          "action": { "type": "string", "minLength": 1 },
          "expression": { "type": ["string", "null"], "minLength": 1 },
          "camera": { "type": "string", "minLength": 1 },
          "framing": { "type": "string", "minLength": 1 },
          "environment": { "type": "string", "minLength": 1 },
          "lighting": { "type": "string", "minLength": 1 },
          "movement": { "type": "string", "minLength": 1 },
          "transition": { "type": "string", "minLength": 1 },
          "audio_intent": { "type": "string", "minLength": 1 },
          "visual_anchor": { "type": ["string", "null"], "minLength": 1 },
          "continuity_constraints": { "type": "array", "items": { "type": "string", "minLength": 1 } }
        }
      }
    },
    "selected_direction": { "type": "string", "pattern": "^[ABC](\\+[ABC])?$" },
    "expansion_boundary": { "enum": ["conservative", "moderate", "bold"] }
  }
}
```

- [ ] **Step 5: Write `schemas/storyboard-lock-v1.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/thales-sblue/video-generator/schemas/storyboard-lock-v1.schema.json",
  "title": "StoryboardLock v1",
  "description": "Human approval record freezing a storyboard and its inputs by SHA-256, mirroring visual-lock-v1.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "lock_id", "approved_by", "approved_at", "reference_image_sha256", "reference_dna_sha256", "creative_brief_sha256", "storyboard_sha256"],
  "properties": {
    "schema_version": { "const": 1 },
    "lock_id": { "type": "string", "minLength": 1 },
    "approved_by": { "type": "string", "minLength": 1 },
    "approved_at": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$" },
    "reference_image_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
    "reference_dna_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
    "creative_brief_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" },
    "storyboard_sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" }
  }
}
```

- [ ] **Step 6: Additive edit to `schemas/shot-plan-v1.schema.json`**

Find the shot item's `properties` object (it currently ends with the `filmable_concept` property) and add two more properties after it — do **not** add them to `required`:

```json
          "filmable_concept": {
            "description": "Editorial Visual Translation v1: the id of the filmable concept the shot's queries came from, for example 'self_deception/2' or 'intent/metaphorical/1'. Null on a plan made without the translation layer.",
            "type": [
              "string",
              "null"
            ],
            "minLength": 1
          },
          "visual_anchor": {
            "description": "The reference-image subject id this shot is anchored to (e.g. a character id), when the shot was compiled from a Storyboard. Null otherwise.",
            "type": ["string", "null"],
            "minLength": 1
          },
          "continuity_constraints": {
            "description": "The DNA traits that must not drift for this shot, when visual_anchor is set. Null otherwise.",
            "type": ["array", "null"],
            "items": { "type": "string", "minLength": 1 }
          }
        }
      }
    }
  },
```

(This replaces the tail of the shot item's `properties` object plus the object/array closing braces that follow `filmable_concept` today — adjust brace nesting to match the file's actual current ending, confirmed by reading the file before editing.)

- [ ] **Step 7: Write the schema tests**

If `tests/test_schemas.py` exists (from Step 1), append to it; otherwise create `tests/test_image_director_schemas.py`:

```python
"""tests/test_image_director_schemas.py (only if tests/test_schemas.py does not already exist)"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

from video_generator.domain.creative_brief import (
    CreativeBrief, CreativeDirection, NarrativeStructureOption, StructureBeat, VideoCategoryOption,
)
from video_generator.domain.reference_dna import CharacterBible, ReferenceImageSignals, VisualDNA
from video_generator.domain.storyboard import Storyboard, StoryboardLock, StoryboardShot

_SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMAS_DIR / name).read_text(encoding="utf-8"))


@unittest.skipUnless(_HAS_JSONSCHEMA, "jsonschema is not installed; schema files are still parsed for valid JSON")
class SchemaValidationTests(unittest.TestCase):
    def test_reference_dna_example_validates(self):
        dna = VisualDNA(
            image_type="character", subject={"main": "p"}, color={"dominant": "#2E7D32"},
            light={"direction": "front"}, material={}, composition={"focal_point": "center"},
            mood=("comical",), movement_potential={"character": ("hop",)},
            required_elements=("green skin",), flexible_elements=(),
            signals=ReferenceImageSignals(width=800, height=600, orientation="landscape", avg_luma=0.4),
            character_bible=CharacterBible(
                anatomy="a", proportions="a", eyes="a", mouth="a", limbs="a", colors="a",
                texture="a", accessories="a", base_expression="a", deformation_limits="a",
                forbidden=("extra eyes",),
            ),
        )
        jsonschema.validate(dna.to_dict(), _load_schema("reference-dna-v1.schema.json"))

    def test_creative_brief_example_validates(self):
        direction = CreativeDirection(
            name="a", concept="a", emotion="a", action="a", expansion="a",
            camera_strategy="a", hook="a", climax="a", ending="a", why_it_works="a",
        )
        brief = CreativeBrief(
            category_options=(VideoCategoryOption(
                category="c", why="w", suggested_structure="s",
                suggested_duration_seconds=15.0, narration=True, dialogue=False, media_format="9:16",
            ),),
            selected_category="c",
            structure=NarrativeStructureOption(beats=(StructureBeat(name="hook", start_seconds=0.0, end_seconds=15.0),)),
            directions=(direction, direction, direction),
            selected_direction="A",
        )
        jsonschema.validate(brief.to_dict(), _load_schema("creative-brief-v1.schema.json"))

    def test_storyboard_example_validates(self):
        storyboard = Storyboard(
            storyboard_id="x",
            shots=(StoryboardShot(
                shot_id="shot_01", time_start=0.0, time_end=3.0, purpose="p", subject="s",
                action="a", camera="c", framing="f", environment="e", lighting="l",
                movement="m", transition="t", audio_intent="ai",
            ),),
            selected_direction="A", expansion_boundary="moderate",
        )
        jsonschema.validate(storyboard.to_dict(), _load_schema("storyboard-v1.schema.json"))

    def test_storyboard_lock_example_validates(self):
        lock = StoryboardLock(
            lock_id="x-lock", approved_by="Isadora", approved_at="2026-09-11T12:00:00Z",
            reference_image_sha256="a" * 64, reference_dna_sha256="b" * 64,
            creative_brief_sha256="c" * 64, storyboard_sha256="d" * 64,
        )
        jsonschema.validate(lock.to_dict(), _load_schema("storyboard-lock-v1.schema.json"))


class SchemaFilesAreValidJSONTests(unittest.TestCase):
    def test_every_new_schema_parses(self):
        for name in (
            "reference-dna-v1.schema.json", "creative-brief-v1.schema.json",
            "storyboard-v1.schema.json", "storyboard-lock-v1.schema.json",
        ):
            with self.subTest(name=name):
                _load_schema(name)  # raises if not valid JSON

    def test_shot_plan_schema_still_parses_after_the_additive_edit(self):
        _load_schema("shot-plan-v1.schema.json")


if __name__ == "__main__":
    unittest.main()
```

Note: `jsonschema` is not a project dependency (stdlib-only rule) — the class that needs it is skipped when it is not installed, so the suite never depends on it; `SchemaFilesAreValidJSONTests` runs unconditionally with only `json.loads`, which is stdlib.

- [ ] **Step 8: Run the schema tests**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_image_director_schemas -v` (or `tests.test_schemas` if you appended there)
Expected: PASS (the `jsonschema`-dependent class may show as skipped — that is fine)

- [ ] **Step 9: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
git add schemas/reference-dna-v1.schema.json schemas/creative-brief-v1.schema.json schemas/storyboard-v1.schema.json schemas/storyboard-lock-v1.schema.json schemas/shot-plan-v1.schema.json tests/test_image_director_schemas.py
git commit -m "Add JSON Schemas for VisualDNA, CreativeBrief, Storyboard, StoryboardLock"
```

---

## Task 7: CLI `analyze-reference-image`

**Files:**
- Modify: `src/video_generator/cli.py` (imports near the top; a new argparse block near the `review-cuts`/`visual-lock` parsers; a new `_run_analyze_reference_image` handler near `_run_review_cuts`; one dispatch line in `main()` near `if args.command == "review-cuts":`)
- Test: `tests/test_analyze_reference_image_cli.py`

**Interfaces:**
- Consumes: `_review_out_dir` (existing), `video_generator.curation.sha256_of` (existing), `video_generator.adapters.ffmpeg.probe_reference_image` (Task 2), `video_generator.domain.reference_dna.{VisualDNA, ReferenceDNAError, reference_dna_template, render_reference_dna_checklist, render_reference_dna_markdown}` (Task 1).
- Produces: CLI command `analyze-reference-image IMAGE (--project SLUG | --out-dir DIR) [--from-dna PATH] [--force] [--json]`.

- [ ] **Step 1: Write the failing CLI test file**

```python
"""tests/test_analyze_reference_image_cli.py"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REFERENCE_IMAGE = Path(__file__).parent / "fixtures" / "reference_image_small.png"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(_REPO_ROOT / "src")}
    import os
    full_env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-m", "video_generator", *args],
        cwd=str(cwd), capture_output=True, text=True, env=full_env,
    )


class AnalyzeReferenceImageCliTests(unittest.TestCase):
    def test_scaffolds_a_template_and_locks_the_image(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "test_project"
            result = _run_cli(
                "analyze-reference-image", str(_REFERENCE_IMAGE),
                "--out-dir", str(project_dir), "--json", cwd=Path(tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            dna_path = Path(payload["dna_path"])
            self.assertTrue(dna_path.is_file())
            template = json.loads(dna_path.read_text(encoding="utf-8"))
            self.assertEqual(template["image_type"], "")
            locked = Path(payload["reference_image_path"])
            self.assertTrue(locked.is_file())
            self.assertTrue(locked.name.startswith(payload["reference_image_sha256"]))

    def test_refuses_to_overwrite_without_force(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "test_project"
            _run_cli(
                "analyze-reference-image", str(_REFERENCE_IMAGE),
                "--out-dir", str(project_dir), cwd=Path(tmp),
            )
            result = _run_cli(
                "analyze-reference-image", str(_REFERENCE_IMAGE),
                "--out-dir", str(project_dir), cwd=Path(tmp),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)

    def test_missing_image_is_an_error(self):
        with TemporaryDirectory() as tmp:
            result = _run_cli(
                "analyze-reference-image", str(Path(tmp) / "nope.png"),
                "--out-dir", str(Path(tmp) / "projects" / "x"), cwd=Path(tmp),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not exist", result.stderr)

    def test_from_dna_validates_a_completed_file(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "test_project"
            scaffold = _run_cli(
                "analyze-reference-image", str(_REFERENCE_IMAGE),
                "--out-dir", str(project_dir), "--json", cwd=Path(tmp),
            )
            payload = json.loads(scaffold.stdout)
            dna_path = Path(payload["dna_path"])
            template = json.loads(dna_path.read_text(encoding="utf-8"))
            template.update(
                image_type="landscape",
                subject={"main": "quadrants"},
                color={"dominant": "#C81E1E"},
                light={"direction": "flat"},
                composition={"focal_point": "center"},
                mood=["graphic"],
                movement_potential={},
                required_elements=["four flat colour blocks"],
                flexible_elements=[],
            )
            dna_path.write_text(json.dumps(template), encoding="utf-8")
            result = _run_cli(
                "analyze-reference-image", "--from-dna", str(dna_path),
                "--out-dir", str(project_dir), "--json", cwd=Path(tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["image_type"], "landscape")
            self.assertFalse(summary["has_character_bible"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_analyze_reference_image_cli -v`
Expected: FAIL — `argument command: invalid choice: 'analyze-reference-image'`

- [ ] **Step 3: Add imports to `src/video_generator/cli.py`**

Near the existing `from video_generator.domain.curation import CurationError` / `from video_generator.domain import (...)` block, add:

```python
from video_generator.domain.reference_dna import (
    ReferenceDNAError,
    VisualDNA,
    reference_dna_template,
    render_reference_dna_checklist,
    render_reference_dna_markdown,
)
from video_generator.adapters.ffmpeg import probe_reference_image
```

Confirm `sha256_of` is already imported from `video_generator.curation` at the top (it is, alongside `load_curation_set` etc. per the existing `visual-lock` command) — if the exact import tuple does not already include it, add it there.

- [ ] **Step 4: Add the argparse block**

Near the `review-cuts`/`visual-lock` parser blocks in `build_parser()`, add:

```python
    analyze_reference_cmd = subparsers.add_parser(
        "analyze-reference-image",
        help="scaffold a reference image's visual DNA, or validate a filled-in one",
    )
    analyze_reference_cmd.add_argument(
        "image", nargs="?", help="the reference image file (omit with --from-dna)"
    )
    analyze_reference_dest = analyze_reference_cmd.add_mutually_exclusive_group(required=True)
    analyze_reference_dest.add_argument(
        "--project", help="project slug; writes under projects/<slug>/"
    )
    analyze_reference_dest.add_argument(
        "--out-dir", help="explicit output directory for the DNA artifacts"
    )
    analyze_reference_cmd.add_argument(
        "--from-dna",
        help="skip probing: re-validate this reference-dna.json and re-render "
        "reference-dna.md from it (for an agent-authored or hand-edited DNA)",
    )
    analyze_reference_cmd.add_argument(
        "--force", action="store_true", help="allow overwriting an existing scaffold"
    )
    analyze_reference_cmd.add_argument(
        "--json", action="store_true", help="print the summary as JSON"
    )
```

- [ ] **Step 5: Add the dispatch line in `main()`**

Near `if args.command == "review-cuts": return _run_review_cuts(args)`, add:

```python
    if args.command == "analyze-reference-image":
        return _run_analyze_reference_image(args)
```

- [ ] **Step 6: Implement `_run_analyze_reference_image`**

Near `_run_review_cuts` in `src/video_generator/cli.py`:

```python
def _run_analyze_reference_image(args: argparse.Namespace) -> int:
    out_dir = _review_out_dir(args)
    if out_dir is None:
        return 2
    dna_path = out_dir / "reference-dna.json"
    md_path = out_dir / "reference-dna.md"

    if args.from_dna is not None:
        source_json = Path(args.from_dna).expanduser()
        try:
            payload = json.loads(source_json.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"Reference DNA error: cannot read {source_json}: {exc}", file=sys.stderr)
            return 2
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"Reference DNA error: {source_json} is not valid UTF-8 JSON: {exc}", file=sys.stderr)
            return 2
        try:
            dna = VisualDNA.from_dict(payload)
        except ReferenceDNAError as exc:
            print(f"Reference DNA error: {exc}", file=sys.stderr)
            return 2
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            if os.path.normcase(str(source_json.resolve())) != os.path.normcase(str(dna_path.resolve())):
                if dna_path.exists():
                    print(f"Reference DNA error: output already exists: {dna_path}", file=sys.stderr)
                    return 2
                dna_path.write_text(
                    json.dumps(dna.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            md_path.write_text(render_reference_dna_markdown(dna), encoding="utf-8")
        except OSError as exc:
            print(f"Reference DNA error: cannot write output: {exc}", file=sys.stderr)
            return 2
        summary = {
            "dna_path": str(dna_path.resolve()) if dna_path.exists() else str(source_json.resolve()),
            "markdown_path": str(md_path.resolve()),
            "image_type": dna.image_type,
            "has_character_bible": dna.character_bible is not None,
            "mode": "from-dna",
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Reference DNA: {summary['dna_path']}")
            print(f"Readable: {summary['markdown_path']}")
            print(f"image_type={summary['image_type']} character_bible={summary['has_character_bible']}")
        return 0

    if args.image is None:
        print("Reference DNA error: an image is required unless --from-dna is given", file=sys.stderr)
        return 2
    source = Path(args.image).expanduser()
    if not source.is_file():
        print(f"Reference DNA error: image does not exist: {source}", file=sys.stderr)
        return 2
    source = source.resolve()

    assets_dir = out_dir / "assets" / "reference"
    for existing in (dna_path, md_path):
        if existing.exists() and not args.force:
            print(f"Reference DNA error: output already exists: {existing}", file=sys.stderr)
            return 2

    try:
        signals = probe_reference_image(source)
    except FFmpegError as exc:
        print(f"Reference DNA error: cannot probe the image: {exc}", file=sys.stderr)
        return 2

    digest = sha256_of(source)
    if digest is None:
        print(f"Reference DNA error: cannot hash the image: {source}", file=sys.stderr)
        return 2

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Reference DNA error: cannot create {out_dir}: {exc}", file=sys.stderr)
        return 2

    locked_path = assets_dir / f"{digest}_{source.name}"
    if not locked_path.exists():
        try:
            locked_path.write_bytes(source.read_bytes())
        except OSError as exc:
            print(f"Reference DNA error: cannot copy the image: {exc}", file=sys.stderr)
            return 2

    relative_path = locked_path.relative_to(out_dir).as_posix()
    template = reference_dna_template(
        signals, source_image_sha256=digest, source_image_path=relative_path
    )
    try:
        dna_path.write_text(
            json.dumps(template, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(render_reference_dna_checklist(template), encoding="utf-8")
    except OSError as exc:
        print(f"Reference DNA error: cannot write output: {exc}", file=sys.stderr)
        return 2

    summary = {
        "dna_path": str(dna_path.resolve()),
        "markdown_path": str(md_path.resolve()),
        "reference_image_path": str(locked_path.resolve()),
        "reference_image_sha256": digest,
        "signals": signals.to_dict(),
        "mode": "analyze",
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Reference DNA scaffold: {summary['dna_path']}")
        print(f"Checklist: {summary['markdown_path']}")
        print(f"Locked reference image: {summary['reference_image_path']}")
        print("Fill in the semantic fields, then re-run with --from-dna to validate.")
    return 0
```

- [ ] **Step 7: Run the CLI test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_analyze_reference_image_cli -v`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 9: Commit**

```bash
git add src/video_generator/cli.py tests/test_analyze_reference_image_cli.py
git commit -m "Add analyze-reference-image CLI command"
```

---

## Task 8: CLI `review-creative-brief`

**Files:**
- Modify: `src/video_generator/cli.py` (import `CreativeBrief`, `CreativeBriefError`, `render_creative_brief_markdown` from `video_generator.domain.creative_brief`; argparse block; `_run_review_creative_brief`; dispatch line)
- Test: `tests/test_review_creative_brief_cli.py`

**Interfaces:**
- Consumes: `_review_out_dir`, `video_generator.domain.creative_brief.{CreativeBrief, CreativeBriefError, render_creative_brief_markdown}` (Task 3).
- Produces: CLI command `review-creative-brief (--project SLUG | --out-dir DIR) [--json]`. Reads `<dir>/creative-brief.json` (must already exist — written by the orchestrator, never by this command); always (re)writes `<dir>/creative-brief.md`.

- [ ] **Step 1: Write the failing CLI test file**

```python
"""tests/test_review_creative_brief_cli.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_BRIEF = {
    "schema_version": 1,
    "category_options": [{
        "category": "short narrativo", "why": "w", "suggested_structure": "s",
        "suggested_duration_seconds": 15.0, "narration": True, "dialogue": False,
        "media_format": "9:16",
    }],
    "selected_category": "short narrativo",
    "structure": {"beats": [{"name": "hook", "start_seconds": 0.0, "end_seconds": 15.0}]},
    "directions": [
        {"name": n, "concept": "c", "emotion": "e", "action": "a", "expansion": "x",
         "camera_strategy": "cs", "hook": "h", "climax": "cl", "ending": "en", "why_it_works": "w"}
        for n in ("A", "B", "C")
    ],
    "selected_direction": "A",
    "expansion_boundary": "moderate",
}


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "video_generator", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


class ReviewCreativeBriefCliTests(unittest.TestCase):
    def test_renders_markdown_for_a_valid_brief(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            project_dir.mkdir(parents=True)
            (project_dir / "creative-brief.json").write_text(json.dumps(_VALID_BRIEF), encoding="utf-8")
            result = _run_cli(
                "review-creative-brief", "--out-dir", str(project_dir), "--json", cwd=Path(tmp)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(Path(summary["markdown_path"]).is_file())
            self.assertEqual(summary["selected_direction"], "A")

    def test_missing_brief_is_an_error(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            result = _run_cli(
                "review-creative-brief", "--out-dir", str(project_dir), cwd=Path(tmp)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr)

    def test_invalid_brief_is_an_error(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            project_dir.mkdir(parents=True)
            bad = dict(_VALID_BRIEF)
            bad["directions"] = _VALID_BRIEF["directions"][:2]
            (project_dir / "creative-brief.json").write_text(json.dumps(bad), encoding="utf-8")
            result = _run_cli(
                "review-creative-brief", "--out-dir", str(project_dir), cwd=Path(tmp)
            )
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_review_creative_brief_cli -v`
Expected: FAIL — `argument command: invalid choice: 'review-creative-brief'`

- [ ] **Step 3: Add the import, argparse block, handler, and dispatch line**

Import, near the other new domain imports added in Task 7:

```python
from video_generator.domain.creative_brief import (
    CreativeBrief,
    CreativeBriefError,
    render_creative_brief_markdown,
)
```

Argparse block, near `analyze_reference_cmd`:

```python
    review_brief_cmd = subparsers.add_parser(
        "review-creative-brief",
        help="validate creative-brief.json and render creative-brief.md",
    )
    review_brief_dest = review_brief_cmd.add_mutually_exclusive_group(required=True)
    review_brief_dest.add_argument(
        "--project", help="project slug; reads/writes under projects/<slug>/"
    )
    review_brief_dest.add_argument(
        "--out-dir", help="explicit project directory"
    )
    review_brief_cmd.add_argument(
        "--json", action="store_true", help="print the summary as JSON"
    )
```

Dispatch line, near the `analyze-reference-image` one added in Task 7:

```python
    if args.command == "review-creative-brief":
        return _run_review_creative_brief(args)
```

Handler, near `_run_analyze_reference_image`:

```python
def _run_review_creative_brief(args: argparse.Namespace) -> int:
    out_dir = _review_out_dir(args)
    if out_dir is None:
        return 2
    brief_path = out_dir / "creative-brief.json"
    md_path = out_dir / "creative-brief.md"
    if not brief_path.is_file():
        print(f"Creative brief error: not found: {brief_path}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Creative brief error: cannot read {brief_path}: {exc}", file=sys.stderr)
        return 2
    try:
        brief = CreativeBrief.from_dict(payload)
    except CreativeBriefError as exc:
        print(f"Creative brief error: {exc}", file=sys.stderr)
        return 2
    try:
        md_path.write_text(render_creative_brief_markdown(brief), encoding="utf-8")
    except OSError as exc:
        print(f"Creative brief error: cannot write output: {exc}", file=sys.stderr)
        return 2
    summary = {
        "brief_path": str(brief_path.resolve()),
        "markdown_path": str(md_path.resolve()),
        "selected_category": brief.selected_category,
        "selected_direction": brief.selected_direction,
        "expansion_boundary": brief.expansion_boundary,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Creative brief: {summary['brief_path']}")
        print(f"Readable: {summary['markdown_path']}")
        print(
            f"category={summary['selected_category']} "
            f"direction={summary['selected_direction']} "
            f"boundary={summary['expansion_boundary']}"
        )
    return 0
```

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_review_creative_brief_cli -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/video_generator/cli.py tests/test_review_creative_brief_cli.py
git commit -m "Add review-creative-brief CLI command"
```

---

## Task 9: CLI `review-storyboard`

**Files:**
- Modify: `src/video_generator/cli.py` (import `Storyboard`, `StoryboardError`, `render_storyboard_markdown` from `video_generator.domain.storyboard`; argparse block; `_run_review_storyboard`; dispatch line)
- Test: `tests/test_review_storyboard_cli.py`

**Interfaces:**
- Consumes: `_review_out_dir`, `video_generator.domain.storyboard.{Storyboard, StoryboardError, render_storyboard_markdown}` (Task 4), `video_generator.domain.reference_dna.{VisualDNA, ReferenceDNAError}` (Task 1).
- Produces: CLI command `review-storyboard (--project SLUG | --out-dir DIR) [--json]`. Reads `<dir>/storyboard.json` + `<dir>/reference-dna.json`; always (re)writes `<dir>/storyboard.md`.

- [ ] **Step 1: Write the failing CLI test file**

```python
"""tests/test_review_storyboard_cli.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_DNA = {
    "schema_version": 1, "image_type": "landscape", "subject": {"main": "s"},
    "color": {"dominant": "#111111"}, "light": {"direction": "front"}, "material": {},
    "composition": {"focal_point": "center"}, "mood": ["calm"], "movement_potential": {},
    "required_elements": ["horizon line"], "flexible_elements": [],
    "signals": {"width": 100, "height": 50, "orientation": "landscape", "avg_luma": 0.5, "palette": []},
    "character_bible": None, "source_image_sha256": None, "source_image_path": None,
}

_VALID_STORYBOARD = {
    "schema_version": 1, "storyboard_id": "x",
    "shots": [{
        "shot_id": "shot_01", "time_start": 0.0, "time_end": 3.0, "purpose": "hook",
        "subject": "s", "action": "a", "camera": "c", "framing": "f", "environment": "e",
        "lighting": "l", "movement": "m", "transition": "t", "audio_intent": "ai",
    }],
    "selected_direction": "A", "expansion_boundary": "moderate",
}


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "video_generator", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _seed_project(project_dir: Path, *, storyboard: dict = _VALID_STORYBOARD) -> None:
    project_dir.mkdir(parents=True)
    (project_dir / "reference-dna.json").write_text(json.dumps(_VALID_DNA), encoding="utf-8")
    (project_dir / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")


class ReviewStoryboardCliTests(unittest.TestCase):
    def test_renders_markdown_for_a_valid_storyboard(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_project(project_dir)
            result = _run_cli("review-storyboard", "--out-dir", str(project_dir), "--json", cwd=Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["shots"], 1)
            self.assertTrue(Path(summary["markdown_path"]).is_file())

    def test_missing_storyboard_is_an_error(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            project_dir.mkdir(parents=True)
            (project_dir / "reference-dna.json").write_text(json.dumps(_VALID_DNA), encoding="utf-8")
            result = _run_cli("review-storyboard", "--out-dir", str(project_dir), cwd=Path(tmp))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr)

    def test_anchor_without_character_bible_is_an_error(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            storyboard = json.loads(json.dumps(_VALID_STORYBOARD))
            storyboard["shots"][0]["visual_anchor"] = "p"
            storyboard["shots"][0]["continuity_constraints"] = ["x"]
            _seed_project(project_dir, storyboard=storyboard)
            result = _run_cli("review-storyboard", "--out-dir", str(project_dir), cwd=Path(tmp))
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_review_storyboard_cli -v`
Expected: FAIL — `argument command: invalid choice: 'review-storyboard'`

- [ ] **Step 3: Add the import, argparse block, handler, and dispatch line**

Import, near the Task 9 imports:

```python
from video_generator.domain.storyboard import (
    Storyboard,
    StoryboardError,
    render_storyboard_markdown,
)
```

Argparse block:

```python
    review_storyboard_cmd = subparsers.add_parser(
        "review-storyboard",
        help="validate storyboard.json against reference-dna.json and render storyboard.md",
    )
    review_storyboard_dest = review_storyboard_cmd.add_mutually_exclusive_group(required=True)
    review_storyboard_dest.add_argument(
        "--project", help="project slug; reads/writes under projects/<slug>/"
    )
    review_storyboard_dest.add_argument(
        "--out-dir", help="explicit project directory"
    )
    review_storyboard_cmd.add_argument(
        "--json", action="store_true", help="print the summary as JSON"
    )
```

Dispatch line:

```python
    if args.command == "review-storyboard":
        return _run_review_storyboard(args)
```

Handler:

```python
def _run_review_storyboard(args: argparse.Namespace) -> int:
    out_dir = _review_out_dir(args)
    if out_dir is None:
        return 2
    storyboard_path = out_dir / "storyboard.json"
    dna_path = out_dir / "reference-dna.json"
    md_path = out_dir / "storyboard.md"
    for required_path in (storyboard_path, dna_path):
        if not required_path.is_file():
            print(f"Storyboard error: not found: {required_path}", file=sys.stderr)
            return 2
    try:
        storyboard_payload = json.loads(storyboard_path.read_text(encoding="utf-8"))
        dna_payload = json.loads(dna_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Storyboard error: cannot read input: {exc}", file=sys.stderr)
        return 2
    try:
        storyboard = Storyboard.from_dict(storyboard_payload)
        dna = VisualDNA.from_dict(dna_payload)
        storyboard.validate_against(dna)
    except (StoryboardError, ReferenceDNAError) as exc:
        print(f"Storyboard error: {exc}", file=sys.stderr)
        return 2
    try:
        md_path.write_text(render_storyboard_markdown(storyboard, dna), encoding="utf-8")
    except OSError as exc:
        print(f"Storyboard error: cannot write output: {exc}", file=sys.stderr)
        return 2
    anchored = sum(1 for s in storyboard.shots if s.visual_anchor is not None)
    summary = {
        "storyboard_path": str(storyboard_path.resolve()),
        "markdown_path": str(md_path.resolve()),
        "shots": len(storyboard.shots),
        "anchored_shots": anchored,
        "total_duration_seconds": storyboard.total_duration_seconds,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Storyboard: {summary['storyboard_path']}")
        print(f"Readable: {summary['markdown_path']}")
        print(
            f"{summary['shots']} shot(s), {summary['anchored_shots']} anchored, "
            f"{summary['total_duration_seconds']:.1f}s total"
        )
    return 0
```

(`VisualDNA` and `ReferenceDNAError` are already imported from Task 7's edit — do not re-import.)

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_review_storyboard_cli -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/video_generator/cli.py tests/test_review_storyboard_cli.py
git commit -m "Add review-storyboard CLI command"
```

---

## Task 10: CLI `storyboard-lock`

**Files:**
- Modify: `src/video_generator/cli.py` (import `StoryboardLock` from `video_generator.domain.storyboard`; argparse block; `_run_storyboard_lock`; dispatch line)
- Test: `tests/test_storyboard_lock_cli.py`

**Interfaces:**
- Consumes: `_review_out_dir`, `sha256_of`, `datetime`/`timezone` (already imported for `visual-lock`), `VisualDNA`/`ReferenceDNAError` (Task 1, already imported), `CreativeBrief`/`CreativeBriefError` (Task 3, already imported), `Storyboard`/`StoryboardError` (Task 4, already imported), `StoryboardLock` (Task 4, new import).
- Produces: CLI command `storyboard-lock (--project SLUG | --out-dir DIR) --approved-by NAME [--approved-at ISO8601] [--force] [--json]`. Writes `<dir>/storyboard-lock.json`.

- [ ] **Step 1: Write the failing CLI test file**

```python
"""tests/test_storyboard_lock_cli.py"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE_IMAGE = Path(__file__).parent / "fixtures" / "reference_image_small.png"

_VALID_DNA = {
    "schema_version": 1, "image_type": "landscape", "subject": {"main": "s"},
    "color": {"dominant": "#111111"}, "light": {"direction": "front"}, "material": {},
    "composition": {"focal_point": "center"}, "mood": ["calm"], "movement_potential": {},
    "required_elements": ["horizon line"], "flexible_elements": [],
    "signals": {"width": 4, "height": 4, "orientation": "square", "avg_luma": 0.5, "palette": []},
    "character_bible": None, "source_image_sha256": None,
    "source_image_path": "assets/reference/ref.png",
}

_VALID_BRIEF = {
    "schema_version": 1,
    "category_options": [{
        "category": "c", "why": "w", "suggested_structure": "s",
        "suggested_duration_seconds": 15.0, "narration": True, "dialogue": False, "media_format": "9:16",
    }],
    "selected_category": "c",
    "structure": {"beats": [{"name": "hook", "start_seconds": 0.0, "end_seconds": 15.0}]},
    "directions": [
        {"name": n, "concept": "c", "emotion": "e", "action": "a", "expansion": "x",
         "camera_strategy": "cs", "hook": "h", "climax": "cl", "ending": "en", "why_it_works": "w"}
        for n in ("A", "B", "C")
    ],
    "selected_direction": "A", "expansion_boundary": "moderate",
}

_VALID_STORYBOARD = {
    "schema_version": 1, "storyboard_id": "x",
    "shots": [{
        "shot_id": "shot_01", "time_start": 0.0, "time_end": 3.0, "purpose": "hook",
        "subject": "s", "action": "a", "camera": "c", "framing": "f", "environment": "e",
        "lighting": "l", "movement": "m", "transition": "t", "audio_intent": "ai",
    }],
    "selected_direction": "A", "expansion_boundary": "moderate",
}


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "video_generator", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _seed_project(project_dir: Path) -> str:
    """Seeds a full project dir with a locked reference image; returns its sha256."""
    import hashlib

    project_dir.mkdir(parents=True)
    ref_dir = project_dir / "assets" / "reference"
    ref_dir.mkdir(parents=True)
    shutil.copyfile(_REFERENCE_IMAGE, ref_dir / "ref.png")
    digest = hashlib.sha256((ref_dir / "ref.png").read_bytes()).hexdigest()
    dna = json.loads(json.dumps(_VALID_DNA))
    dna["source_image_sha256"] = digest
    (project_dir / "reference-dna.json").write_text(json.dumps(dna), encoding="utf-8")
    (project_dir / "creative-brief.json").write_text(json.dumps(_VALID_BRIEF), encoding="utf-8")
    (project_dir / "storyboard.json").write_text(json.dumps(_VALID_STORYBOARD), encoding="utf-8")
    return digest


class StoryboardLockCliTests(unittest.TestCase):
    def test_writes_a_lock_with_four_matching_digests(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_project(project_dir)
            result = _run_cli(
                "storyboard-lock", "--out-dir", str(project_dir),
                "--approved-by", "Isadora", "--json", cwd=Path(tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            lock = json.loads(Path(summary["lock_path"]).read_text(encoding="utf-8"))
            self.assertEqual(lock["approved_by"], "Isadora")
            self.assertEqual(len(lock["storyboard_sha256"]), 64)

    def test_refuses_to_overwrite_without_force(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_project(project_dir)
            _run_cli("storyboard-lock", "--out-dir", str(project_dir), "--approved-by", "A", cwd=Path(tmp))
            result = _run_cli(
                "storyboard-lock", "--out-dir", str(project_dir), "--approved-by", "A", cwd=Path(tmp)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)

    def test_rejects_a_changed_reference_image(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_project(project_dir)
            (project_dir / "assets" / "reference" / "ref.png").write_bytes(b"different bytes")
            result = _run_cli(
                "storyboard-lock", "--out-dir", str(project_dir), "--approved-by", "A", cwd=Path(tmp)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no longer matches", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard_lock_cli -v`
Expected: FAIL — `argument command: invalid choice: 'storyboard-lock'`

- [ ] **Step 3: Add the import, argparse block, handler, and dispatch line**

Extend the Task 9 import to also pull in `StoryboardLock`:

```python
from video_generator.domain.storyboard import (
    Storyboard,
    StoryboardError,
    StoryboardLock,
    render_storyboard_markdown,
)
```

Argparse block:

```python
    storyboard_lock_cmd = subparsers.add_parser(
        "storyboard-lock",
        help="freeze an approved storyboard and its inputs by SHA-256",
    )
    storyboard_lock_dest = storyboard_lock_cmd.add_mutually_exclusive_group(required=True)
    storyboard_lock_dest.add_argument(
        "--project", help="project slug; reads/writes under projects/<slug>/"
    )
    storyboard_lock_dest.add_argument(
        "--out-dir", help="explicit project directory"
    )
    storyboard_lock_cmd.add_argument(
        "--approved-by", required=True, help="who approved this storyboard"
    )
    storyboard_lock_cmd.add_argument(
        "--approved-at", help="ISO-8601 UTC timestamp (default: now)"
    )
    storyboard_lock_cmd.add_argument(
        "--force", action="store_true", help="allow overwriting an existing lock"
    )
    storyboard_lock_cmd.add_argument(
        "--json", action="store_true", help="print the summary as JSON"
    )
```

Dispatch line:

```python
    if args.command == "storyboard-lock":
        return _run_storyboard_lock(args)
```

Handler:

```python
def _run_storyboard_lock(args: argparse.Namespace) -> int:
    out_dir = _review_out_dir(args)
    if out_dir is None:
        return 2
    dna_path = out_dir / "reference-dna.json"
    brief_path = out_dir / "creative-brief.json"
    storyboard_path = out_dir / "storyboard.json"
    lock_path = out_dir / "storyboard-lock.json"

    if lock_path.exists() and not args.force:
        print(f"Storyboard lock error: output already exists: {lock_path}", file=sys.stderr)
        return 2
    for required_path in (dna_path, brief_path, storyboard_path):
        if not required_path.is_file():
            print(f"Storyboard lock error: not found: {required_path}", file=sys.stderr)
            return 2

    try:
        dna = VisualDNA.from_dict(json.loads(dna_path.read_text(encoding="utf-8")))
        CreativeBrief.from_dict(json.loads(brief_path.read_text(encoding="utf-8")))
        storyboard = Storyboard.from_dict(json.loads(storyboard_path.read_text(encoding="utf-8")))
        storyboard.validate_against(dna)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Storyboard lock error: cannot read input: {exc}", file=sys.stderr)
        return 2
    except (ReferenceDNAError, CreativeBriefError, StoryboardError) as exc:
        print(f"Storyboard lock error: {exc}", file=sys.stderr)
        return 3

    if not dna.source_image_path:
        print("Storyboard lock error: reference-dna.json has no source_image_path", file=sys.stderr)
        return 2
    reference_image_path = (out_dir / dna.source_image_path).resolve()
    reference_digest = sha256_of(reference_image_path)
    if reference_digest is None:
        print(f"Storyboard lock error: reference image is missing: {reference_image_path}", file=sys.stderr)
        return 2
    if reference_digest != dna.source_image_sha256:
        print("Storyboard lock error: reference image no longer matches reference-dna.json", file=sys.stderr)
        return 2

    approved_at = args.approved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        lock = StoryboardLock(
            lock_id=f"{storyboard.storyboard_id}-lock",
            approved_by=args.approved_by,
            approved_at=approved_at,
            reference_image_sha256=reference_digest,
            reference_dna_sha256=sha256_of(dna_path),
            creative_brief_sha256=sha256_of(brief_path),
            storyboard_sha256=sha256_of(storyboard_path),
        )
    except StoryboardError as exc:
        print(f"Storyboard lock error: {exc}", file=sys.stderr)
        return 2

    try:
        lock_path.write_text(
            json.dumps(lock.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Storyboard lock error: cannot write output: {exc}", file=sys.stderr)
        return 2

    summary = {
        "lock_path": str(lock_path.resolve()),
        "approved_by": lock.approved_by,
        "approved_at": lock.approved_at,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Storyboard lock: {summary['lock_path']}")
        print(f"Approved by {summary['approved_by']} at {summary['approved_at']}")
    return 0
```

(`CreativeBrief`, `CreativeBriefError`, `VisualDNA`, `ReferenceDNAError`, `datetime`, `timezone`, `sha256_of` are already imported from earlier tasks/existing code — do not re-import.)

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard_lock_cli -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/video_generator/cli.py tests/test_storyboard_lock_cli.py
git commit -m "Add storyboard-lock CLI command"
```

---

## Task 11: CLI `storyboard-to-shots`

**Files:**
- Modify: `src/video_generator/cli.py` (import `lock_violations`, `storyboard_to_shot_plan`; argparse block; `_run_storyboard_to_shots`; dispatch line)
- Test: `tests/test_storyboard_to_shots_cli.py`

**Interfaces:**
- Consumes: `_review_out_dir`, `sha256_of`, `VisualDNA`/`ReferenceDNAError`, `Storyboard`/`StoryboardError`/`StoryboardLock`/`lock_violations` (new import), `video_generator.domain.planning.{storyboard_to_shot_plan, PlanningError}` (Task 5, new import).
- Produces: CLI command `storyboard-to-shots (--project SLUG | --out-dir DIR) [--force] [--json]`. Refuses to run without an intact `storyboard-lock.json`; writes `<dir>/shot-plan.json` + `<dir>/asset-requirements.json`.

- [ ] **Step 1: Write the failing CLI test file**

```python
"""tests/test_storyboard_to_shots_cli.py"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE_IMAGE = Path(__file__).parent / "fixtures" / "reference_image_small.png"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "video_generator", *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


def _seed_and_lock(project_dir: Path, cwd: Path) -> None:
    project_dir.mkdir(parents=True)
    ref_dir = project_dir / "assets" / "reference"
    ref_dir.mkdir(parents=True)
    shutil.copyfile(_REFERENCE_IMAGE, ref_dir / "ref.png")
    digest = hashlib.sha256((ref_dir / "ref.png").read_bytes()).hexdigest()
    dna = {
        "schema_version": 1, "image_type": "landscape", "subject": {"main": "s"},
        "color": {"dominant": "#111111"}, "light": {"direction": "front"}, "material": {},
        "composition": {"focal_point": "center"}, "mood": ["calm"], "movement_potential": {},
        "required_elements": ["horizon line"], "flexible_elements": [],
        "signals": {"width": 4, "height": 4, "orientation": "square", "avg_luma": 0.5, "palette": []},
        "character_bible": None, "source_image_sha256": digest,
        "source_image_path": "assets/reference/ref.png",
    }
    brief = {
        "schema_version": 1,
        "category_options": [{
            "category": "c", "why": "w", "suggested_structure": "s",
            "suggested_duration_seconds": 15.0, "narration": True, "dialogue": False, "media_format": "9:16",
        }],
        "selected_category": "c",
        "structure": {"beats": [{"name": "hook", "start_seconds": 0.0, "end_seconds": 15.0}]},
        "directions": [
            {"name": n, "concept": "c", "emotion": "e", "action": "a", "expansion": "x",
             "camera_strategy": "cs", "hook": "h", "climax": "cl", "ending": "en", "why_it_works": "w"}
            for n in ("A", "B", "C")
        ],
        "selected_direction": "A", "expansion_boundary": "moderate",
    }
    storyboard = {
        "schema_version": 1, "storyboard_id": "x",
        "shots": [{
            "shot_id": "shot_01", "time_start": 0.0, "time_end": 3.0, "purpose": "hook",
            "subject": "s", "action": "a", "camera": "c", "framing": "f", "environment": "e",
            "lighting": "l", "movement": "m", "transition": "t", "audio_intent": "ai",
        }],
        "selected_direction": "A", "expansion_boundary": "moderate",
    }
    (project_dir / "reference-dna.json").write_text(json.dumps(dna), encoding="utf-8")
    (project_dir / "creative-brief.json").write_text(json.dumps(brief), encoding="utf-8")
    (project_dir / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
    lock_result = _run_cli(
        "storyboard-lock", "--out-dir", str(project_dir), "--approved-by", "Isadora", cwd=cwd
    )
    assert lock_result.returncode == 0, lock_result.stderr


class StoryboardToShotsCliTests(unittest.TestCase):
    def test_writes_shot_plan_and_asset_requirements(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_and_lock(project_dir, Path(tmp))
            result = _run_cli(
                "storyboard-to-shots", "--out-dir", str(project_dir), "--json", cwd=Path(tmp)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["shots"], 1)
            shot_plan = json.loads(Path(summary["shot_plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(shot_plan["shots"]), 1)

    def test_refuses_without_a_lock(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_and_lock(project_dir, Path(tmp))
            (project_dir / "storyboard-lock.json").unlink()
            result = _run_cli("storyboard-to-shots", "--out-dir", str(project_dir), cwd=Path(tmp))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr)

    def test_refuses_when_storyboard_changed_after_the_lock(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projects" / "x"
            _seed_and_lock(project_dir, Path(tmp))
            storyboard = json.loads((project_dir / "storyboard.json").read_text(encoding="utf-8"))
            storyboard["shots"][0]["purpose"] = "edited after the lock"
            (project_dir / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
            result = _run_cli("storyboard-to-shots", "--out-dir", str(project_dir), cwd=Path(tmp))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sha256 changed", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard_to_shots_cli -v`
Expected: FAIL — `argument command: invalid choice: 'storyboard-to-shots'`

- [ ] **Step 3: Add the import, argparse block, handler, and dispatch line**

Extend the Task 10 import to also pull in `lock_violations`:

```python
from video_generator.domain.storyboard import (
    Storyboard,
    StoryboardError,
    StoryboardLock,
    lock_violations,
    render_storyboard_markdown,
)
from video_generator.domain.planning import storyboard_to_shot_plan
```

(`PlanningError` is already imported from `video_generator.domain` for `plan-scenes` — confirm and reuse it rather than re-importing.)

Argparse block:

```python
    storyboard_to_shots_cmd = subparsers.add_parser(
        "storyboard-to-shots",
        help="compile a locked storyboard into ShotPlan/AssetRequirements for resolve-assets",
    )
    storyboard_to_shots_dest = storyboard_to_shots_cmd.add_mutually_exclusive_group(required=True)
    storyboard_to_shots_dest.add_argument(
        "--project", help="project slug; reads/writes under projects/<slug>/"
    )
    storyboard_to_shots_dest.add_argument(
        "--out-dir", help="explicit project directory"
    )
    storyboard_to_shots_cmd.add_argument(
        "--force", action="store_true", help="allow overwriting existing outputs"
    )
    storyboard_to_shots_cmd.add_argument(
        "--json", action="store_true", help="print the summary as JSON"
    )
```

Dispatch line:

```python
    if args.command == "storyboard-to-shots":
        return _run_storyboard_to_shots(args)
```

Handler:

```python
def _run_storyboard_to_shots(args: argparse.Namespace) -> int:
    out_dir = _review_out_dir(args)
    if out_dir is None:
        return 2
    dna_path = out_dir / "reference-dna.json"
    storyboard_path = out_dir / "storyboard.json"
    lock_path = out_dir / "storyboard-lock.json"
    shot_plan_path = out_dir / "shot-plan.json"
    assets_path = out_dir / "asset-requirements.json"

    for existing in (shot_plan_path, assets_path):
        if existing.exists() and not args.force:
            print(f"Storyboard handoff error: output already exists: {existing}", file=sys.stderr)
            return 2
    for required_path in (dna_path, storyboard_path, lock_path):
        if not required_path.is_file():
            print(f"Storyboard handoff error: not found: {required_path}", file=sys.stderr)
            return 2

    try:
        dna = VisualDNA.from_dict(json.loads(dna_path.read_text(encoding="utf-8")))
        storyboard = Storyboard.from_dict(json.loads(storyboard_path.read_text(encoding="utf-8")))
        lock = StoryboardLock.from_dict(json.loads(lock_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Storyboard handoff error: cannot read input: {exc}", file=sys.stderr)
        return 2
    except (ReferenceDNAError, StoryboardError) as exc:
        print(f"Storyboard handoff error: {exc}", file=sys.stderr)
        return 2

    reference_image_path = (
        str((out_dir / dna.source_image_path).resolve()) if dna.source_image_path else ""
    )
    problems = lock_violations(
        lock,
        digest_of=sha256_of,
        paths={
            "reference_image": reference_image_path,
            "reference_dna": str(dna_path.resolve()),
            "creative_brief": str((out_dir / "creative-brief.json").resolve()),
            "storyboard": str(storyboard_path.resolve()),
        },
    )
    if problems:
        for problem in problems:
            print(f"Storyboard handoff error: {problem}", file=sys.stderr)
        return 3

    try:
        storyboard.validate_against(dna)
        shot_plan, asset_requirements = storyboard_to_shot_plan(storyboard, dna)
    except (StoryboardError, PlanningError) as exc:
        print(f"Storyboard handoff error: {exc}", file=sys.stderr)
        return 2

    try:
        shot_plan_path.write_text(
            json.dumps(shot_plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assets_path.write_text(
            json.dumps(asset_requirements.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Storyboard handoff error: cannot write output: {exc}", file=sys.stderr)
        return 2

    summary = {
        "shot_plan_path": str(shot_plan_path.resolve()),
        "asset_requirements_path": str(assets_path.resolve()),
        "shots": len(shot_plan.shots),
        "asset_requirements": len(asset_requirements.requirements),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ShotPlan: {summary['shot_plan_path']}")
        print(f"AssetRequirements: {summary['asset_requirements_path']}")
        print(f"{summary['shots']} shot(s), {summary['asset_requirements']} requirement(s)")
        print("Ready for: resolve-assets")
    return 0
```

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest tests.test_storyboard_to_shots_cli -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/video_generator/cli.py tests/test_storyboard_to_shots_cli.py
git commit -m "Add storyboard-to-shots CLI command: handoff to resolve-assets"
```

---

## Task 12: `domain/__init__.py` exports, the skill, and documentation

**Files:**
- Modify: `src/video_generator/domain/__init__.py` (re-export the three new modules' public names, following the existing per-module block pattern)
- Create: `.claude/skills/direcao-a-partir-de-imagem/SKILL.md`
- Modify: `README.md` (command list + one worked example)
- Modify: `CLAUDE.md` ("CLI disponível hoje" line)
- Modify: `docs/WORKFLOWS.md` (new section)
- Modify: `docs/ARCHITECTURE.md` (one paragraph)

**Interfaces:** none new — this task wires up existing names from Tasks 1-5 for convenient import (`from video_generator.domain import VisualDNA, CreativeBrief, Storyboard, ...`) and writes documentation. No test file — verified by the existing test suite still importing cleanly and by a manual `python -c "import video_generator.domain"` check.

- [ ] **Step 1: Add the three new import blocks to `domain/__init__.py`**

Append, after the existing `from video_generator.domain.planning import (...)` block and before the final `__all__` list starts, three new blocks:

```python
from video_generator.domain.reference_dna import (
    CHARACTER_IMAGE_TYPES,
    IMAGE_TYPES,
    MOVEMENT_POTENTIAL_KEYS,
    CharacterBible,
    ReferenceDNAError,
    ReferenceImageSignals,
    VisualDNA,
    reference_dna_template,
    render_reference_dna_checklist,
    render_reference_dna_markdown,
)
from video_generator.domain.creative_brief import (
    EXPANSION_BOUNDARIES,
    CreativeBrief,
    CreativeBriefError,
    CreativeDirection,
    NarrativeStructureOption,
    StructureBeat,
    VideoCategoryOption,
    render_creative_brief_markdown,
)
from video_generator.domain.storyboard import (
    BANNED_CONTENT_TERMS,
    Storyboard,
    StoryboardError,
    StoryboardLock,
    StoryboardShot,
    default_continuity_constraints,
    lock_violations,
    render_storyboard_markdown,
)
```

- [ ] **Step 2: Extend `__all__`**

Append to the existing `__all__` list in the same file:

```python
    "CHARACTER_IMAGE_TYPES",
    "IMAGE_TYPES",
    "MOVEMENT_POTENTIAL_KEYS",
    "CharacterBible",
    "ReferenceDNAError",
    "ReferenceImageSignals",
    "VisualDNA",
    "reference_dna_template",
    "render_reference_dna_checklist",
    "render_reference_dna_markdown",
    "EXPANSION_BOUNDARIES",
    "CreativeBrief",
    "CreativeBriefError",
    "CreativeDirection",
    "NarrativeStructureOption",
    "StructureBeat",
    "VideoCategoryOption",
    "render_creative_brief_markdown",
    "BANNED_CONTENT_TERMS",
    "Storyboard",
    "StoryboardError",
    "StoryboardLock",
    "StoryboardShot",
    "default_continuity_constraints",
    "lock_violations",
    "render_storyboard_markdown",
]
```

Note: `lock_violations` already exists in `__all__`... check first — `domain.curation`'s `lock_violations` is **not** currently re-exported through `domain/__init__.py` (only `video_generator.curation`'s impure wrapper is), so there is no name collision; if a future check finds one, keep both under their fully-qualified module paths instead of flattening into `domain/__init__.py`.

- [ ] **Step 3: Verify the package still imports cleanly**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -c "import video_generator.domain; print('ok')"`
Expected: prints `ok`, no `ImportError`

- [ ] **Step 4: Run the full suite**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, no regressions

- [ ] **Step 5: Write the skill**

Create `.claude/skills/direcao-a-partir-de-imagem/SKILL.md`:

```markdown
---
name: direcao-a-partir-de-imagem
description: >-
  Planejar um vídeo a partir de uma imagem de referência (um personagem, produto,
  cena ou qualquer IP visual) mantendo consistência visual com a imagem, sem
  gerar vídeo por IA. Use quando o pedido for dirigir/planejar um vídeo a partir
  de uma imagem, extrair o "DNA visual" de uma referência, ou criar um storyboard
  consistente com um personagem/objeto específico. Termina num ShotPlan pronto
  para resolve-assets; não substitui o skill producao-audiovisual, que assume
  a partir daí.
---

# Direção de vídeo a partir de imagem de referência

Segue o design em
[`docs/superpowers/specs/2026-09-11-image-director-design.md`](../../../docs/superpowers/specs/2026-09-11-image-director-design.md).
Leia antes: [`AGENTS.md`](../../../AGENTS.md).

## Premissa que não se negocia

Nenhum comando desta pipeline "analisa" a imagem ou "gera" direções
criativas sozinho — isso violaria a regra do `AGENTS.md` de que o motor nunca
chama a API do próprio orquestrador. **Você (Claude Code) escreve o conteúdo
de cada artefato olhando a imagem e raciocinando**, exatamente como já faz
hoje para `visual_intent`/tradução de beats. Os comandos só validam schema,
renderizam o `.md` de revisão humana, e fazem a ponte para o pipeline
existente. Nunca pule uma aprovação humana para acelerar.

## O fluxo

1. `analyze-reference-image <imagem> --project <slug>` — sinais objetivos
   (dimensões, luma, paleta) via FFmpeg; escreve um scaffold de
   `reference-dna.json` com os campos semânticos vazios.
2. Você preenche `reference-dna.json` olhando a imagem: tipo, sujeito, cor,
   luz, material, composição, mood, movement_potential, required_elements,
   flexible_elements e, se for um personagem/IP (`image_type` = `character`
   ou `stylized_ip`), o `character_bible` completo (incluindo `forbidden`).
3. `analyze-reference-image --from-dna reference-dna.json --project <slug>`
   valida e gera `reference-dna.md` — mostre ao usuário.
4. Você propõe 3-5 categorias de vídeo (`category_options`), uma estrutura
   narrativa (`structure`) e **exatamente 3** direções criativas
   (`directions`), e escreve tudo em `creative-brief.json` — ainda sem
   escolher nada.
5. Peça ao usuário para escolher categoria, direção (`A`, `B`, `C`, `A+B`,
   `B+C`, `A+C`, ou pedir para regenerar) e o limite de expansão visual
   (`conservative` | `moderate` | `bold`, default `moderate`). Grave as
   escolhas em `creative-brief.json`.
6. `review-creative-brief --project <slug>` valida e gera `creative-brief.md`
   — mostre ao usuário antes de seguir.
7. Você escreve `storyboard.json`: um shot por vez, cada um com
   `time_start`/`time_end` contíguos a partir de 0, e para todo shot que
   precisar do personagem/objeto de referência, `visual_anchor` +
   `continuity_constraints` (use `default_continuity_constraints` como ponto
   de partida, nunca invente uma restrição que não esteja na DNA).
8. `review-storyboard --project <slug>` valida contra `reference-dna.json` e
   gera `storyboard.md` — **aprovação humana obrigatória aqui**.
9. Só depois da aprovação: `storyboard-lock --project <slug> --approved-by
   <nome>`.
10. `storyboard-to-shots --project <slug>` gera `shot-plan.json` +
    `asset-requirements.json`. A partir daqui, use a skill
    `producao-audiovisual` / `resolve-assets` normalmente.

## Regras que nunca mudam

- Nunca invente um novo protagonista, logo, texto de marca, watermark, ou IP
  externa não fornecida — em nenhum nível de expansão.
- Consistência do personagem nesta v1 vem de **reusar literalmente** a
  imagem de referência como asset travado por SHA-256 em todo shot
  ancorado — não há geração de novas poses/ângulos.
- `storyboard-to-shots` se recusa a rodar sem um `storyboard-lock.json`
  íntegro (verificado por SHA-256) — nunca contorne isso editando o lock à
  mão.
```

- [ ] **Step 6: Update `README.md`**

Find the command list section and add the five new commands with the same style as the existing entries (`review-cuts`, `apply-cuts`, etc.), plus one short worked example:

```markdown
- `analyze-reference-image <image> (--project <slug> | --out-dir <dir>) [--from-dna <path>] [--force] [--json]`
  — scaffold or validate a reference image's visual DNA (`reference-dna.json`/`.md`).
- `review-creative-brief (--project <slug> | --out-dir <dir>) [--json]`
  — validate `creative-brief.json` and render `creative-brief.md`.
- `review-storyboard (--project <slug> | --out-dir <dir>) [--json]`
  — validate `storyboard.json` against `reference-dna.json` and render `storyboard.md`.
- `storyboard-lock (--project <slug> | --out-dir <dir>) --approved-by <name> [--approved-at <iso>] [--force] [--json]`
  — freeze an approved storyboard by SHA-256 into `storyboard-lock.json`.
- `storyboard-to-shots (--project <slug> | --out-dir <dir>) [--force] [--json]`
  — compile a locked storyboard into `shot-plan.json` + `asset-requirements.json`, ready for `resolve-assets`.

Example — directing a video from a reference image of a character:

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m video_generator analyze-reference-image reference.png --project pererekossauro_01
# fill in projects/pererekossauro_01/reference-dna.json by hand, then:
& $py -m video_generator analyze-reference-image --from-dna projects/pererekossauro_01/reference-dna.json --project pererekossauro_01
# author creative-brief.json and storyboard.json by hand, then:
& $py -m video_generator review-creative-brief --project pererekossauro_01
& $py -m video_generator review-storyboard --project pererekossauro_01
& $py -m video_generator storyboard-lock --project pererekossauro_01 --approved-by "Isadora"
& $py -m video_generator storyboard-to-shots --project pererekossauro_01
& $py -m video_generator resolve-assets --shot-plan projects/pererekossauro_01/shot-plan.json --assets projects/pererekossauro_01/asset-requirements.json --project pererekossauro_01
```
```

(Confirm `resolve-assets`'s exact flag names by reading its existing `README.md` entry before writing this block — reuse them verbatim rather than guessing.)

- [ ] **Step 7: Update `CLAUDE.md`**

In the "CLI disponível hoje" paragraph, add after `resolve-assets`:
`, analyze-reference-image`/`review-creative-brief`/`review-storyboard`/`storyboard-lock`/`storyboard-to-shots` (planeja um vídeo a partir de uma imagem de referência mantendo consistência visual, sem gerar vídeo por IA; ver skill `direcao-a-partir-de-imagem`)`.

- [ ] **Step 8: Update `docs/WORKFLOWS.md`**

Add a new section (mirroring the existing "Scene Planner / Shot Planner" section's structure and tone) titled "Direção a partir de imagem de referência", stating: this is an alternative entry point ahead of `plan-scenes`'s normal script-first path; it ends at a `ShotPlan`/`AssetRequirements` pair with two additive `Shot` fields (`visual_anchor`, `continuity_constraints`); DNA extraction and creative-direction content are always orchestrator-authored, never derived by code; character consistency in this increment comes from locking the single reference image as a shared asset, not from generation.

- [ ] **Step 9: Update `docs/ARCHITECTURE.md`**

Add one paragraph: `VisualDNA` / `CreativeBrief` / `Storyboard` realize the `Storyboard` contract candidate named in `AGENTS.md` for the reference-image pipeline; `reference_dna.py` and `creative_brief.py` are kept as separate modules from `storyboard.py` because the DNA/creative-decision layer and the shot-compilation layer are expected to evolve independently; note the "orchestrator authors, domain validates" split as the explicit pattern this increment names for the first time (it was implicit in `visual_intent`/beat-translation provenance before).

- [ ] **Step 10: Commit**

```bash
git add src/video_generator/domain/__init__.py .claude/skills/direcao-a-partir-de-imagem/SKILL.md README.md CLAUDE.md docs/WORKFLOWS.md docs/ARCHITECTURE.md
git commit -m "Wire up domain exports, add the image-director skill, update docs"
```

---

## Task 13: End-to-end walkthrough with a placeholder character image + final verification

**Files:**
- Create: `output/image-director-demo/` (scratch walkthrough output — matches `AGENTS.md`'s "never commit renders/media"; this directory is **not** committed, only referenced in the final report)
- Create (temporary, for the demo only): a small synthetic placeholder character PNG, generated with the same stdlib PNG writer as Task 2's fixture
- No new source files — this task exercises Tasks 1-12 together and is the "show a worked example using a character image" deliverable from the original request

This task has no TDD cycle (nothing new to unit-test) — it is a manual run-through plus the mandatory full-suite gate.

- [ ] **Step 1: Generate a placeholder character reference image**

Run a short throwaway script (do not commit it) that reuses the Task 2 PNG writer to draw a simple 64x64 "placeholder creature" — a green ellipse-ish blob with two dark eyes — clearly labeled as a stand-in, not real character art:

```powershell
.\.venv\Scripts\python.exe -c @"
import sys
sys.path.insert(0, 'tests/fixtures')
from generate_reference_image_small import write_png
from pathlib import Path

width, height = 64, 64
colours = []
for y in range(height):
    row = []
    for x in range(width):
        cx, cy = width / 2, height / 2 + 6
        in_body = ((x - cx) / 26) ** 2 + ((y - cy) / 20) ** 2 <= 1
        in_left_eye = ((x - (cx - 9)) / 4) ** 2 + ((y - (cy - 8)) / 4) ** 2 <= 1
        in_right_eye = ((x - (cx + 9)) / 4) ** 2 + ((y - (cy - 8)) / 4) ** 2 <= 1
        if in_left_eye or in_right_eye:
            row.append((20, 20, 20))
        elif in_body:
            row.append((60, 160, 70))
        else:
            row.append((235, 235, 225))
    colours.append(row)

path = Path('output/image-director-demo/placeholder_character.png')
path.parent.mkdir(parents=True, exist_ok=True)
raw = b''.join(b'\x00' + b''.join(bytes(p) for p in row) for row in colours)
import zlib, struct
def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
path.write_bytes(png)
print('wrote', path)
"@
```

Expected: `output/image-director-demo/placeholder_character.png` exists (a simple green blob with two eyes, ~64x64).

- [ ] **Step 2: Run `analyze-reference-image`**

```powershell
& $py -m video_generator analyze-reference-image output/image-director-demo/placeholder_character.png --out-dir output/image-director-demo --json
```

Expected: exit 0; `output/image-director-demo/reference-dna.json` and `.md` exist; the locked copy exists under `output/image-director-demo/assets/reference/`.

- [ ] **Step 3: Fill in `reference-dna.json` by hand (this is the orchestrator's authored step)**

Edit `output/image-director-demo/reference-dna.json` directly (Edit tool), setting real content, e.g.:

```json
{
  "image_type": "character",
  "subject": {"main": "creature placeholder", "expression": "neutral, wide-eyed"},
  "color": {"dominant": "#3CA046", "secondary": "#EBEBE1", "accent": "#141414"},
  "movement_potential": {"character": ["blink"], "camera": ["push_in", "static_hold"]},
  "mood": ["simple", "placeholder"],
  "required_elements": ["green body", "two dark round eyes", "no visible limbs"],
  "flexible_elements": ["background"],
  "character_bible": {
    "anatomy": "single rounded body, no visible limbs",
    "proportions": "wide oval body, eyes in the upper third",
    "eyes": "two, round, dark, evenly spaced",
    "mouth": "none drawn",
    "limbs": "none drawn",
    "colors": "green body, off-white background is not part of the character",
    "texture": "flat, no shading",
    "accessories": "none",
    "base_expression": "neutral",
    "deformation_limits": "must stay a single rounded body with exactly two eyes",
    "forbidden": ["extra eyes", "visible limbs", "mouth", "color change", "species change"]
  }
}
```

(Keep `signals`, `source_image_sha256`, `source_image_path` exactly as the scaffold wrote them — do not edit those.)

- [ ] **Step 4: Validate the filled-in DNA**

```powershell
& $py -m video_generator analyze-reference-image --from-dna output/image-director-demo/reference-dna.json --out-dir output/image-director-demo --json
```

Expected: exit 0; `reference-dna.md` now shows the character bible.

- [ ] **Step 5: Author `creative-brief.json` by hand**

Write `output/image-director-demo/creative-brief.json` with 2-3 `category_options`, a `structure` (e.g. hook/development/payoff beats totalling 15s), exactly 3 `directions`, `selected_category`, `selected_direction` (e.g. `"A"`), and `expansion_boundary: "moderate"` — same shape as the Task 8/9 test fixtures, with real prose content instead of placeholder `"c"`/`"w"` strings.

- [ ] **Step 6: Validate the creative brief**

```powershell
& $py -m video_generator review-creative-brief --out-dir output/image-director-demo --json
```

Expected: exit 0; `creative-brief.md` exists and marks the chosen category/direction.

- [ ] **Step 7: Author `storyboard.json` by hand**

Write 3-5 `StoryboardShot` entries covering 15s, contiguous from 0, at least one shot with `visual_anchor: "creature placeholder"` and `continuity_constraints` drawn from the DNA's `required_elements`/`forbidden` (e.g. `["green body", "two dark round eyes"]`).

- [ ] **Step 8: Validate the storyboard**

```powershell
& $py -m video_generator review-storyboard --out-dir output/image-director-demo --json
```

Expected: exit 0; `storyboard.md` reports how many shots anchor the subject.

- [ ] **Step 9: Lock the storyboard**

```powershell
& $py -m video_generator storyboard-lock --out-dir output/image-director-demo --approved-by "Isadora" --json
```

Expected: exit 0; `storyboard-lock.json` exists.

- [ ] **Step 10: Hand off to the existing pipeline**

```powershell
& $py -m video_generator storyboard-to-shots --out-dir output/image-director-demo --json
```

Expected: exit 0; `shot-plan.json` + `asset-requirements.json` exist and are ready for `resolve-assets` (do not actually run `resolve-assets` in this task — that would reach into the network/local library, which is out of scope for this plan's verification and belongs to the already-existing, unmodified pipeline).

- [ ] **Step 11: Run the full suite one final time**

Run: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS, full count reported (compare against the pre-Task-1 baseline count to confirm every new test actually ran)

- [ ] **Step 12: Run `doctor` to confirm nothing else regressed**

Run: `.\.venv\Scripts\python.exe -m video_generator doctor`
Expected: exit 0, environment report unchanged from before this plan

- [ ] **Step 13: Save memory of this increment**

Write a new memory file at `C:\Users\Thales\.claude\projects\C--GIT-video-generator\memory\image-director-v1.md` (frontmatter `type: project`) recording: what was added (five checkpoint commands + three domain contracts + a bridge into `ShotPlan`), the key architectural decision (analysis/creative-direction content is always orchestrator-authored, never a code path — forced by `AGENTS.md`'s no-self-API rule and the absence of any vision-model dependency), the v1 limitation (character consistency via literal reference-image reuse, not generation; downstream relevance/direction layers don't yet enforce `continuity_constraints`), and add a one-line pointer to it in `MEMORY.md`.

- [ ] **Step 14: Final commit**

```bash
git add docs/superpowers/plans/2026-09-11-image-director.md
git commit -m "Complete image-director v1 walkthrough and plan checkboxes"
```

(The `output/image-director-demo/` directory and the placeholder PNG are never committed, per the media/output policy — leave them on disk only, or delete them after the walkthrough is reported.)

---

## Plan self-review

**Spec coverage:** Every numbered section of the spec maps to a task — §1/§2 contracts → Tasks 1, 3, 4; §3 FFmpeg probe → Task 2; §2.4/planning.py bridge → Task 5; §5 schemas → Task 6; §4 CLI commands → Tasks 7-11; §7 skill → Task 12; §6 persistence layout → exercised end-to-end in Task 13; §9 docs → Task 12; §8 testing → one test file per domain module plus one CLI test file per command, matching the spec's stated file list.

**Placeholder scan:** No `TBD`/`TODO` in any task's code. The two "confirm during implementation" notes (Task 6 Step 6's exact brace nesting; Task 12 Step 6's `resolve-assets` flag names) are read-before-you-edit instructions, not unresolved content — every value and piece of logic they touch is fully specified in the surrounding text.

**Type consistency:** `VisualDNA`/`CharacterBible`/`ReferenceImageSignals` field names and defaults are identical across Tasks 1, 5, 6, 7, 9, 10, 11. `Storyboard`/`StoryboardShot`/`StoryboardLock` field names are identical across Tasks 4, 5, 6, 9, 10, 11. `storyboard_to_shot_plan`'s signature (`storyboard, visual_dna, *, plan_id=None`) matches between Task 5's implementation and Task 11's call site. `lock_violations(lock, *, digest_of, paths)`'s signature matches between Task 4's implementation and Task 11's call site.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-11-image-director.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
