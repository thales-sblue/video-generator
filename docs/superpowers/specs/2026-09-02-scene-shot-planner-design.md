# Scene Planner + Shot Planner — design

Date: 2026-09-02
Status: approved for planning
Scope owner: Claude Code (orchestrator)

## Context

The render engine is complete from `EditPlan` onward: `EditPlan -> FFmpeg -> TTS
-> captions -> music -> ducking -> loudness -> Ken Burns -> MP4 ->
RenderManifest`, 252 tests green, and an all-image timeline renders once the plan
declares a `target_format`. The first Desumanizando render
(`output/video_final.mp4`) proved the pipeline but exposed the real bottleneck:
**it looks like a slideshow, not a competitive dark video**. Ten abstract images
across ~4 min 30 s means every asset sits on screen ~30 s. There is no layer
between "narration script" and "EditPlan" that thinks in *shots*.

This increment inserts that layer. It turns a narrated script into a dense
visual timeline — many short shots per narrative block — expressed as
inspectable intermediate artifacts, then converts a resolved shot plan into a
valid `EditPlan` for the existing renderer. No new renderer, no asset download,
no external LLM, no MP4 re-render this cycle.

Target: a 4–6 min script yields ~8–14 scenes and ~50–100 shots, each 2–6 s
(up to ~10 s with justification), with explicit visual intent per shot and an
explicit list of assets required.

### Editorial-intelligence split (decided)

**Hybrid: heuristic draft, agent refines.** The domain emits a *complete*
deterministic draft — including derived `visual_query` / `purpose` text from
keyword extraction and a policy-driven rotation of shot types and scales. The
orchestrator (Claude Code) then overrides the weak editorial fields with
concrete choices, recorded as a separate overrides document. Every editorial
field carries `provenance: "derived" | "authored"`. Reproducibility:
`seed + policy + script` reproduces the draft; `+ overrides` reproduces the
final.

## Non-goals

- No asset acquisition. `AssetRequirements` is a *specification* the next stage
  consumes; nothing is downloaded, generated or picked.
- No external LLM, no paid API, no network. All planning is pure stdlib.
- No new workflow and no renderer change. `workflows/sequence.py`,
  `adapters/ffmpeg.py` and `domain/models.py` are **not touched**.
- No MP4 render this increment. Only 10 real assets exist; a shot plan mapped
  onto them would still be a slideshow. The converter is proven by tests, not by
  a new `final.mp4`.
- No `schema_version` bump on existing contracts. The four new schemas are
  independent `v1` documents.
- No DB, no UI, no HyperFrames, no speculative "tracks" refactor.

## Vocabulary (maps to AGENTS.md)

`AGENTS.md` lists `Script`, `Storyboard`, `AssetPlan` as future contract
candidates that materialise "only when a real case forces their invariants".
This increment is that case. The names used here:

| This increment | AGENTS.md candidate |
| --- | --- |
| `NarrativeScript` | `Script` |
| `ScenePlan` + `ShotPlan` | `Storyboard` |
| `AssetRequirements` | `AssetPlan` (spec only; no provenance yet) |

## 1. Module layout

One new domain module: **`src/video_generator/domain/planning.py`** — stdlib
only, no I/O, no adapters, dependencies point inward (it may import from
`domain/models.py` for `EditPlan` / `EditOperation` / `TargetFormat`; nothing
imports back). `domain/models.py` is left byte-unchanged so `models.py` does not
grow past its already-large size.

Exports added to `src/video_generator/domain/__init__.py`:
`RhythmPolicy`, `DEFAULT_RHYTHM_POLICY`, `NarrativeScript`, `NarrativeBlock`,
`ScenePlan`, `Scene`, `ShotPlan`, `Shot`, `AssetRequirements`,
`AssetRequirement`, `PlanningError`, `plan_scenes`, `plan_shots`,
`apply_overrides`, `shot_plan_to_edit_plan`.

`PlanningError(ValueError)` — planning-time contract violations, mirroring
`ContractError` in `models.py` (the module reuses `models.ContractError` shape
conventions but keeps its own class so callers can distinguish the stage).

A ~8-line `_estimate_syllables` lives in this module (a deliberate small
duplicate of the one in `subtitles.py`; a domain module must not import outward,
and unifying them is a separate refactor). A comment points at the sibling.

## 2. Contracts

All four follow the existing `models.py` conventions exactly: `@dataclass(frozen
=True, slots=True)`, `schema_version: int = 1`, strict key checking (the shared
`_keys` helper pattern — reimplemented locally or imported from `models` as an
internal helper), `from_dict` / `to_dict` / `to_json` with
`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True) + "\n"`,
`PlanningError` on any violation, deep-frozen JSON for free-form sub-objects.

### 2.1 `RhythmPolicy`

Editorial rhythm as data, not hardcoded constants. Defaults chosen for a PT-BR
dark video at ~speed 0.92.

