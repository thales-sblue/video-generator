# Target Format + fit/crop — design

Date: 2026-09-02
Status: approved for planning
Scope owner: Claude Code (orchestrator)

## Context

The render engine is solid from `EditPlan` onward: `EditPlan -> FFmpeg -> TTS ->
captions -> music -> ducking -> loudness -> MP4 -> RenderManifest`. The gap this
increment closes: the output canvas is **inherited implicitly** from the source
clips (`video-sequence` requires every `sequence_clip` to share pixel
dimensions, and that shared size *is* the canvas). There is no explicit notion
of a delivery format, so the same assets cannot be rendered for YouTube
landscape and for a 9:16 Short without hand-picking matching sources.

This is the "Asset Layer + Target Format" phase. Per `AGENTS.md` (`continue`
protocol: one cohesive increment, functional capability over speculative
infrastructure) the phase is split:

- **This spec — Thread A:** explicit `TargetFormat` in the domain, deterministic
  `contain` / `cover` fitting, real locked-FFmpeg integration tests, the
  smallest `editorial_review` contract evolution, minimal structured logging.
- **This spec — Thread C:** a written build-vs-reuse decision on Pexels /
  Pixabay / Openverse. Analysis only, no adapter.
- **Deferred to its own spec -> plan cycle — Thread B:** `AssetProvenance` and
  `AssetPlan` contracts, a local asset catalog/provider, provenance traceable to
  the manifest. `AGENTS.md` and `ARCHITECTURE.md` are explicit that these become
  contracts only when a real consumer forces their invariants. The
  format-aware compositor delivered here is that consumer; Thread B starts after
  it lands.
- **Also deferred:** image motion / Ken Burns (report item 7 permits deferral);
  any tracks/timeline refactor (report item 8).

## Non-goals

- No AI reframing. `cover` is a deterministic centre crop, full stop.
- No new workflow. `video-sequence` is extended in place.
- No paid API, no external asset provider, no scraping.
- No `schema_version` bump. Both contract changes are backward compatible
  (every existing v1 document still validates and still means the same thing);
  `AGENTS.md` calls this "evoluir de forma compatível".
- No change to the byte output of any plan that does not declare a
  `target_format`. The `projects/prod/` `final.mp4` and its manifest stay
  reproducible and byte-identical.

## 1. Domain: `TargetFormat`

New frozen dataclass in `src/video_generator/domain/models.py`, exported from
`video_generator.domain`.

```python
@dataclass(frozen=True, slots=True)
class TargetFormat:
    width: int
    height: int
    fit: str = "contain"
```

Validation (raises `ContractError`):

- `width`, `height`: `int`, not `bool`, finite, `> 0`, **even** (H.264 / yuv420p
  needs even dimensions), `<= 7680` (fail closed on absurd values).
- `fit`: one of `{"contain", "cover"}`.
- `aspect_ratio` is a derived `@property` returning the reduced ratio as
  `"W:H"` (e.g. `"16:9"`) via `math.gcd`. Never stored — a stored ratio could
  contradict the pixel size.

`to_dict()` -> `{"width", "height", "fit"}`. `from_dict()` uses the shared
`_keys()` strict check (`required={"width","height"}`, `optional={"fit"}`).

Module-level presets (not an `Enum`, so future formats need no code change and
the value is structured, not a free string):

```python
YOUTUBE_LANDSCAPE = TargetFormat(1920, 1080)
SHORTS_PORTRAIT = TargetFormat(1080, 1920)
```

## 2. Contract wiring

### 2.1 `EditPlan`

- New field: `target_format: TargetFormat | None = None`.
- `to_dict()` **omits the `target_format` key entirely when `None`**. This is
  the critical compatibility rule: existing plans serialize byte-for-byte as
  before, so `fingerprint_plan()` / `plan_sha256` is unchanged and the existing
  `projects/prod/` manifest still passes `validate-manifest`.
- `from_dict()`: add `target_format` to the `optional` set; parse via
  `TargetFormat.from_dict` when present.
