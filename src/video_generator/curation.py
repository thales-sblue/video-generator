"""Reading, writing and reviewing a visual-curation set.

The decisions themselves live in :mod:`video_generator.domain.curation`, which
is pure. This module is the boundary around it: JSON on disk, SHA-256 of real
files, and the one artifact the author actually opens — a single self-contained
HTML contact sheet with every option side by side.

The sheet is one file on purpose. The complaint that started this stage was
having to open dozens of individual images to judge a cut; previews are
embedded as data URIs so the review travels as a single ``review.html``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
from pathlib import Path
from typing import Any, Mapping

from .domain.curation import (
    SCHEMA_VERSION,
    CurationAsset,
    CurationDecision,
    CurationError,
    CurationOption,
    CurationSet,
    build_visual_lock,
    lock_violations,
    parse_approvals,
)

__all__ = [
    "load_curation_set",
    "write_curation_set",
    "render_review_sheet",
    "write_review_sheet",
    "sha256_of",
    "lock_from_files",
    "verify_visual_lock",
]

#: Previews larger than this are linked instead of embedded, so a review sheet
#: never becomes a file the author's browser refuses to open.
MAX_EMBEDDED_PREVIEW_BYTES = 2_000_000


def sha256_of(path: Path | str) -> str | None:
    """The SHA-256 of ``path``, or ``None`` when there is no such file."""

    candidate = Path(path)
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _asset_from_payload(payload: Mapping[str, Any]) -> CurationAsset:
    return CurationAsset(
        path=str(payload.get("path", "")),
        role=str(payload.get("role", "clip")),
        sha256=payload.get("sha256"),
        start_seconds=payload.get("start_seconds"),
        seconds=payload.get("seconds"),
        origin=str(payload.get("origin", "")),
    )


def _option_from_payload(payload: Mapping[str, Any]) -> CurationOption:
    return CurationOption(
        option_id=str(payload.get("option_id", "")),
        material_kind=str(payload.get("material_kind", "")),
        visual_concept=str(payload.get("visual_concept", "")),
        justification=str(payload.get("justification", "")),
        assets=tuple(_asset_from_payload(item) for item in payload.get("assets", [])),
        preview=payload.get("preview"),
        query=payload.get("query"),
        origin=str(payload.get("origin", "")),
    )


def _decision_from_payload(payload: Mapping[str, Any]) -> CurationDecision:
    return CurationDecision(
        sequence_id=str(payload.get("sequence_id", "")),
        block_id=str(payload.get("block_id", "")),
        title=str(payload.get("title", "")),
        shot_indexes=tuple(int(index) for index in payload.get("shot_indexes", ())),
        start_seconds=float(payload.get("start_seconds", 0.0)),
        seconds=float(payload.get("seconds", 0.0)),
        options=tuple(_option_from_payload(item) for item in payload.get("options", [])),
        needs_approval=bool(payload.get("needs_approval", False)),
        decision_reason=payload.get("decision_reason"),
        recommended_option_id=payload.get("recommended_option_id"),
        recommendation_reason=str(payload.get("recommendation_reason", "")),
        narrative_terms=tuple(payload.get("narrative_terms", ())),
        notes=str(payload.get("notes", "")),
        gate_rejections=tuple(
            (str(item.get("candidate", "")), tuple(item.get("reasons", ())))
            for item in payload.get("gate_rejections", [])
        ),
    )


def load_curation_set(path: Path | str) -> CurationSet:
    """Read a persisted curation set, validating it on the way in."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CurationError(f"no curation set at {source}") from exc
    except json.JSONDecodeError as exc:
        raise CurationError(f"{source} is not valid JSON: {exc}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CurationError(
            f"unsupported curation set schema: {payload.get('schema_version')!r}"
        )
    return CurationSet(
        set_id=str(payload.get("set_id", "")),
        project_id=str(payload.get("project_id", "")),
        video_title=str(payload.get("video_title", "")),
        decisions=tuple(
            _decision_from_payload(item) for item in payload.get("decisions", [])
        ),
    )