```python
@dataclass(frozen=True, slots=True)
class RhythmPolicy:
    min_shot_seconds: float = 1.5
    ideal_shot_seconds_low: float = 2.0
    ideal_shot_seconds_high: float = 6.0
    soft_max_shot_seconds: float = 8.0       # exceeding requires shot.justification
    absolute_max_shot_seconds: float = 10.0  # hard contract ceiling
    min_scene_seconds: float = 6.0           # shorter blocks merge forward
    max_scene_seconds: float = 26.0          # longer blocks stay their own scene
    speaking_rate_wpm: float = 155.0         # estimated-duration fallback
    scale_cycle: tuple[str, ...] = ("wide", "medium", "close", "detail")
    shot_type_weights: Mapping[str, float] = {  # frozen at construction
        "b_roll_human": 3, "environment": 3, "object": 2, "screen": 2,
        "interface": 2, "document": 2, "photography": 2, "simple_graphic": 1,
        "on_screen_text": 1, "close_detail": 2, "establishing": 2,
        "insert": 2, "symbolic": 1,
    }
    # Visual clichés to keep the DERIVED visual_query drafts away from — these are
    # concept/keyword strings that appear in PT-BR narration, NOT shot types.
    # They never touch the shot_type picker (correction 6).
    discouraged_cliches: Mapping[str, str] = {  # clichê token -> concrete replacement phrase
        "cérebro": "pessoa concentrada em uma mesa",
        "cerebro": "pessoa concentrada em uma mesa",
        "máscara": "rosto parcialmente na sombra",
        "mascara": "rosto parcialmente na sombra",
        "silhueta": "pessoa de costas contra uma janela",
        "labirinto": "corredor estreito de escritório",
        "marionete": "mãos ajustando um objeto pequeno",
        "quebra-cabeça": "papéis espalhados sobre uma mesa",
    }
    asset_type_for_shot_type: Mapping[str, str] = {  # image | video
        "b_roll_human": "video", "environment": "video", "interface": "video",
        "screen": "video", "establishing": "video",
        # everything else defaults to "image"
    }
    max_consecutive_same_scale: int = 2
    max_consecutive_same_shot_type: int = 1
    reuse_max_per_source: int = 3
    reuse_min_scene_gap: int = 2              # no reuse inside the same/adjacent scene
    min_distinct_asset_ratio: float = 0.6    # distinct assets >= ratio * shot count
    duration_jitter_fraction: float = 0.2    # bounded ±fraction around the equal split
    duration_needed_headroom_seconds: float = 1.0
```

- Validation: every `*_seconds` finite `> 0` and ordered
  (`min <= ideal_low <= ideal_high <= soft_max <= absolute_max`);
  `speaking_rate_wpm` in `[60, 400]`; `scale_cycle` non-empty, unique;
  `shot_type_weights` non-empty, all `>= 0`, at least one `> 0`; every
  `asset_type_for_shot_type` key present in `shot_type_weights` and every value
  in `{"image","video"}`; `discouraged_cliches` keys and values non-empty
  strings; `max_consecutive_*` ints `>= 1`; `reuse_max_per_source` int `>= 1`;
  `reuse_min_scene_gap` int `>= 0`; `min_distinct_asset_ratio` in `(0, 1]`;
  `duration_jitter_fraction` in `[0, 0.9]`.
- `DEFAULT_RHYTHM_POLICY = RhythmPolicy()` module constant.
- `to_dict` emits every field; `from_dict` fills omitted fields from the default
  (so a partial `--policy` file only overrides what it names) — the one place a
  contract here is lenient about missing keys, and it is deliberate and tested.

### 2.2 `NarrativeScript` / `NarrativeBlock`

Input contract. The orchestrator builds it; `--from-text` bootstraps it.

```python
@dataclass(frozen=True, slots=True)
class NarrativeBlock:
    text: str                              # non-empty
    duration_seconds: float | None = None  # measured, optional
    visual_intent: str | None = None       # authored hint, optional
    emphasis: bool = False                 # force a shot boundary here

@dataclass(frozen=True, slots=True)
class NarrativeScript:
    script_id: str
    blocks: tuple[NarrativeBlock, ...]      # >= 1
    language: str = "pt-br"
    total_duration_seconds: float | None = None   # measured whole-track length
    schema_version: int = 1
```

- If both `total_duration_seconds` and every `block.duration_seconds` are given
  and they disagree by more than 1 s, `PlanningError` (caller inconsistency).
- `from_text(script_id, text)` classmethod: split on blank lines
  (`re.split(r"\n[ \t]*\n")`), strip, drop empties, one `NarrativeBlock` per
  paragraph, no durations, no intents.

### 2.3 `ScenePlan` / `Scene`

```python
@dataclass(frozen=True, slots=True)
class Scene:
    scene_id: str                          # "scene_01", "scene_02", ...
    narration: str
    duration_seconds: float                # > 0
    visual_intent: str                     # never empty (derived if not authored)
    visual_intent_provenance: str          # "derived" | "authored"
    source_block_indices: tuple[int, ...]  # >= 1, into NarrativeScript.blocks
    emphasis_offsets: tuple[float, ...]    # seconds from scene start where an
                                           # emphasis-flagged block begins; sorted,
                                           # strictly inside (0, duration_seconds),
                                           # unique. Correction 2: the boundary
                                           # position is preserved, not aggregated.

@dataclass(frozen=True, slots=True)
class ScenePlan:
    plan_id: str
    script_id: str
    seed: int
    policy: RhythmPolicy
    scenes: tuple[Scene, ...]              # >= 1
    total_duration_seconds: float          # > 0
    schema_version: int = 1
```