- `__post_init__`: accept an already-built `TargetFormat`, or `None`. If a
  `Mapping` is passed (defensive), reject with `ContractError` — construction
  goes through `from_dict`.

### 2.2 `VideoBrief`

- New field: `target_format: TargetFormat | None = None` (editorial intent).
- Same `to_dict()` omit-when-`None` rule, same `from_dict()` optional handling.
- The existing free-form `aspect_ratio: str | None` stays as-is; docs mark it
  soft-deprecated in favour of `target_format`. No cross-validation between
  brief and plan in this increment.

### 2.3 Per-clip `fit` override

Rides inside `EditOperation.parameters` as `{"fit": "contain" | "cover"}`. The
schema already types `parameters` as a free object, so **no operation-level
schema change**. Validated in the workflow (section 3).

### 2.4 JSON Schemas

- `schemas/edit-plan-v1.schema.json` and `schemas/video-brief-v1.schema.json`:
  add an optional `target_format` object:

  ```json
  "target_format": {
    "type": "object",
    "additionalProperties": false,
    "required": ["width", "height"],
    "properties": {
      "width": {"type": "integer", "minimum": 2, "maximum": 7680, "multipleOf": 2},
      "height": {"type": "integer", "minimum": 2, "maximum": 7680, "multipleOf": 2},
      "fit": {"enum": ["contain", "cover"]}
    }
  }
  ```

- `schemas/render-manifest-v1.schema.json`: widen `editorial_review` from
  `{"const": "not_performed"}` to
  `{"enum": ["not_performed", "approved", "rejected", "needs_changes"]}`.
- Both are additive / widening changes; `schema_version` stays `1`. Documented
  in `docs/` and the schema `title`/description text.

## 3. Workflow: `workflows/sequence.py`

### 3.1 Canvas selection

`_video_shape(probes, segments, plan.target_format)` becomes conditional:

- **`target_format` is set** -> canvas = `(tf.width, tf.height)`. Video clips no
  longer have to share dimensions with each other. Each segment gets a resolved
  fit: the operation's `parameters["fit"]` if present, else `tf.fit`.
- **`target_format` is `None`** -> unchanged: all `sequence_clip` dimensions
  must match, that shared size is the canvas, images are scaled + letterboxed
  onto it, `fit` stays `None` for every segment (legacy filter path).

### 3.2 Operation parameter parsing

- `sequence_clip` currently rejects any `parameters`. Relax to accept **only**
  `{"fit": "contain" | "cover"}` (still reject any other key, and reject `fit`
  entirely when `plan.target_format` is `None` — a fit with no target is a
  planning error).
- `image_clip` currently requires exactly `{"duration_seconds"}`. Relax to also
  accept an optional `"fit"` with the same rules.

### 3.3 Adapter dataclasses

`SequenceClip` and `SequenceImage` in `adapters/ffmpeg.py` gain
`fit: str | None = None`.

- `None` -> that segment uses the **exact current filter chain** (no byte
  change for legacy plans).
- `"contain"` / `"cover"` -> new fit chain (section 4).

The workflow sets `fit` on every segment **iff** `plan.target_format` is set.

## 4. Adapter: `compose_video_sequence`

For a segment whose `fit` is not `None`, the per-input video chain is:

- `contain`:
  `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1`
- `cover`:
  `scale=W:H:force_original_aspect_ratio=increase,crop=W:H,setsar=1`

Then, as today when the timeline is heterogeneous, append
`fps=IMAGE_TIMELINE_FPS,format=yuv420p` and the existing
`trim`/`setpts` handling. A `SequenceClip` keeps its `trim=start:end`; a
`SequenceImage` keeps `-loop 1 -t N` + `trim=duration`.

When every segment's `fit` is `None`, the emitted `filter_complex` string is
identical to the current implementation — verified by keeping the existing
adapter unit tests unchanged and green.

`canvas` is already required by the adapter whenever an image is present or
captions are used; it is additionally required (raise `FFmpegError`) whenever
any segment carries a non-`None` `fit`.

## 5. Real FFmpeg integration tests

New file `tests/test_ffmpeg_integration.py`.

