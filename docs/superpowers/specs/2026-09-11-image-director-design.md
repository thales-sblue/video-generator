# Reference-image creative direction ("image director") — design

Date: 2026-09-11
Status: approved for planning
Scope owner: Claude Code (orchestrator)

## Context

Every `dark-video` production today starts from a written script. There is no
path that starts from a **reference image** — a piece of visual IP (e.g. a
character like "Pererekossauro") whose look must stay recognizable across an
entire video, with variety coming from a deliberately chosen creative
direction rather than from re-drawing the subject.

This increment adds that path as a **pre-production stage** ending in a valid
`ShotPlan`, at which point the existing `resolve-assets -> curate-visuals /
visual-lock -> render` chain takes over unmodified. It is not a second video
pipeline: it is four small, human-approved checkpoints between "here is an
image" and "here is a `ShotPlan`".

### The one architectural fact that shapes everything else

`AGENTS.md` forbids the engine from calling the orchestrator's own API as an
operational dependency, and the project has no vision-model/image-analysis
library today (confirmed by codebase survey: no PIL/OpenCV/CLIP, the only
pixel-reading code is `adapters/ffmpeg.py::measure_luma`, a 1x1-scale average
brightness probe). Reading a reference image's visual DNA and writing three
creative directions are **semantic, subjective judgments** — there is no
deterministic algorithm for them and none should be built. They are done by
the orchestrator (Claude Code) directly, in conversation, the same way
`Scene.visual_intent` and beat-to-concept translation are already
agent-authored today (`provenance: "authored"` vs `"derived"`, per
`domain/planning.py` / `domain/visual_concept.py`).

Consequently, **no CLI command in this increment "analyzes an image" or
"generates directions" as an algorithm.** Every new command's job is one of:
validate a JSON artifact I wrote against its schema and invariants, render it
as human-readable Markdown for approval, or bridge an approved artifact into
the next stage. The only genuinely deterministic new code is a small
FFmpeg-only image-signal probe (dimensions, orientation, average luma, a
5-swatch dominant-color palette) that seeds the DNA template with objective
facts before I fill in the subjective ones.

## Non-goals

- No image-to-video / image-to-image generation (Kling, Veo, Runway, Sora, or
  any paid API). Character consistency in this increment comes from **literal
  reuse of the single supplied reference image** as a locked local asset,
  varied by the existing Visual Direction / Treatment / motion layers
  (crop, zoom, pan, grade) — not from generating new poses. Multiple reference
  images of the same subject is explicitly future work.
- No vision-model or image-processing dependency (PIL, OpenCV, CLIP, etc.).
  That would be its own build-vs-reuse cycle (`AGENTS.md`) and is not needed:
  the semantic analysis is agent-authored, and the only deterministic signals
  needed are FFmpeg-computable.