**Local invariants** (`__post_init__`, self-contained — correction 4):
`scene_id` values unique and contiguous `scene_01..scene_NN`;
`source_block_indices` non-empty, strictly increasing within a scene, and — read
across scenes in order — form `0, 1, 2, …` with no gap or repeat and starting at
`0` (a self-contained partition check that does **not** need `len(blocks)`);
every `duration_seconds > 0`; `sum(scene.duration_seconds)` within 1 ms of
`total_duration_seconds`; every `visual_intent` non-empty; every
`visual_intent_provenance` in `{"derived","authored"}`; each scene's
`emphasis_offsets` sorted, unique, every value in the open interval
`(0, duration_seconds)`.

**Cross-document** (`ScenePlan.validate_against(script)` — correction 4): the
highest `source_block_index` is exactly `len(script.blocks) - 1`; each scene's
`narration` equals its source blocks joined with `"\n\n"`; a block flagged
`emphasis` and not the first block of its scene has a matching entry in that
scene's `emphasis_offsets` (within 1 ms).

### 2.4 `ShotPlan` / `Shot`

```python
@dataclass(frozen=True, slots=True)
class Shot:
    shot_id: str                # "scene_04_shot_02"
    scene_id: str
    index: int                  # 1-based within the scene
    duration_seconds: float     # > 0, <= policy.absolute_max_shot_seconds
    asset_type: str             # "image" | "video"
    shot_type: str              # key from policy.shot_type_weights
    scale: str                  # member of policy.scale_cycle
    visual_query: str           # non-empty
    purpose: str                # non-empty
    beat: bool                  # lands on an emphasis boundary
    asset_id: str               # FK into AssetRequirements
    reuse_of: str | None        # asset_id of an earlier requirement, or None
    framing: Mapping[str, Any]  # {"motion": <ken-burns|null>, "crop_bias": "..."}
    justification: str | None   # required iff duration_seconds > policy.soft_max
    provenance: Mapping[str, str]  # per-field: visual_query/purpose/shot_type -> derived|authored

@dataclass(frozen=True, slots=True)
class ShotPlan:
    plan_id: str
    scene_plan_id: str
    script_id: str
    seed: int
    policy: RhythmPolicy
    shots: tuple[Shot, ...]     # >= 1
    schema_version: int = 1
```

**Local invariants** (`__post_init__`, self-contained — correction 4):
`shot_id` unique and equal to `f"{scene_id}_shot_{index:02d}"`; per `scene_id`
the `index` values are contiguous `1..k`; scenes appear in `scene_01, scene_02,
…` order in the flat list with all of one scene's shots before the next;
`duration_seconds > 0` and `<= policy.absolute_max_shot_seconds`;
`justification` is a non-empty string exactly when `duration_seconds >
policy.soft_max_shot_seconds` (and `None` otherwise); `shot_type` in
`policy.shot_type_weights`; `scale` in `policy.scale_cycle`; `asset_type` in
`{"image","video"}` and equal to `policy.asset_type_for_shot_type.get(shot_type,
"image")`; `framing["motion"]` in `{None} | IMAGE_MOTIONS` (the six the adapter
knows); `framing` keys `⊆ {"motion","crop_bias"}`; `provenance` keys `⊆
{"visual_query","purpose","shot_type"}` with values in
`{"derived","authored"}`; no run longer than
`policy.max_consecutive_same_scale` / `policy.max_consecutive_same_shot_type` in
the flat list; `visual_query` / `purpose` non-empty; `reuse_of` is `None` or an
`asset_id` that some **earlier** shot in the flat list already carries.

**Cross-document** (`ShotPlan.validate_against(scene_plan)` — correction 4):
every `scene_id` is a scene in `scene_plan`; the set of scene ids is exactly
covered; per scene `sum(shot.duration_seconds)` within 1 ms of that scene's
`Scene.duration_seconds`; for every `Scene.emphasis_offsets` entry there is a
shot in that scene whose cumulative start offset matches it within 1 ms and that
shot has `beat=True`; every `beat=True` shot starts on such an offset;
`reuse_of` targets respect `policy.reuse_min_scene_gap` (the reused `asset_id`
first appears at least that many scenes earlier) and
`policy.reuse_max_per_source` (no `asset_id` used by more shots than that).

### 2.5 `AssetRequirements` / `AssetRequirement`

```python
@dataclass(frozen=True, slots=True)
class AssetRequirement:
    asset_id: str               # "asset_scene04_02"
    type: str                   # "image" | "video"
    query: str
    duration_needed_seconds: float   # max shot duration using it + headroom
    orientation: str            # "landscape" | "portrait" | "square"
    purpose: str
    used_by: tuple[str, ...]    # shot_ids, >= 1
    min_count: int = 1
    notes: str | None = None

@dataclass(frozen=True, slots=True)
class AssetRequirements:
    plan_id: str
    shot_plan_id: str
    script_id: str
    orientation: str
    requirements: tuple[AssetRequirement, ...]   # >= 1
    schema_version: int = 1
```

**Local invariants** (`__post_init__`, self-contained — correction 4):
`asset_id` values unique; every `used_by` non-empty and internally unique;
`min_count >= 1`; `type` in `{"image","video"}`; `duration_needed_seconds > 0`;
every `AssetRequirement.orientation` equals the top-level `orientation`, which is
in `{"landscape","portrait","square"}`; `query` / `purpose` non-empty.