def write_curation_set(curation_set: CurationSet, path: Path | str) -> Path:
    """Persist ``curation_set`` as JSON, creating the parent directory."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(curation_set.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# The review sheet
# ---------------------------------------------------------------------------

_MATERIAL_LABELS = {
    "project_material": "material real do projeto",
    "project_diagram": "diagrama do projeto",
    "motion_typography": "motion typography",
    "graphic_composition": "composicao grafica",
    "external_asset": "asset externo",
}

_REASON_LABELS = {
    "external_asset": "asset externo",
    "stock_footage": "stock",
    "visual_metaphor": "metafora visual",
    "graphic_composition": "composicao grafica",
    "frame_choice": "escolha entre frames",
    "multiple_plausible_solutions": "mais de uma solucao plausivel",
}

_STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #0d0f12; color: #e8eaed;
  font: 15px/1.55 ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
a { color: #7fb8ff; }
header { padding: 32px 28px 20px; border-bottom: 1px solid #23262c; }
h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: -.01em; }
.sub { color: #9aa1ac; font-size: 14px; }
.counts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.count { background: #16191e; border: 1px solid #23262c; border-radius: 8px;
  padding: 8px 12px; font-size: 13px; }
.count b { display: block; font-size: 19px; font-weight: 650; }
main { padding: 24px 28px 80px; max-width: 1500px; }
.seq { border: 1px solid #23262c; border-radius: 12px; margin: 0 0 22px;
  background: #101317; overflow: hidden; }
.seq.objective { opacity: .82; }
.seq > .head { display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline;
  padding: 14px 18px; background: #141820; border-bottom: 1px solid #23262c; }
.seq h2 { margin: 0; font-size: 16px; font-family: ui-monospace, Consolas, monospace; }
.meta { color: #9aa1ac; font-size: 13px; }
.tag { font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
  padding: 3px 8px; border-radius: 999px; border: 1px solid currentColor; }
.tag.decide { color: #ffcf6b; }
.tag.evidence { color: #6fd39a; }
.options { display: grid; gap: 14px; padding: 16px 18px;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.option { border: 1px solid #23262c; border-radius: 10px; background: #0d1014;
  display: flex; flex-direction: column; overflow: hidden; }
.option.recommended { border-color: #6fd39a; box-shadow: 0 0 0 1px #6fd39a33; }
.option .letter { display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; background: #161a20; font-weight: 700; font-size: 14px; }
.option .letter .kind { font-weight: 400; font-size: 12px; color: #9aa1ac; }
/* A preview may be one frame (16:9) or a filmstrip of up to five of them.
   Never crop it: the whole point is seeing every shot the option would cut. */
.option img { width: 100%; height: auto; display: block; background: #000; }
.option .noimg { aspect-ratio: 16/9; display: grid; place-items: center;
  color: #626873; font-size: 13px; background: #06080a; }
.option dl { margin: 0; padding: 12px; font-size: 13px; }
.option dt { color: #8b929d; font-size: 11px; text-transform: uppercase;
  letter-spacing: .06em; margin-top: 9px; }
.option dt:first-child { margin-top: 0; }
.option dd { margin: 2px 0 0; }
.option code { font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  background: #171b21; padding: 1px 5px; border-radius: 4px; word-break: break-all; }
.rec { margin: 0 18px 16px; padding: 11px 14px; border-radius: 8px;
  background: #12211a; border: 1px solid #244634; font-size: 14px; }
.rec b { color: #6fd39a; }
.rejects { margin: 0 18px 16px; padding: 11px 14px; border-radius: 8px;
  background: #201414; border: 1px solid #452727; font-size: 13px; color: #d8b4b4; }
.rejects code { font-family: ui-monospace, Consolas, monospace; }
.answer { margin: 0 18px 16px; font-family: ui-monospace, Consolas, monospace;
  color: #7f8792; font-size: 13px; }
footer { padding: 24px 28px 60px; border-top: 1px solid #23262c; }
pre { background: #0d1014; border: 1px solid #23262c; border-radius: 8px;
  padding: 14px; overflow-x: auto; font-size: 13px; }
"""