- `setUpClass`: `executable = resolve_media_tool("ffmpeg", path_lookup=lambda _:
  None)`. If `None` -> `raise unittest.SkipTest("locked FFmpeg not present")`.
  So CI (no `.local-tools/`) stays green; local runs exercise real encode.
  `path_lookup` is stubbed to `None` so the test only ever runs against the
  hash-locked install, never a random PATH ffmpeg.
- Fixture synthesis, with the locked ffmpeg, into a `tempfile.TemporaryDirectory`:
  - clip A: `lavfi testsrc2=size=640x360:rate=15:duration=1` -> H.264 mp4
  - clip B: `lavfi testsrc2=size=480x480:rate=15:duration=1` -> H.264 mp4
    (deliberately a different shape from A)
  - image: `lavfi color=c=gray:size=200x200:duration=1` -> single PNG frame
  - audio: `lavfi sine=frequency=220:duration=2` -> `.wav`
- Cases (real `run_sequence_workflow`, no mocked preflight / compose / validate):
  1. `TargetFormat(1920, 1080, "contain")`, clips A+B, silent.
  2. `TargetFormat(1080, 1920, "cover")`, clips A+B.
  3. `TargetFormat(1080, 1920, "contain")`, clip A + image (horizontal source on
     a vertical canvas -> pillarbox).
  4. `TargetFormat(1080, 1920, "cover")`, clip A + image (centre crop).
  5. `TargetFormat(1920, 1080, "contain")`, clips A+B + the `.wav` as a
     narration file (`duration_policy=match_timeline`).
- Assertions per case (ffprobe on the real output):
  - output `.mp4` exists and is non-empty;
  - exactly one video stream, `codec_name == "h264"`;
  - stream `width` / `height` **exactly** equal the target;
  - `width / height` ratio matches the target ratio;
  - exactly one AAC audio stream in case 5, none in cases 1-4
    (`-an` silent timeline);
  - probed duration within `duration_tolerance_seconds` of the expected sum.
- No pixel comparison. Encodes are ~1-2s at 15 fps; whole-file budget < ~20s.

## 6. `editorial_review` — smallest contract evolution

- `RenderManifest.__post_init__`: replace

  ```python
  if self.editorial_review != "not_performed":
      raise ContractError("editorial_review must be not_performed in schema v1")
  ```

  with

  ```python
  _ALLOWED_EDITORIAL_REVIEW = {
      "not_performed", "approved", "rejected", "needs_changes",
  }
  if self.editorial_review not in _ALLOWED_EDITORIAL_REVIEW:
      raise ContractError(
          "editorial_review must be one of "
          "not_performed, approved, rejected, needs_changes"
      )
  ```

- Default stays `"not_performed"`. The field stays **fully decoupled** from
  `technical_validation_valid` — a technically invalid render can still carry
  `approved` and vice versa; no combined rule.
- `manifests.py` builders keep emitting the default. No behaviour change until a
  caller passes a human verdict (a future CLI surface, out of scope here).
- Audit `validation/manifest.py` and `validation/project.py` for any assumption
  that the value is `not_performed`; they already report it as its own
  dimension, so this should be limited to test fixtures.

## 7. Structured logging

New module `src/video_generator/observability.py` (named to avoid any confusion
with the stdlib `logging` module).

- `get_logger()` -> `logging.getLogger("video_generator")`, with a
  `logging.NullHandler` attached once at import so libraries/tests stay silent
  unless they opt in (`assertLogs`) or the CLI configures a handler.
- `log_event(logger, event, /, **fields)` -> emits `logger.info(json.dumps({...},
  sort_keys=True))` with `event` and a wall-clock `ts` (UTC ISO-8601 from
  `datetime.now(timezone.utc)`). Non-JSON-safe values are dropped; the helper
  only ever receives explicit scalars from callers. `duration_ms` is measured
  separately with `time.monotonic`.
- `configure_logging(stream=sys.stderr)` -> attaches a single `StreamHandler`
  at `INFO`; idempotent. Called by the CLI entrypoints when `--log` is passed.

Instrumentation in `run_sequence_workflow` / `run_final_sequence_workflow`:

| event | fields |
| --- | --- |
| `workflow.start` | `workflow`, `plan_id`, `output` (basename), `target_format` (`"WxH/fit"` or `null`) |
| `workflow.preflight` | `plan_id`, `valid`, `issue_count` |
| `workflow.segments` | `plan_id`, `clip_count`, `image_count`, `segments` (list of `{index, kind, fit}`) |
| `workflow.stage` | `plan_id`, `stage` (`compose` \| `validate`) |
| `workflow.staging` | `plan_id`, `dir` (basename) — final workflow only |
| `workflow.output` | `plan_id`, `path` (basename), `publication` |
| `external.command_failed` | `tool` (`ffmpeg`), `stage`, `returncode` — **no** command line, **no** stderr text, **no** caption text |
| `workflow.done` | `plan_id`, `valid`, `duration_ms` (wall clock via `time.monotonic`) |

Never logged: full source paths, filter graphs, subprocess argv, caption text,
narration text.

CLI: `execute-sequence-plan` and `execute-final-sequence-plan` get an opt-in
`--log` flag -> `configure_logging()`. Default off.

## 8. Thread C — Pexels / Pixabay / Openverse (analysis only)

Add a "decisão de reuso" section to `docs/VISION.md`, matching the existing
voice/caption-alignment sections, covering per provider: API-key requirement,
free-tier limits and the "may start charging" rule, current ToS, commercial
use, attribution obligations, stability, copyright risk beyond the bank licence
(trademarks / recognisable people / private property — already an `AGENTS.md`
point), automatability, and provenance-storage need.

Decision to record: **no external asset adapter in this increment.** The asset
layer that would consume one is deferred (Thread B), and each provider still has
an open question — Pexels / Pixabay need an API key plus ToS that can change,
Openverse aggregates many upstream licences and would need per-result licence
capture before any automatic use. Local-only stands. Re-open when Thread B lands
and gives a concrete consumer.

## Testing summary

TDD. Files touched:

- `tests/test_contracts.py` — `TargetFormat` validation; `EditPlan` /
  `VideoBrief` round-trip with and without `target_format`; **regression: a plan
  with no `target_format` serializes to the same JSON and same `plan_sha256` as
  before**; `editorial_review` enum accept/reject.
- `tests/test_schemas.py` — schema accepts `target_format`, rejects bad `fit` /
  odd dims / extra keys; manifest schema accepts the four verdicts.
- `tests/test_sequence_workflow.py` — canvas derived from `target_format`;
  per-clip `fit` override; `fit` rejected when no `target_format`; legacy path
  (no `target_format`) unchanged.
- `tests/test_ffmpeg_adapter.py` — `contain` / `cover` filter-graph strings for
  clips and images (still mocked subprocess); legacy graph unchanged.
- `tests/test_manifest_validation.py`, `tests/test_project_validation.py` —
  manifests carrying `approved` / `rejected` / `needs_changes`.
- `tests/test_ffmpeg_integration.py` — new, real locked FFmpeg, `SkipTest` when
  absent.
- `tests/test_observability.py` — new; event shape, secret-free fields,
  `NullHandler` default, `configure_logging` idempotent.

Full suite green (currently 218). `doctor` unaffected.

## Docs to update

- `docs/ARCHITECTURE.md` — `TargetFormat` in the domain; canvas is explicit when
  declared, inherited otherwise; `editorial_review` now a four-value field.
- `docs/WORKFLOWS.md` — `video-sequence`: `target_format`, per-clip `fit`,
  `contain` / `cover` semantics, integration-test coverage.
- `docs/VISION.md` — Thread C decision section; note `target_format` available;
  move image-motion note to "still pending".
- `docs/VIDEO_LANGUAGE.md` — brief note that `cover` crops centre and may lose
  edge content; `contain` letterboxes.
- `README.md` — CLI `--log` flag; a `target_format` plan example.
- `CLAUDE.md` — no change expected.

## Rollback

Every change is opt-in behind `target_format` or a new optional field. Reverting
the commit restores prior behaviour; no persisted artifact format changes for
existing plans.