**Cross-document** (`AssetRequirements.validate_against(shot_plan)` —
corrections 3 & 4): the union of all `used_by` equals the set of
**`Shot.shot_id`** in the shot plan (not `asset_id`), and the `used_by` sets are
pairwise disjoint — every shot is covered by exactly one requirement, reached
through that shot's `asset_id`; each requirement's `type` equals the
`asset_type` of every shot in its `used_by`; `duration_needed_seconds >=
max(shot.duration_seconds for that requirement's shots)`; every distinct
`Shot.asset_id` has exactly one matching `AssetRequirement`.

`orientation` is derived once by the caller from the target format
(`landscape` if `w > h`, `portrait` if `h > w`, else `square`).

### 2.6 Local vs cross-document validation (correction 4)

Each contract's `__post_init__` checks **only** what it can see from its own
fields — it never reaches into a sibling contract. Every invariant that needs
another document lives in an explicit method:

| Method | Needs | Raises |
| --- | --- | --- |
| `ScenePlan.validate_against(script: NarrativeScript)` | block count, block text, block emphasis flags | `PlanningError` |
| `ShotPlan.validate_against(scene_plan: ScenePlan)` | scene ids, scene durations, emphasis offsets | `PlanningError` |
| `AssetRequirements.validate_against(shot_plan: ShotPlan)` | shot ids, shot `asset_id`, shot `asset_type`, shot durations | `PlanningError` |

Each returns `None` on success. `plan_scenes` / `plan_shots` / `apply_overrides`
call the relevant `validate_against` before returning, so any value the planner
hands back is already fully cross-checked; the CLI re-runs all three after
loading documents from disk. Tests exercise the `validate_against` methods
directly with hand-built mismatched pairs.

## 3. `plan_scenes(script, *, policy=DEFAULT_RHYTHM_POLICY, seed=0, plan_id=None) -> ScenePlan`

Pure, deterministic.

1. **Per-block duration.** For each block: `block.duration_seconds` if set; else,
   if `script.total_duration_seconds` set, a syllable-weighted split of the
   total across all unset blocks (weight = `sum(_estimate_syllables) + pause
   allowance`, reusing the `subtitles.py` weighting idea); else
   `max(min_shot_seconds, words / speaking_rate_wpm * 60)`.
2. **Group blocks into scenes.** Walk blocks in order accumulating into the
   current scene. Close the current scene when its running duration
   `>= min_scene_seconds` (one block per paragraph, so every block is a valid
   break point). A block whose own duration `> max_scene_seconds` is its own
   scene (the shot planner densifies it). A trailing scene left under
   `min_scene_seconds` merges back into the previous scene.
3. **Emphasis offsets (correction 2).** While accumulating a scene, track the
   running offset. When a source block has `emphasis=True` **and it is not the
   first block of its scene**, record `running_offset` (its start, seconds from
   scene start) in the scene's `emphasis_offsets`. A first-block emphasis needs
   no offset — the scene boundary is already a cut there. Offsets are exact
   functions of the resolved block durations, hence deterministic. If a merge
   (step 2) folds a short trailing scene back, its emphasis offsets are shifted
   by the host scene's prior duration and kept.
4. **Scene fields.** `narration` = source blocks joined with `"\n\n"`;
   `duration_seconds` = sum of resolved block durations; `visual_intent` = the
   first authored block intent if any (provenance `authored`), else a derived
   phrase: the two highest-scoring keyword groups from the scene text
   (stopword-filtered token n-grams, deterministic tie-break by first
   occurrence) rendered as `"mostrar: <kw a> / <kw b>"` (provenance `derived`).
   **Clichê guard (correction 6):** before emitting a derived `visual_intent`,
   each keyword token is checked against `policy.discouraged_cliches`; a match is
   replaced by its mapped concrete phrase, so the draft never seeds a "cérebro" /
   "máscara" / "silhueta" scene.
5. Assign `scene_01..scene_NN`. Build `ScenePlan`, then call
   `plan.validate_against(script)` before returning.

`seed` is accepted and stored; scene planning itself is order-deterministic, but
carrying the seed keeps the whole pipeline reproducible from one number.

## 4. `plan_shots(scene_plan, *, policy=..., seed=0, orientation="landscape") -> tuple[ShotPlan, AssetRequirements]`

Pure, deterministic. One `random.Random(seed)` created up front and threaded
through every choice. `orientation` (`"landscape" | "portrait" | "square"`) is
derived by the caller from the target format and only sets
`AssetRequirements.orientation`; it does not affect shot timing or type choice.

**ID derivation.** The CLI derives every `plan_id` from the script id:
`ScenePlan.plan_id = f"{script_id}-scene-plan"`,
`ShotPlan.plan_id = f"{script_id}-shot-plan"`,
`AssetRequirements.plan_id = f"{script_id}-asset-requirements"`. `scene_plan_id`
/ `shot_plan_id` back-references use those same strings. Callers may pass
explicit ids; the functions themselves take `plan_id` as a keyword with that
default.

For each scene:

1. **Forced segments from emphasis (correction 2).** Split the scene span
   `[0, scene_seconds]` at every `Scene.emphasis_offsets` value, giving one or
   more *segments*. Each emphasis offset becomes the start of a shot, and that
   shot is marked `beat=True`. If an offset sits closer than `min_shot_seconds`
   to a segment edge, snap it to the nearest position that keeps both sides
   `>= min_shot_seconds` (deterministic; recorded, changes nothing else). A
   segment shorter than `2 * min_shot_seconds` stays one shot.