def _data_uri(path: Path) -> str | None:
    if not path.is_file():
        return None
    # Only pictures are inlined. The sheet is a file the author may forward, and
    # a preview path is data read off a JSON artifact: embedding whatever it
    # happens to point at would turn a typo into a leak.
    mime = mimetypes.guess_type(path.name)[0] or ""
    if not mime.startswith("image/"):
        return None
    data = path.read_bytes()
    if len(data) > MAX_EMBEDDED_PREVIEW_BYTES:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _preview_src(preview: str | None, preview_root: Path | None) -> str | None:
    if not preview:
        return None
    candidate = Path(preview)
    if not candidate.is_absolute() and preview_root is not None:
        candidate = preview_root / preview
    embedded = _data_uri(candidate)
    return embedded if embedded is not None else preview


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _render_option(
    option: CurationOption,
    *,
    recommended: bool,
    preview_root: Path | None,
) -> str:
    src = _preview_src(option.preview, preview_root)
    image = (
        f'<img src="{_e(src)}" alt="preview {_e(option.option_id)}" loading="lazy">'
        if src
        else '<div class="noimg">sem preview</div>'
    )
    rows = [
        ("conceito visual", _e(option.visual_concept)),
        ("fonte / asset", "<br>".join(f"<code>{_e(a.path)}</code>" for a in option.assets)),
    ]
    if option.origin:
        rows.append(("origem", _e(option.origin)))
    if option.query:
        rows.append(("query", f"<code>{_e(option.query)}</code>"))
    rows.append(("justificativa", _e(option.justification)))
    body = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    kind = _MATERIAL_LABELS.get(option.material_kind, option.material_kind)
    classes = "option recommended" if recommended else "option"
    return (
        f'<div class="{classes}">'
        f'<div class="letter"><span>[{_e(option.option_id)}]'
        f'{" &#10003;" if recommended else ""}</span>'
        f'<span class="kind">{_e(kind)}</span></div>'
        f"{image}<dl>{body}</dl></div>"
    )


def _render_decision(decision: CurationDecision, preview_root: Path | None) -> str:
    shots = ", ".join(str(index) for index in decision.shot_indexes)
    tag = (
        f'<span class="tag decide">decisao: '
        f'{_e(_REASON_LABELS.get(decision.decision_reason or "", decision.decision_reason))}</span>'
        if decision.needs_approval
        else '<span class="tag evidence">material real &mdash; sem aprovacao</span>'
    )
    options = "".join(
        _render_option(
            option,
            recommended=option.option_id == decision.recommended_option_id,
            preview_root=preview_root,
        )
        for option in decision.options
    )
    parts = [
        f'<section class="seq {"" if decision.needs_approval else "objective"}" '
        f'id="{_e(decision.sequence_id)}">',
        '<div class="head">',
        f"<h2>{_e(decision.sequence_id.upper().replace('_', ' '))}</h2>",
        f'<span class="meta">{_e(decision.title)}</span>',
        f'<span class="meta">bloco <code>{_e(decision.block_id)}</code> &middot; '
        f"shots {_e(shots)} &middot; {decision.start_seconds:.1f}s &rarr; "
        f"{decision.start_seconds + decision.seconds:.1f}s "
        f"({decision.seconds:.1f}s)</span>",
        tag,
        "</div>",
        f'<div class="options">{options}</div>',
    ]
    if decision.needs_approval:
        parts.append(
            f'<div class="rec"><b>RECOMENDADO: {_e(decision.recommended_option_id)}</b>'
            f" &mdash; {_e(decision.recommendation_reason)}</div>"
        )
    if decision.notes:
        parts.append(f'<div class="answer">nota: {_e(decision.notes)}</div>')
    if decision.gate_rejections:
        rejected = "".join(
            f"<div><code>{_e(name)}</code> &rarr; {_e(', '.join(reasons))}</div>"
            for name, reasons in decision.gate_rejections
        )
        parts.append(
            f'<div class="rejects"><b>rejeitado pelo gate editorial</b>{rejected}</div>'
        )
    if decision.needs_approval:
        number = decision.sequence_id.split("_")[-1]
        parts.append(
            f'<div class="answer">responda: <code>SEQ {_e(number)} &rarr; '
            f'{_e(decision.recommended_option_id)}</code></div>'
        )
    parts.append("</section>")
    return "".join(parts)