- No enforcement inside `domain/relevance.py` / `domain/direction.py` /
  `domain/treatment.py` of `continuity_constraints` when picking or grading
  *non-anchor* assets (e.g. a background shot must not use the character, but
  the resolver isn't taught to veto candidates on this basis yet). The field
  is produced, validated, and carried through to `ShotPlan`; consuming it
  downstream is a follow-up increment.
- No new "Project" domain object or database. Artifacts are plain files under
  `projects/<slug>/`, exactly like every other stage.
- No changes to `Scene`, `ScenePlan`, `AssetRequirement`'s existing fields, or
  to `EditPlan` / `RenderManifest` / the renderer. `Shot` gains two additive
  optional fields only.
- No headless/one-shot CLI that runs the whole flow. Each checkpoint is its
  own small command; the sequencing and the creative content between them are
  the orchestrator's job, documented as a skill (see §7).

## Vocabulary (maps to AGENTS.md)

`AGENTS.md` lists `Storyboard` as a future contract candidate that
materializes "only when a real case forces its invariants". This is that
case: a `StoryboardShot` carries descriptive pre-production fields (`subject`,
`expression`, `lighting`, `continuity_constraints`, …) that `Shot` does not
and should not — `Shot` is already the *executable* unit the render engine
understands. `Storyboard` compiles down to `ShotPlan`.

## 1. Module layout

Three new domain modules, each stdlib-only, no I/O, dependencies point inward
(mirrors `domain/planning.py`'s sibling modules like `domain/direction.py`):

- **`src/video_generator/domain/reference_dna.py`** — `VisualDNA`,
  `ReferenceImageSignals`. Defines what must stay consistent.
- **`src/video_generator/domain/creative_brief.py`** — `VideoCategoryOption`,
  `NarrativeStructureOption`, `CreativeDirection`, `ExpansionBoundary`,
  `CreativeBrief`. Defines what we're going to do creatively. Kept separate
  from `storyboard.py` per explicit decision: the two evolve independently
  (a brief format change should never force a storyboard format change).
- **`src/video_generator/domain/storyboard.py`** — `StoryboardShot`,
  `Storyboard`, `StoryboardLock`. Turns an approved direction into
  time-boxed, executable-adjacent shots and the human-approval record.

One small addition to **`src/video_generator/domain/planning.py`**: two new
optional `Shot` fields (`visual_anchor`, `continuity_constraints`) and one new
function, `storyboard_to_shot_plan(storyboard, visual_dna, *, seed=0) ->
ShotPlan`, following the existing convention that cross-layer bridge
functions live in `planning.py` (see `direction_inputs`,
`plan_shot_visual_direction`, etc.).

One small addition to **`src/video_generator/adapters/ffmpeg.py`**: a
deterministic `probe_reference_image(path) -> ReferenceImageSignals`-shaped
helper (dimensions/orientation via `ffprobe`, average luma reusing
`measure_luma`, dominant-color palette via `palettegen`/`paletteuse` +
`rawvideo` readback — the same "pure FFmpeg, no new dependency" pattern
`measure_luma` already established).

## 2. Contracts

All follow `domain/planning.py` conventions: `@dataclass(frozen=True,
slots=True)`, `schema_version: int = 1`, strict key checking via a `_keys`
helper, `from_dict`/`to_dict`/`to_json` with
`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True) + "\n"`, a
dedicated `*Error(ValueError)` per module family (or a shared
`ReferenceDNAError` / reuse a common base — decide in the plan), local
`__post_init__` invariants only, cross-document checks in explicit
`validate_against` methods.

### 2.1 `domain/reference_dna.py`

```python
@dataclass(frozen=True, slots=True)
class ReferenceImageSignals:      # deterministic, FFmpeg-computed
    width: int
    height: int
    orientation: str              # "landscape" | "portrait" | "square"
    avg_luma: float | None        # 0..1, None if ffmpeg unavailable
    palette: tuple[str, ...]      # up to 5 "#rrggbb", most-used first

@dataclass(frozen=True, slots=True)
class CharacterBible:             # present only when VisualDNA.image_type
    anatomy: str                  # indicates a character/IP subject
    proportions: str
    eyes: str
    mouth: str
    limbs: str
    colors: str
    texture: str
    accessories: str
    base_expression: str
    deformation_limits: str
    forbidden: tuple[str, ...]    # non-empty; e.g. "extra eyes", "extra limbs",
                                   # "tongue behind head", "species change",
                                   # "color change", "radical proportion change"

@dataclass(frozen=True, slots=True)
class VisualDNA:
    image_type: str               # character | photograph | illustration |
                                   # product | space | landscape | poster |
                                   # advertising | stylized_ip | mixed
    subject: Mapping[str, Any]    # main/secondary elements, proportions,
                                   # silhouette, position, expression, movements
    color: Mapping[str, Any]      # dominant/secondary/accent (60/30/10),
                                   # temperature, saturation, contrast
    light: Mapping[str, Any]      # direction, intensity, shadows, highlights,
                                   # rim light, temperature, behavior-in-new-scenes
    material: Mapping[str, Any]   # per-element material notes; may be {}
    composition: Mapping[str, Any]  # focal point, planes, perspective, depth,
                                   # lines, negative space, direction
    mood: tuple[str, ...]         # non-empty, free-form tags
    movement_potential: Mapping[str, tuple[str, ...]]  # keys: character,
                                   # micro, camera, light, ambient, transitions
    required_elements: tuple[str, ...]   # non-empty; never changes
    flexible_elements: tuple[str, ...]   # may be empty
    signals: ReferenceImageSignals
    character_bible: CharacterBible | None = None
    source_image_sha256: str | None = None   # filled by the CLI, not authored
    schema_version: int = 1
```

Invariants: `image_type` non-empty; `mood`/`required_elements` non-empty
tuples of non-empty strings; `movement_potential` keys ⊆ the five named
above, each value a tuple (possibly empty); `character_bible` required (not
`None`) when `image_type in {"character", "stylized_ip"}`, forbidden
otherwise (keeps the "is this a character" decision unambiguous instead of an
optional field that could be silently omitted); every `CharacterBible` field
non-empty, `forbidden` non-empty.

### 2.2 `domain/creative_brief.py`

```python
@dataclass(frozen=True, slots=True)
class VideoCategoryOption:
    category: str
    why: str
    suggested_structure: str
    suggested_duration_seconds: float      # > 0
    narration: bool
    dialogue: bool
    media_format: str                      # free text, e.g. "vertical 9:16"

@dataclass(frozen=True, slots=True)
class NarrativeStructureOption:
    beats: tuple["StructureBeat", ...]     # >= 1, see below

@dataclass(frozen=True, slots=True)
class StructureBeat:
    name: str
    start_seconds: float
    end_seconds: float                     # > start_seconds

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

EXPANSION_BOUNDARIES = ("conservative", "moderate", "bold")

@dataclass(frozen=True, slots=True)
class CreativeBrief:
    category_options: tuple[VideoCategoryOption, ...]   # >= 1
    selected_category: str                              # one of the options'
                                                          # .category values
    structure: NarrativeStructureOption
    directions: tuple[CreativeDirection, ...]            # exactly 3
    selected_direction: str                              # "A" | "B" | "C" |
                                                          # "A+B" | "B+C" | "A+C"
    expansion_boundary: str = "moderate"                 # in EXPANSION_BOUNDARIES
    schema_version: int = 1
```

Invariants: `len(directions) == 3`, named positionally A/B/C for selection
purposes; `selected_category` matches a `category_options` entry;
`selected_direction` matches `^[ABC](\+[ABC])?$` with no repeated letter and
letters in A<B<C order; `expansion_boundary in EXPANSION_BOUNDARIES`; beats
in `structure` sorted, non-overlapping, contiguous from 0; every text field
non-empty. The universal hard rule — never invent a new protagonist, logo,
brand text, watermark, or external IP — is not a field to fill in; it is
documentation on the dataclass plus a `Storyboard`-level content scan (§2.3),
since a brief has no shot content to scan yet.

### 2.3 `domain/storyboard.py`

```python
@dataclass(frozen=True, slots=True)
class StoryboardShot:
    shot_id: str                   # "shot_01", "shot_02", ...
    time_start: float
    time_end: float                # > time_start
    purpose: str
    subject: str
    action: str
    expression: str | None
    camera: str
    framing: str
    environment: str
    lighting: str
    movement: str
    transition: str
    audio_intent: str
    visual_anchor: str | None      # e.g. "pererekossauro"; None for shots
                                    # that don't need the protagonist
    continuity_constraints: tuple[str, ...]   # non-empty iff visual_anchor set

@dataclass(frozen=True, slots=True)
class Storyboard:
    storyboard_id: str
    shots: tuple[StoryboardShot, ...]   # >= 1
    selected_direction: str             # copied from the approved CreativeBrief
    expansion_boundary: str
    schema_version: int = 1

    def validate_against(self, visual_dna: VisualDNA) -> None: ...

@dataclass(frozen=True, slots=True)
class StoryboardLock:
    lock_id: str
    approved_by: str
    approved_at: str               # ISO-8601 UTC
    reference_image_sha256: str
    reference_dna_sha256: str
    creative_brief_sha256: str
    storyboard_sha256: str
    schema_version: int = 1
```

`Storyboard` local invariants: `shot_id` unique, `shot_NN` sequential from 01;
the first shot's `time_start == 0`; times non-overlapping and strictly
increasing (shot *n*'s `time_start` equals shot *n-1*'s `time_end`); every
text field non-empty except `expression`
(optional — not every shot has a legible expression, e.g. a wide establishing
shot); `continuity_constraints` non-empty exactly when `visual_anchor` is set,
empty otherwise.

`Storyboard.validate_against(visual_dna)` (needs the sibling document, so it
is not in `__post_init__` — mirrors `ShotPlan.validate_against(scene_plan)`):
every shot's `visual_anchor`, when set, requires `visual_dna.character_bible`
to exist; every such shot's `continuity_constraints` is a subset of
`visual_dna.character_bible.forbidden ∪ visual_dna.required_elements`, and
defaults (when a caller omits it) are **not** silently filled by this
method — the storyboard must state them explicitly per shot, so nothing is
consistent by accident. A separate free function,
`default_continuity_constraints(visual_dna) -> tuple[str, ...]`, gives me a
value to copy into each shot while authoring, so I don't retype the whole
list by hand every time — it is a convenience for the author, not a
validation fallback. No shot's free-text fields contain a case-insensitive
match against a small fixed banned-term list covering the universal rule
(`"logo"`, `"watermark"`, `"marca d'água"`, plus any brand-name-shaped
token — kept intentionally simple; this is a safety net, not a content
filter, and false negatives are expected and acceptable).

`StoryboardLock` has no local cross-checks beyond non-empty strings and a
64-hex-char regex on each `*_sha256` field (same pattern as
`AssetProvenance.sha256` in `domain/assets.py`). The *comparison* against
files on disk is done by a pure function taking an injected `digest_of`
callable, mirroring `domain/curation.py::lock_violations`:

```python
def lock_violations(lock: StoryboardLock, *, digest_of: Callable[[str], str | None],
                     paths: Mapping[str, str]) -> tuple[str, ...]: ...
```

### 2.4 `planning.py` additions

```python
# Shot, additive fields (default None so every existing plan stays valid):
visual_anchor: str | None = None
continuity_constraints: tuple[str, ...] | None = None
```

`ShotPlan.__post_init__` gains one more per-shot check, symmetric with the
existing `provenance`/`framing` checks: `continuity_constraints` is `None` or
a tuple of non-empty unique strings; non-`None` only when `visual_anchor` is
also non-`None`.

```python
def storyboard_to_shot_plan(
    storyboard: Storyboard,
    visual_dna: VisualDNA,
    reference_asset_id: str,
    *,
    plan_id: str | None = None,
    seed: int = 0,
) -> tuple[ShotPlan, AssetRequirements]: ...
```

Compiles each `StoryboardShot` into one `Shot`: `duration_seconds =
time_end - time_start`; `visual_query` synthesized deterministically from
`subject + ", " + action + ", " + environment + ", " + lighting` (a plain
join — no keyword extraction, this is compiling already-authored text, not
deriving it); `purpose` copied; `shot_type`/`scale` chosen by a small fixed
mapping from `camera`/`framing` text via the same kind of closed-vocabulary
heuristic `plan_shots` already uses for scale-cycle assignment (kept
intentionally simple — this is compilation, not planning); `asset_id` and
`AssetRequirement` are **forced to the reference asset** for every shot whose
`visual_anchor` is set (`reuse_of` chains them together, `used_by` grows) and
a fresh per-shot requirement otherwise; `provenance` marks every field
`"authored"` (a storyboard-compiled shot is never `"derived"`); `beat` is
always `False` (no emphasis concept at this layer yet — future work if
needed). Missing `visual_dna.character_bible` when any shot sets
`visual_anchor` -> `PlanningError` before constructing anything, this is the
one place the two contracts are cross-checked. Returns
`(ShotPlan, AssetRequirements)` — actually returns a tuple like
`plan_shots` does, for symmetry and because `resolve-assets` needs both.

## 3. FFmpeg-only deterministic signal probe

`adapters/ffmpeg.py::probe_reference_image(path: Path) ->
ReferenceImageSignals`:

1. Dimensions + orientation via the existing `ffprobe` JSON-output helper
   already used elsewhere in the module (reuse, don't re-implement).
2. `avg_luma` via the existing `measure_luma(path)` (image mode, already
   handles missing FFmpeg by returning `None` — reused verbatim).
3. `palette`: `ffmpeg -i <path> -vf palettegen=max_colors=5 -f rawvideo
   -pix_fmt rgb24 -` piped and parsed as 5 consecutive 3-byte RGB triples,
   each formatted `#rrggbb`; ordered by `palettegen`'s own frequency-first
   default. On any FFmpeg failure or missing binary, `palette = ()` (fail
   soft, matching `measure_luma`'s `None`-on-unavailable convention) — never
   raises, since this only seeds a template a human/agent completes anyway.

No new dependency; this is the same "shell FFmpeg, parse fixed-size raw
output" technique `measure_luma` already uses.

## 4. CLI: five new commands

All follow the `--project <slug>` / `--out-dir <dir>` mutually-exclusive
pair (`_review_out_dir`, reused as-is), refuse-overwrite semantics, `--json`
support, and narrow exception imports — exactly the house style surveyed in
`review-cuts`/`apply-cuts`/`visual-lock`.

1. **`analyze-reference-image <image> --project <slug> [--from-dna PATH]
   [--force]`** — default mode: validates the image exists/is readable,
   computes `ReferenceImageSignals` (§3), computes `source_image_sha256`
   (reuse the existing `sha256_of`-style helper), and writes a **scaffold**
   `reference-dna.json` with `signals`/`source_image_sha256` filled and every
   semantic field set to an obvious placeholder that fails validation if left
   untouched (e.g. `image_type: ""`), plus `reference-dna.md` — a checklist
   of exactly which fields I still need to fill and why (rendered from a
   fixed template, not from the file's content). `--from-dna PATH`: skip
   re-scanning, just re-validate an edited file in place and re-render the
   `.md` (mirrors `review-cuts --from-review`).
2. **`review-creative-brief --project <slug>`** — reads `creative-brief.json`
   from the project dir, validates it, writes `creative-brief.md` (category
   options with the chosen one marked, structure beats, all 3 directions with
   the selection marked, expansion boundary). No `--from-*` needed — there is
   no scan step, so there is only one mode.
3. **`review-storyboard --project <slug>`** — reads `storyboard.json` +
   `reference-dna.json`, runs `Storyboard.validate_against`, writes
   `storyboard.md` (one row per shot plus a coverage line: how many shots
   anchor the character, whether every one carries constraints).
4. **`storyboard-lock --project <slug> --approved-by NAME [--approved-at
   ISO8601]`** — re-validates `storyboard.json` against `reference-dna.json`
   (refuses to lock something that wouldn't pass `review-storyboard`),
   computes the four SHA-256 digests, writes `storyboard-lock.json`.
5. **`storyboard-to-shots --project <slug>`** — refuses to run without a
   `storyboard-lock.json` whose digests still match the files on disk
   (`lock_violations`, fail-closed exactly like `verify-visual-lock`);
   registers the reference image into the local asset library (copies it
   under `projects/<slug>/assets/reference/<sha256>_<original_name>` with an
   `AssetProvenance` of `source_kind: "reference_image"`, license/author as
   given by `--license`/`--author` flags, required — the user's own image
   still needs a recorded right-to-use, per `AGENTS.md`); calls
   `storyboard_to_shot_plan`; writes `shot-plan.json` +
   `asset-requirements.json`, ready for `resolve-assets`.

No command "generates" DNA, category options, directions, or storyboard
content — see §0. `storyboard-to-shots`'s refusal to run without a fresh lock
is the enforcement point for "no skipping approval".

## 5. Schemas

Four new files, `additionalProperties: false`, `schema_version` `const 1`,
mirroring `shot-plan-v1.schema.json`'s style:

- `schemas/reference-dna-v1.schema.json`
- `schemas/creative-brief-v1.schema.json`
- `schemas/storyboard-v1.schema.json`
- `schemas/storyboard-lock-v1.schema.json`

`schemas/shot-plan-v1.schema.json` updated additively: `visual_anchor`
(string|null) and `continuity_constraints` (array of strings, or null) added
to the shot object's `properties` (not `required`).

## 6. Persistence layout

Everything lives directly under `projects/<slug>/`, matching the existing
convention (`visual-direction-v1.json` sits next to `edit-plan.json`, no
project-manifest-of-manifests):

```
projects/<slug>/
  assets/reference/<sha256>_<original_name>   # locked copy, written by storyboard-to-shots
  reference-dna.json / .md
  creative-brief.json / .md
  storyboard.json / .md
  storyboard-lock.json
  shot-plan.json
  asset-requirements.json
```

From `shot-plan.json` onward, the project directory looks exactly like one
produced by `plan-scenes`, and `resolve-assets` needs no changes.

## 7. Skill: `.claude/skills/direcao-a-partir-de-imagem/`

A new project skill (Portuguese, matching `producao-audiovisual`'s
convention) documenting the ten-step protocol for me to follow, so it isn't
re-derived from scratch each time the user asks to direct a video from a
reference image. It states explicitly, up front, that steps 1-2 (DNA),
3-7 (category/structure/directions/choice/boundary), and 8 (storyboard) are
**my** authored judgment, not a command's output; lists the exact CLI command
to run at each checkpoint; and states the two hard rules verbatim (never
invent protagonist/logo/brand/watermark/external IP; never skip a human
approval gate). It does not duplicate `AGENTS.md`, only points at it plus the
five new commands.

## 8. Testing

New file `tests/test_reference_dna.py`, `tests/test_creative_brief.py`,
`tests/test_storyboard.py` (one per domain module, matching the one-module
one-test-file convention), plus additions to `tests/test_planning.py` for the
`Shot` fields and `storyboard_to_shot_plan`, and CLI tests for the five new
commands (happy path, refuse-overwrite, missing-lock refusal, schema
round-trip) in whichever file already covers `review-cuts`/`visual-lock`
(`tests/test_cli.py` or its equivalent — confirm exact file in the plan).

Key invariants to cover: `VisualDNA` requires/forbids `character_bible`
correctly by `image_type`; `CreativeBrief` rejects a 2nd/4th direction and an
invalid `selected_direction` string; `Storyboard` rejects overlapping/
non-contiguous shot times and a character-anchored shot with empty
`continuity_constraints`; `Storyboard.validate_against` rejects a constraint
not present in the DNA's `forbidden`/`required_elements`; the banned-term
scan catches "logo"/"watermark"; `storyboard_to_shot_plan` forces every
anchored shot onto the same `asset_id`/reference asset and raises
`PlanningError` when a storyboard anchors a character but the DNA has no
`character_bible`; `lock_violations` reports a changed/missing file and a
clean lock reports nothing; `storyboard-to-shots` refuses without a valid
lock; a small fixture PNG (checked into `tests/fixtures/`, not a gitignored
real asset — per the CI-break lesson already in project memory) exercises
`probe_reference_image` end to end including the palette path.

Full suite must stay green before and after.

## 9. Docs to update

- `README.md` — five new commands in the command list, one worked example
  with a small placeholder character image.
- `CLAUDE.md` — add the five commands to "CLI disponível hoje".
- `docs/WORKFLOWS.md` — new section: the reference-image stage sits before
  `plan-scenes`'s normal script-first path as an alternative entry point,
  ending at `ShotPlan`.
- `docs/ARCHITECTURE.md` — one paragraph on `VisualDNA` / `CreativeBrief` /
  `Storyboard` as the realization of the `Storyboard` candidate from
  `AGENTS.md`, and the "orchestrator authors, domain validates" split that
  this increment makes explicit for the first time as a *named* pattern.
- Memory: a new memory file once implemented, noting the pattern (analysis
  and creative generation are always agent-authored, never a code path) for
  future increments that might be tempted to add a "smarter" analyzer.

## 10. Rollback

Additive throughout: three new domain modules, two small additions to
`planning.py`/`ffmpeg.py`, five new CLI subcommands, four new schemas plus
one additive schema change, new test files, doc sections, one new skill. No
existing contract, workflow, adapter, or renderer behavior changes. Reverting
the commit(s) removes the capability and leaves the existing suite exactly as
it was.

## Open implementation questions (resolve in the plan, not here)

- Exact banned-term list and matching approach (case-insensitive substring is
  simplest and stated as intentionally a safety net, not a filter — confirm
  that's acceptable rather than a stronger check).
- Whether `storyboard_to_shot_plan`'s `shot_type`/`scale` mapping from
  free-text `camera`/`framing` needs a small fixed vocabulary on
  `StoryboardShot.camera`/`.framing` (closed enum) or stays free text with a
  best-effort keyword mapping and a safe default. Leaning closed enum for
  `camera` (a handful of values: `wide`, `medium`, `close`, `detail`) to keep
  the compiler deterministic and simple; `framing`/`environment`/`lighting`
  stay free text since they only feed `visual_query`.
- Exact location of the shared `_keys`/strict-dict-parsing helper for the
  three new modules — import from `domain/planning.py` (already has one) or
  redeclare locally; prefer importing per the precedent in the scene/shot
  planner spec.