2. **Shot count per segment.** `target = (ideal_low + ideal_high) / 2`;
   `n = clamp(round(seg_len / target), ceil(seg_len / soft_max_shot_seconds),
   floor(seg_len / min_shot_seconds))`. When that range is empty (only for a
   pathological policy or a forced segment under `2 * min_shot_seconds`), fall
   back to `max(1, floor(seg_len / min_shot_seconds))` and let step 3 attach a
   `justification` to any shot that still exceeds `soft_max`.
3. **Bounded deterministic duration redistribution (correction 5).** Never let
   one shot absorb the whole residual. Per segment of length `L` into `n` shots:
   - raw multiplicative weights `w_i = 1 + rng.uniform(-J, J)` with
     `J = policy.duration_jitter_fraction`; `d_i = L * w_i / Σw` — exact sum by
     construction;
   - iteratively clamp each `d_i` into `[min_shot_seconds,
     soft_max_shot_seconds]`; after each clamp pass, spread the resulting
     surplus/deficit **proportionally across the shots that still have slack**
     (not onto one shot), repeating until every `d_i` is in range and
     `|L - Σd| < 1e-9`. Convergence is guaranteed because `n` was chosen so the
     mean `L/n` is strictly inside the band (except the pathological fallback);
   - final residual `< 1e-9` is distributed as `residual / k` across the `k`
     shots with the most remaining slack, one pass;
   - a shot left above `soft_max` only in the pathological fallback gets
     `justification = "segment shorter than policy allows for n>=… min-length
     shots; densified to floor"`.
   Invariant guaranteed by construction: exact segment sum, every shot
   `>= min_shot_seconds`, every shot `<= soft_max_shot_seconds` unless it carries
   a `justification`, every shot `<= absolute_max_shot_seconds` (asserted).
4. **Scale.** Walk `scale_cycle` from a per-scene seeded start offset; skip a
   value that would break `max_consecutive_same_scale` against the running flat
   tail.
5. **Shot type (correction 6 — no clichés here).** Weighted pick from
   `policy.shot_type_weights` with the seeded RNG; any type that would break
   `max_consecutive_same_shot_type` is temporarily zeroed for that draw. The
   `discouraged_cliches` map is **not** consulted — it is a `visual_query`
   concern, not a shot-type one. `beat=True` shots bias toward
   `establishing` / `symbolic` / `on_screen_text` (a small additive weight bump,
   still a weighted draw).
6. **asset_type.** `policy.asset_type_for_shot_type.get(shot_type, "image")`.
7. **framing.** `motion` chosen from `IMAGE_MOTIONS` by scale
   (`wide -> zoom_in`, `medium -> pan_*` seeded, `close -> zoom_out`,
   `detail -> None`); `crop_bias` a short deterministic string. Stored for every
   shot; only consumed by the converter for `image` assets.
8. **visual_query / purpose (derived).** Slice the scene narration across the
   scene's shots by their duration proportions. `visual_query` =
   `"<scale> <shot_type_human>, <keyword phrase from this shot's slice>"`; each
   keyword token is run through `policy.discouraged_cliches` and replaced by its
   mapped phrase on a hit, so a draft never lands on a discouraged clichÃª
   (correction 6). `purpose` = `"<scene.visual_intent> — beat <index>/<count>"`.
   Both provenance `derived`.
9. **Assets & reuse.** Default: one fresh `AssetRequirement` per shot
   (`asset_scene<NN>_<II>`). Reuse is proposed when an earlier requirement has
   the same `shot_type`, shares `>= 2` keyword tokens, first appeared
   `>= reuse_min_scene_gap` scenes back, and has fewer than
   `reuse_max_per_source` users — then the shot sets `reuse_of` and reuses that
   `asset_id`, and the requirement's `used_by` / `duration_needed_seconds`
   (`= max + headroom`) grow. Reuse stops as soon as the distinct-asset count
   would drop below `ceil(policy.min_distinct_asset_ratio * shot_count)` so
   variety never collapses.

Build `ShotPlan`; call `ShotPlan.validate_against(scene_plan)`. Build
`AssetRequirements`; call `AssetRequirements.validate_against(shot_plan)`. Return
both only after both pass.

## 5. `apply_overrides(scene_plan, shot_plan, assets, overrides) -> tuple[ScenePlan, ShotPlan, AssetRequirements]`

The hybrid half. Correction 1: it takes **all three** planning documents and
returns all three rebuilt and re-validated, so a scene-level override is
actually persisted and reproducible. `overrides` is a plain mapping (loaded from
`shot-overrides.json`):

```json
{
  "shots": {
    "scene_04_shot_02": {
      "visual_query": "close-up of two newspaper front pages with opposite headlines",
      "purpose": "show the same event read two ways",
      "shot_type": "document"
    }
  },
  "scenes": { "scene_04": { "visual_intent": "confirmation bias as selective filtering" } }
}
```

- Writable keys — nothing else: on a shot `visual_query`, `purpose`,
  `shot_type`, `framing.motion`; on a scene `visual_intent`. Any other key, or
  an unknown `shot_id` / `scene_id`, raises `PlanningError`.
- A scene `visual_intent` override rebuilds that `Scene` (provenance
  `authored`), rebuilds `ScenePlan`, and re-runs `validate_against(script)` when
  a script is available to the caller (the CLI passes it; a direct caller may
  pass `script=None` to skip only the script cross-check). The new
  `visual_intent` is also propagated into the `purpose` text of that scene's
  derived (non-authored) shots so the documents stay coherent.