def render_review_sheet(
    curation_set: CurationSet,
    *,
    preview_root: Path | str | None = None,
) -> str:
    """Build the self-contained HTML contact sheet for ``curation_set``."""

    root = Path(preview_root) if preview_root is not None else None
    summary = curation_set.summary()
    counts = [
        ("sequencias", summary["sequences"]),
        ("exigem aprovacao", summary["needing_approval"]),
        ("so material real", summary["real_material_only"]),
        ("assets externos", summary["external_assets"]),
        ("shots cobertos", summary["shots"]),
        ("segundos", summary["seconds"]),
    ]
    count_html = "".join(
        f'<div class="count"><b>{_e(value)}</b>{_e(label)}</div>' for label, value in counts
    )
    body = "".join(_render_decision(d, root) for d in curation_set.decisions)
    template = "\n".join(
        f"SEQ {d.sequence_id.split('_')[-1]} -> {d.recommended_option_id}"
        for d in curation_set.decisions
        if d.needs_approval
    )
    return (
        "<!doctype html>\n"
        '<html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Curadoria visual &mdash; {_e(curation_set.project_id)}</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"<header><h1>Curadoria visual &mdash; {_e(curation_set.video_title)}</h1>"
        f'<div class="sub">{_e(curation_set.set_id)} &middot; projeto '
        f"<code>{_e(curation_set.project_id)}</code> &middot; nada foi renderizado ainda"
        "</div>"
        f'<div class="counts">{count_html}</div></header>'
        f"<main>{body}</main>"
        "<footer><h2>Como responder</h2>"
        "<p>Copie o bloco abaixo, troque as letras onde discordar e responda no chat. "
        "Use <code>regenerar</code> para pedir novos candidatos.</p>"
        f"<pre>{_e(template)}</pre></footer></body></html>\n"
    )


def write_review_sheet(
    curation_set: CurationSet,
    path: Path | str,
    *,
    preview_root: Path | str | None = None,
) -> Path:
    """Write the review sheet to ``path`` and return it."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_review_sheet(curation_set, preview_root=preview_root), encoding="utf-8"
    )
    return target


# ---------------------------------------------------------------------------
# The lock, against real files
# ---------------------------------------------------------------------------


def lock_from_files(
    curation_set: CurationSet,
    approvals_text: str,
    *,
    approved_by: str,
    approved_at: str,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Build a visual lock and stamp every approved asset with its digest.

    An asset that cannot be hashed is refused here rather than at render time:
    a lock that points at a file nobody can find locks nothing.
    """

    base = Path(root) if root is not None else Path.cwd()
    lock = build_visual_lock(
        curation_set,
        parse_approvals(approvals_text),
        approved_by=approved_by,
        approved_at=approved_at,
    )
    missing: list[str] = []
    for entry in lock["sequences"]:
        for asset in entry["assets"]:
            path = Path(asset["path"])
            digest = sha256_of(path if path.is_absolute() else base / path)
            if digest is None:
                missing.append(f"{entry['sequence_id']}: {asset['path']}")
                continue
            asset["sha256"] = digest
    if missing:
        raise CurationError(
            "cannot lock assets that are not on disk: " + "; ".join(missing)
        )
    return lock


def verify_visual_lock(
    lock: Mapping[str, Any],
    *,
    root: Path | str | None = None,
) -> tuple[str, ...]:
    """Check a persisted lock against the files on disk, failing closed."""

    base = Path(root) if root is not None else Path.cwd()

    def digest_of(path: str) -> str | None:
        candidate = Path(path)
        return sha256_of(candidate if candidate.is_absolute() else base / candidate)

    return lock_violations(lock, digest_of=digest_of)