- A shot `shot_type` override re-derives `asset_type` from
  `policy.asset_type_for_shot_type`; `AssetRequirements` is then **fully
  recomputed** from the patched shots (same reuse rules) so `type` /
  `used_by` / `duration_needed_seconds` stay correct.
- Every touched field flips its `provenance` entry to `"authored"`; untouched
  fields keep theirs.
- Immutable under override: every id, `index`, `duration_seconds`,
  `source_block_indices`, `emphasis_offsets`, `beat`, `scale`, the reuse graph
  (`reuse_of` / `asset_id`), `seed`, `policy`. The function rebuilds each
  dataclass through its normal constructor, so all §2 local invariants re-run,
  then all three `validate_against` methods run.
- Determinism: `(script, policy, seed)` reproduces the draft; the same
  `overrides` mapping on top reproduces the final three documents byte-for-byte.

## 6. `shot_plan_to_edit_plan(shot_plan, asset_bindings, *, plan_id, brief_id, output_path, target_format, extra_operations=()) -> EditPlan`

Pure. `asset_bindings: Mapping[str, str]` maps every `asset_id` in the plan to a
local filesystem path (the caller resolves these; the function does no I/O and
does not stat the files — `EditPlan.__post_init__` already guards source/output
overlap).

- Missing binding for any `asset_id` -> `PlanningError` before constructing
  anything.
- Each shot in order -> one operation:
  - `asset_type == "image"` -> `EditOperation(operation_id=shot_id,
    kind="image_clip", source=path, parameters={"duration_seconds":
    shot.duration_seconds, **fit, **motion})`. `parameters` keys are a subset of
    exactly `{"duration_seconds", "fit", "motion"}` — the set
    `workflows/sequence.py::_image_duration` / `_image_motion` accept
    (correction 8). `fit` is `{"fit": "cover"}` for `close`/`detail`,
    `{"fit": "contain"}` for `on_screen_text`/`document`/`simple_graphic`, else
    omitted (segment inherits `target_format.fit`). `motion` is
    `{"motion": framing["motion"]}` when `framing["motion"]` is one of the six
    `IMAGE_MOTIONS`, else omitted. No `start_seconds` / `end_seconds` on the
    operation (an `image_clip` carries none).
  - `asset_type == "video"` -> `EditOperation(operation_id=shot_id,
    kind="sequence_clip", source=path, start_seconds=s,
    end_seconds=s + shot.duration_seconds, parameters={})` (or
    `parameters={"fit": …}` with the same scale rule — `sequence_clip` accepts
    **only** an optional `fit`, per `workflows/sequence.py`). `s` is `0.0` for
    the first window on a source and then steps by the previous window length
    via a per-source cursor. **The converter assumes each bound video asset is
    long enough for the cumulative windows placed on it** (correction 7); it
    does no I/O and takes no available-duration map. Validating real asset
    lengths belongs to the later asset-acquisition stage. Documented in the
    docstring.
  - `reuse_of` shots point at the **same `source`** with a different
    `motion` / `fit` / `start` — the crop-different reuse demonstration.
- `operation_id` = `shot_id` (already globally unique per §2.4).
- `sources` = distinct bound paths in first-use order, then any source named by
  an `extra_operations` entry not already present.
- `extra_operations: tuple[EditOperation, ...]` is appended verbatim after the
  timeline ops — how a caller adds the existing `captions` / `music` /
  `narration` operations to get a runnable plan in one call. The converter does
  not synthesise them.
- The returned `EditPlan` always carries the passed `target_format` (a per-shot
  `fit` is invalid without one). Its constructor — unchanged — is the final
  gate: source⊆declared, output≠source, unique `operation_id`.
- **No change to `EditOperation`, `EditPlan`, `workflows/sequence.py` or
  `adapters/ffmpeg.py`.** The converter only emits shapes those already accept;
  a mismatch is a converter bug to fix in the converter (correction 8).

**Not rendered this increment.** Tests build `asset_bindings` pointing at
throwaway temp files and assert the `EditPlan` is valid, has exactly one
timeline operation per shot (plus any extras), and that reused assets share a
`source` while differing in `parameters` / timing.

## 7. CLI: `plan-scenes`

One new subcommand in `src/video_generator/cli.py`, following the existing
argparse structure and the `narrate` / `execute-sequence-plan` handlers.

```
python -m video_generator plan-scenes
    (--script PATH | --from-text PATH)
    [--policy PATH]
    [--overrides PATH]
    [--seed N]                 # default 0
    [--total-duration SECONDS] # only with --from-text; sets NarrativeScript.total
    --target WxH[:fit]         # required, e.g. 1920x1080:cover ; sets AssetRequirements.orientation
    --out-dir DIR
    [--emit-edit-plan PATH --assets PATH]  # optional: also run the converter
```

Behaviour:

1. Load or bootstrap `NarrativeScript` (`--from-text` -> `from_text`, then apply
   `--total-duration` if given).
2. Load `RhythmPolicy` (`--policy` merged over defaults) — `DEFAULT_RHYTHM_POLICY`
   if absent.
3. `plan_scenes` -> write `<out-dir>/scene-plan.json`.
4. `plan_shots` -> write `<out-dir>/shot-plan.json` and
   `<out-dir>/asset-requirements.json`.
5. If `--overrides`, call `apply_overrides(scene_plan, shot_plan, assets,
   overrides)` and write **all three** returned documents (so a scene
   `visual_intent` override is persisted in `scene-plan.json` too, not only
   folded downstream).
6. Always run the three `validate_against` cross-checks on the final documents
   before writing.
7. If `--emit-edit-plan` and `--assets` (a JSON `{asset_id: path}` map),
   `shot_plan_to_edit_plan` -> write that path. Requires `--target`.

Read-only w.r.t. `inputs/` and `assets/`; every write lands under `--out-dir`
(or the explicit `--emit-edit-plan` path). Refuses to overwrite an existing
output file unless `--force` (matches sibling commands — confirm the exact
convention during implementation).

Exit non-zero with the `PlanningError` message on any contract violation.

## 8. Testing

TDD, new file `tests/test_planning.py` (plus a schema case in
`tests/test_schemas.py` if that file exists; otherwise fold schema validation
into the new file). Every invariant below gets a test:

**Contracts (local invariants only)**
- round-trip `from_dict(to_dict()) == obj` for all seven dataclasses;
- strict key checking rejects unknown and (except `RhythmPolicy`) missing keys;
- `RhythmPolicy.from_dict` fills omitted fields from the default;
- `RhythmPolicy` ordering / range validation rejects bad bands, unknown
  `asset_type_for_shot_type` keys, ratio/fraction out of range;
- each contract's `__post_init__` rejects its self-contained violations
  (`emphasis_offsets` outside `(0, duration)`, non-contiguous `index`,
  `justification` without a soft-max breach, `reuse_of` pointing forward, …)
  **without** any sibling document.

**Cross-document `validate_against`** (built by hand, not via the planner)
- `ScenePlan.validate_against(script)`: max block index ≠ `len(blocks)-1`
  raises; an emphasis block with no matching offset raises;
- `ShotPlan.validate_against(scene_plan)`: unknown `scene_id` raises; per-scene
  duration sum mismatch raises; an `emphasis_offset` with no `beat` shot on it
  raises; a `beat` shot off every offset raises; `reuse_min_scene_gap` /
  `reuse_max_per_source` breach raises;
- `AssetRequirements.validate_against(shot_plan)` (correction 3): passes only
  when `⋃ used_by == {shot_id}` and the `used_by` sets are disjoint; a plan
  where they'd union to the `asset_id` set instead is a **failing** fixture;
  `type` mismatch raises; `duration_needed_seconds` below the max raises.

**plan_scenes**
- `Σ scene.duration_seconds` within 1 ms of `total_duration_seconds` (measured
  path) and of the estimate sum (WPM path);
- `source_block_indices` read across scenes are `0..len(blocks)-1` with no gap;
- authored block `visual_intent` kept, provenance `authored`; absent ->
  non-empty `derived`; a scene text containing "cérebro" yields a derived
  `visual_intent` **without** that token (clichê guard);
- an interior emphasis block produces a matching `emphasis_offsets` entry; a
  first-block emphasis produces none;
- scene count for the real Desumanizando script `>= 8`;
- byte-identical `to_json()` for a fixed `(script, policy, seed)`.

**plan_shots — duration redistribution (correction 5)**
- for a scene split into `n` shots: `Σ shot.duration_seconds` within 1 ms of the
  scene duration; every shot `>= min_shot_seconds`; every shot
  `<= soft_max_shot_seconds` **or** carries a non-empty `justification`; every
  shot `<= absolute_max_shot_seconds`;
- **no single shot holds the whole residual**: with jitter on, the max-minus-min
  spread across a scene's shots is `> 0` and `< (soft_max - min)`, and removing
  any one shot's deviation still leaves the others non-trivially jittered
  (assert the deviations are spread, not concentrated in the last shot);
- a pathological policy (`soft_max` barely above `min`) that forces a
  justification actually attaches one and still hits exact sum.

**plan_shots — structure & rhythm**
- `(scene_id, index)` contiguous per scene; `shot_id` globally unique and
  `== f"{scene_id}_shot_{index:02d}"`;
- no run longer than `max_consecutive_same_scale` /
  `max_consecutive_same_shot_type`;
- every `emphasis_offset` has exactly one `beat=True` shot starting on it
  (within 1 ms); no other shot is `beat`;
- `shot_type` distribution over the real script: `>= 5` distinct types, none
  `> 40 %` — and the clichés never appear as a `shot_type` (they aren't in the
  palette);
- determinism: same `(scene_plan, policy, seed)` -> byte-identical
  `ShotPlan.to_json()` **and** `AssetRequirements.to_json()`; a different seed ->
  a different but still fully-valid pair;
- reuse: `reuse_of` targets obey `reuse_min_scene_gap` / `reuse_max_per_source`;
  distinct asset count `>= ceil(min_distinct_asset_ratio * shot_count)`; at
  least one reuse occurs on the real script (so the path is exercised).

**apply_overrides (correction 1 — three documents in, three out)**
- a scene `visual_intent` override is present in the returned **`ScenePlan`**
  with provenance `authored`, and its derived shots' `purpose` text reflects it;
- a shot editorial override changes only that field and flips its provenance;
- a `shot_type` override that changes `asset_type` propagates into the returned
  `AssetRequirements` (`type`, possibly `used_by` regrouping) and all three
  `validate_against` still pass;
- a structural edit (id, `duration_seconds`, `index`, `emphasis_offsets`,
  `reuse_of`, a new/unknown `shot_id` or `scene_id`, any non-writable key) ->
  `PlanningError`;
- determinism: draft + same overrides -> byte-identical three-document output.

**shot_plan_to_edit_plan (corrections 7 & 8)**
- returns a valid `EditPlan` carrying the passed `target_format`; exactly one
  timeline op per shot; `extra_operations` appended verbatim after;
- image shot -> `image_clip`, `parameters` keys `⊆ {"duration_seconds","fit",
  "motion"}`, `duration_seconds == shot.duration_seconds`, no `start/end` on the
  op; `motion` only ever one of the six `IMAGE_MOTIONS`;
- video shot -> `sequence_clip`, `end_seconds - start_seconds ==
  shot.duration_seconds`, `parameters` keys `⊆ {"fit"}`;
- the plan round-trips: `EditPlan.from_dict(plan.to_dict())` equals it, and
  `run_sequence_workflow`'s `_operations_from_plan` accepts the timeline shape
  (call it directly with a stub preflight, or assert against its parsing rules);
- reused asset -> shared `source`, differing `parameters` / timing;
- missing `asset_bindings` entry -> `PlanningError`;
- **no import-time or call-time mutation** of `EditOperation` / `EditPlan`
  behaviour — a guard test that a plain hand-built `image_clip` / `sequence_clip`
  op still validates exactly as before.

**Desumanizando acceptance** (`tests/test_planning.py::test_desumanizando_shot_plan`)
- load `assets/desumanizando/video_01/roteiro_narracao.txt` via `from_text`,
  `total_duration_seconds = 270.0` (module constant with a comment), default
  policy, fixed seed;
- assert: `>= 8` scenes; `>= 50` shots; **every** shot `<= 10 s`; the share of
  shots in `[2, 6] s` is `>= 0.6` ("majoritariamente 2–6 s"); `>= 5` distinct
  `shot_type`; none `> 40 %`; every `emphasis_offset` in every scene has its
  `beat` shot; `⋃ AssetRequirement.used_by == {every shot_id}`; a second run
  with the same inputs is byte-identical.

Full suite must stay green (252 -> 252 + new). `doctor` untouched.

## 9. Artifacts produced this increment (committed? no)

Running the command on the real script writes, under `output/`:
`scene-plan.json`, `shot-plan.json`, `asset-requirements.json`, plus a
hand-authored `projects/desumanizando_01/shot-overrides.json` and a re-emitted
overridden `shot-plan.json` / `asset-requirements.json`. These are inspection
artifacts for the report, not committed (media/output policy). The final report
includes the scene/shot counts and one full example scene with its shots.

## 10. Schemas

Four new files in `schemas/`, each a self-contained JSON Schema (draft 2020-12),
`additionalProperties: false`, `schema_version` `const 1`, mirroring the style
of `edit-plan-v1.schema.json`:

- `narrative-script-v1.schema.json`
- `scene-plan-v1.schema.json`
- `shot-plan-v1.schema.json`
- `asset-requirements-v1.schema.json`

`RhythmPolicy` is embedded (by value) inside `scene-plan` and `shot-plan` as an
object with all fields optional (defaults documented). A test validates each
example artifact against its schema.

## 11. Docs to update

- `docs/WORKFLOWS.md` — new top section "Scene Planner / Shot Planner" before
  the workflow list: the stage sits between `VideoBrief` and `EditPlan`, is pure
  and deterministic, emits three artifacts, and feeds the existing
  `video-sequence` renderer through `shot_plan_to_edit_plan`. Note the hybrid
  draft/override model and that no assets are acquired yet.
- `docs/VIDEO_LANGUAGE.md` — new section "Densidade e ritmo visual": the
  `RhythmPolicy` defaults (2–6 s shots, scale alternation, discourage list,
  reuse caps) documented explicitly as *policy/configuration*, not universal
  law, per the file's own preamble.
- `docs/ARCHITECTURE.md` — one paragraph: `NarrativeScript` / `ScenePlan` /
  `ShotPlan` / `AssetRequirements` realise the `Script` / `Storyboard` /
  `AssetPlan` candidates from `AGENTS.md`; they live in `domain/planning.py`,
  stdlib-only, dependencies inward; the converter targets the existing
  `EditPlan` unchanged.
- `README.md` — `plan-scenes` in the command list with a worked example on the
  Desumanizando script.
- `CLAUDE.md` — add `plan-scenes` to the "CLI disponível hoje" line.
- Memory: update `image-only-sequence-and-desumanizando-01.md` (or add a new
  memory) noting the planner exists and the slideshow bottleneck is now
  addressed at the planning layer, render still pending real assets.

## 12. Rollback

Everything is additive: one new module, one new CLI subcommand, four new
schemas, one new test file, doc sections. No existing contract, workflow,
adapter or schema changes. Reverting the commit removes the capability and
leaves the 252-test baseline exactly as it was.

## Open implementation questions (resolve in the plan, not here)

- Exact keyword-extraction routine (stopword list source, n-gram window). Keep
  it tiny, deterministic, PT-BR aware; it only feeds *derived* drafts the agent
  overrides.
- Whether `_keys` / helpers are imported from `models.py` as internal names or
  re-declared in `planning.py`. Prefer importing to avoid drift; acceptable to
  re-declare if the import surface feels wrong.
- `--force` / overwrite convention: match whatever `execute-sequence-plan` does.
