"""Visual Curation v1 — the human checkpoint between planning and render.

Until now the loop was ``render -> discover bad choices -> redo``. The render
is the most expensive step in the pipeline and it was also the first place the
author could see what the planner had decided. This module moves the look at
the pictures *before* the render:

    planning -> visual candidates -> human approval -> visual lock -> render

It answers three questions, in this order:

1. **Which sequences actually need a human?** Most of a developer video is
   evidence — real code, a real terminal, a real manifest, a frame of a render
   this repository produced. Evidence does not need approval; it needs to be
   correct. Only a *subjective* choice does: an external asset, stock, a visual
   metaphor, a graphic composition, or a pick between several plausible frames.
   :func:`needs_human_approval` draws that line so the author is never asked to
   rubber-stamp a screenshot of their own test suite.
2. **Is this candidate editorially relevant, or only semantically related?**
   :func:`editorial_gate` rejects the failure modes that produced the previous
   cut: a Portuguese query handed to an English-indexed provider, technical
   metadata turned into a picture (``PT-BR`` -> a flag of Brazil), the generic
   stock fallbacks (AI robot, glowing brain, hacker, matrix code, server room),
   and any external asset proposed while real project material covers the same
   point.
3. **What did the human actually approve?** :func:`parse_approvals` reads the
   author's shorthand (``SEQ 03 -> B``) and :func:`build_visual_lock` freezes
   it. After that the render may not silently substitute anything:
   :func:`lock_violations` fails closed when a locked asset is missing or its
   bytes changed.

The decision itself is never automated. This module can research, rank and
recommend; :func:`build_visual_lock` refuses to produce a lock for a sequence
the human has not answered.

Pure stdlib, no I/O, no sibling-module imports. The HTML review sheet and the
file handling live outside the domain, in :mod:`video_generator.curation`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1


class CurationError(ValueError):
    """A curation set, an approval or a visual lock is not usable."""


# ---------------------------------------------------------------------------
# Where a picture is allowed to come from, best first.
# ---------------------------------------------------------------------------

#: Material families in editorial priority order. Index 0 is the material the
#: video should reach for first; ``external_asset`` is the last resort.
MATERIAL_KINDS: tuple[str, ...] = (
    "project_material",
    "project_diagram",
    "motion_typography",
    "graphic_composition",
    "external_asset",
)

MATERIAL_PRIORITY: Mapping[str, int] = {
    kind: index + 1 for index, kind in enumerate(MATERIAL_KINDS)
}

#: The two families that are *evidence of the thing being explained*: real code,
#: a real terminal, real JSON, a real manifest, a frame of a real render, a
#: diagram drawn from the repository's own numbers. Nothing here is a matter of
#: taste, so nothing here costs the author a decision.
OBJECTIVE_MATERIAL_KINDS = frozenset({"project_material", "project_diagram"})

#: Why a sequence is being sent to a human.
DECISION_REASONS: tuple[str, ...] = (
    "external_asset",
    "stock_footage",
    "visual_metaphor",
    "graphic_composition",
    "frame_choice",
    "multiple_plausible_solutions",
)


# ---------------------------------------------------------------------------
# Text folding, shared by every lexical rule below.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase ``text`` and strip its accents, so an accented word matches."""

    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped).lower()


def tokens(text: str) -> frozenset[str]:
    """The folded word tokens of ``text``."""

    return frozenset(_TOKEN.findall(fold(text)))


def _has_accents(text: str) -> bool:
    decomposed = unicodedata.normalize("NFD", text or "")
    return any(unicodedata.combining(ch) for ch in decomposed)


def _phrase_hits(text: str, phrases: Iterable[str]) -> tuple[str, ...]:
    """Which of ``phrases`` occur in ``text`` as a run of folded words."""

    haystack = " " + " ".join(_TOKEN.findall(fold(text))) + " "
    hits = [
        phrase
        for phrase in phrases
        if " " + " ".join(_TOKEN.findall(fold(phrase))) + " " in haystack
    ]
    return tuple(sorted(set(hits)))


# ---------------------------------------------------------------------------
# Rule 1 - every external query is written in English, as a scene.
# ---------------------------------------------------------------------------

#: Function and subject words that only appear in a Portuguese query. A query
#: carrying any of them was written in the script's language instead of the
#: provider's, and the provider will answer with whatever its index thinks
#: Portuguese looks like.
#:
#: Only words that are *not* also English survive here. ``a``, ``as``, ``no``,
#: ``com``, ``video``, ``mesa`` and ``sim`` were dropped after they flagged a
#: perfectly good English scene description; a language guard that cries wolf
#: is a guard the author learns to bypass.
PORTUGUESE_MARKERS = frozenset(
    """
    um uma uns umas de do da dos das em na nos nas por para
    sem sobre entre apos ou mas que qual quais quando onde
    como nao ele ela eles elas seu sua seus suas nosso nossa
    mao maos tela telas pessoa pessoas homem mulher imagem
    imagens quadro tabela grafico graficos computador programador
    escritorio papel papeis codigo texto legenda narracao roteiro
    """.split()
)

#: A provider query has to describe a scene, not name a topic. Anything shorter
#: than this is a keyword, and keywords are what produced the previous cut.
MIN_QUERY_WORDS = 4


def non_english_query_reasons(query: str) -> tuple[str, ...]:
    """Why ``query`` is not an English description of a concrete scene."""

    text = (query or "").strip()
    if not text:
        return ("query_missing",)
    reasons: list[str] = []
    if _has_accents(text):
        reasons.append("query_not_english:diacritics")
    hits = sorted(tokens(text) & PORTUGUESE_MARKERS)
    if hits:
        reasons.append("query_not_english:" + ",".join(hits))
    if len(_TOKEN.findall(fold(text))) < MIN_QUERY_WORDS:
        reasons.append("query_is_a_keyword_not_a_scene")
    return tuple(reasons)


# ---------------------------------------------------------------------------
# Rule 2 - technical metadata is not a visual concept.
# ---------------------------------------------------------------------------

#: Words that describe *how the file is written*, never what the viewer should
#: see. "PT-BR" is a locale tag; it is not a country, a flag or a landscape.
METADATA_TERMS = frozenset(
    """
    ptbr pt br portuguese portugues brazilian brazil brasil locale locales
    language languages idioma idiomas i18n l10n encoding charset utf8
    extension filename filepath basename codec mimetype timezone
    """.split()
)

#: The pictures a metadata term used to summon all by itself.
NATIONAL_SYMBOL_TERMS = frozenset(
    """
    flag flags bandeira bandeiras pennant map maps mapa mapas
    globe atlas cartography country countries nation national nationality
    patriotic patriotism border borders territory anthem
    """.split()
)

#: The national palette, a defect only when it arrives together with a symbol.
NATIONAL_COLOUR_TERMS = frozenset("green yellow verde amarelo".split())


def metadata_symbol_rejections(
    *,
    query: str = "",
    visual_concept: str = "",
    narrative_terms: Iterable[str] = (),
) -> tuple[str, ...]:
    """Reject a picture that exists only because of a metadata word.

    ``narrative_terms`` is the vocabulary the narration itself uses. A flag or a
    map is allowed through only when the story is explicitly about one - the
    author says so by putting the word in the narration.
    """

    justified: set[str] = set()
    for term in narrative_terms:
        justified |= tokens(term)
    found = tokens(f"{query} {visual_concept}")

    reasons: list[str] = []
    symbols = sorted((found & NATIONAL_SYMBOL_TERMS) - justified)
    if symbols:
        reasons.append("national_symbol_without_narrative_reason:" + ",".join(symbols))
    metadata = sorted((found & METADATA_TERMS) - justified)
    if metadata:
        reasons.append("metadata_as_visual_subject:" + ",".join(metadata))
    if symbols and (found & NATIONAL_COLOUR_TERMS):
        reasons.append("national_palette")
    return tuple(reasons)


# ---------------------------------------------------------------------------
# Rule 3 - no generic fallbacks.
# ---------------------------------------------------------------------------

#: The stock plates a keyword search reaches for when it has understood
#: nothing. Every one of these is banned by name, because each of them is
#: *semantically* related to a video about software and none of them says
#: anything.
GENERIC_FALLBACK_PHRASES: tuple[str, ...] = (
    "ai robot",
    "artificial intelligence robot",
    "humanoid robot",
    "glowing brain",
    "digital brain",
    "neural network render",
    "hacker",
    "hooded figure",
    "matrix code",
    "falling code",
    "binary rain",
    "futuristic technology",
    "futuristic interface",
    "generic programmer",
    "person typing on a laptop",
    "man typing on a keyboard",
    "woman typing on a keyboard",
    "server room",
    "data center corridor",
    "business handshake",
    "corporate meeting",
    "lightbulb idea",
    "cityscape timelapse",
)


def generic_fallback_reasons(text: str) -> tuple[str, ...]:
    """Which banned stock fallbacks ``text`` is asking for."""

    return tuple(
        f"generic_fallback:{hit}" for hit in _phrase_hits(text, GENERIC_FALLBACK_PHRASES)
    )


# ---------------------------------------------------------------------------
# The curation set.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurationAsset:
    """One concrete file an option would put on screen, with its provenance."""

    path: str
    role: str = "clip"
    sha256: str | None = None
    start_seconds: float | None = None
    seconds: float | None = None
    origin: str = ""

    def __post_init__(self) -> None:
        if not str(self.path).strip():
            raise CurationError("a curation asset requires a path")
        if not str(self.role).strip():
            raise CurationError("a curation asset requires a role")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path, "role": self.role}
        if self.sha256:
            payload["sha256"] = self.sha256
        if self.start_seconds is not None:
            payload["start_seconds"] = round(float(self.start_seconds), 3)
        if self.seconds is not None:
            payload["seconds"] = round(float(self.seconds), 3)
        if self.origin:
            payload["origin"] = self.origin
        return payload


@dataclass(frozen=True)
class CurationOption:
    """One of the ways a sequence could look, ready to be judged on sight."""

    option_id: str
    material_kind: str
    visual_concept: str
    justification: str
    assets: tuple[CurationAsset, ...] = ()
    preview: str | None = None
    query: str | None = None
    origin: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z]", self.option_id or ""):
            raise CurationError("an option id is a single capital letter (A, B, C)")
        if self.material_kind not in MATERIAL_PRIORITY:
            raise CurationError(f"unknown material kind: {self.material_kind}")
        if not self.visual_concept.strip():
            raise CurationError(f"option {self.option_id} requires a visual concept")
        if not self.justification.strip():
            raise CurationError(f"option {self.option_id} requires a justification")
        if not self.assets:
            raise CurationError(f"option {self.option_id} requires at least one asset")

    @property
    def priority(self) -> int:
        return MATERIAL_PRIORITY[self.material_kind]

    @property
    def is_objective(self) -> bool:
        return self.material_kind in OBJECTIVE_MATERIAL_KINDS

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "option_id": self.option_id,
            "material_kind": self.material_kind,
            "visual_concept": self.visual_concept,
            "justification": self.justification,
            "assets": [asset.to_payload() for asset in self.assets],
        }
        if self.preview:
            payload["preview"] = self.preview
        if self.query:
            payload["query"] = self.query
        if self.origin:
            payload["origin"] = self.origin
        return payload


def editorial_gate(
    option: CurationOption,
    *,
    real_material_available: bool = False,
    narrative_terms: Iterable[str] = (),
) -> tuple[str, ...]:
    """Every reason ``option`` must not reach the author, in a fixed order.

    The editorial questions, applied mechanically: is there real project
    material that says this better; is the option a concrete scene; did it
    arrive by keyword association with technical metadata; is it generic stock?
    """

    reasons: list[str] = []
    if option.material_kind == "external_asset":
        if real_material_available:
            reasons.append("real_project_material_covers_this")
        reasons.extend(non_english_query_reasons(option.query or ""))
    elif option.query:
        reasons.extend(non_english_query_reasons(option.query))
    reasons.extend(
        metadata_symbol_rejections(
            query=option.query or "",
            visual_concept=option.visual_concept,
            narrative_terms=narrative_terms,
        )
    )
    reasons.extend(generic_fallback_reasons(f"{option.query or ''} {option.visual_concept}"))
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return tuple(ordered)


@dataclass(frozen=True)
class CurationDecision:
    """One visual decision, covering every shot that shares it."""

    sequence_id: str
    block_id: str
    title: str
    shot_indexes: tuple[int, ...]
    start_seconds: float
    seconds: float
    options: tuple[CurationOption, ...]
    needs_approval: bool
    decision_reason: str | None = None
    recommended_option_id: str | None = None
    recommendation_reason: str = ""
    narrative_terms: tuple[str, ...] = ()
    notes: str = ""
    gate_rejections: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"seq_\d{2,}", self.sequence_id or ""):
            raise CurationError("a sequence id looks like seq_03")
        if not self.shot_indexes:
            raise CurationError(f"{self.sequence_id} covers no shot")
        if not self.options:
            raise CurationError(f"{self.sequence_id} has no option")
        if len(self.options) > 3:
            raise CurationError(f"{self.sequence_id} offers more than 3 options")
        ids = [option.option_id for option in self.options]
        if len(set(ids)) != len(ids):
            raise CurationError(f"{self.sequence_id} repeats an option id")
        if self.needs_approval:
            if len(self.options) < 2:
                raise CurationError(
                    f"{self.sequence_id} needs approval but offers a single option"
                )
            if self.decision_reason not in DECISION_REASONS:
                raise CurationError(
                    f"{self.sequence_id} needs approval without a known reason"
                )
            if self.recommended_option_id not in ids:
                raise CurationError(
                    f"{self.sequence_id} recommends an option it does not offer"
                )
            if not self.recommendation_reason.strip():
                raise CurationError(f"{self.sequence_id} recommends without saying why")
        else:
            if len(self.options) != 1:
                raise CurationError(
                    f"{self.sequence_id} is objective and must carry exactly one option"
                )
            if not self.options[0].is_objective:
                raise CurationError(
                    f"{self.sequence_id} skips approval with subjective material"
                )

    @property
    def uses_only_real_material(self) -> bool:
        return all(option.is_objective for option in self.options)

    @property
    def external_asset_count(self) -> int:
        return sum(
            len(option.assets)
            for option in self.options
            if option.material_kind == "external_asset"
        )

    def option(self, option_id: str) -> CurationOption:
        for candidate in self.options:
            if candidate.option_id == option_id:
                return candidate
        raise CurationError(f"{self.sequence_id} has no option {option_id}")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sequence_id": self.sequence_id,
            "block_id": self.block_id,
            "title": self.title,
            "shot_indexes": list(self.shot_indexes),
            "start_seconds": round(float(self.start_seconds), 3),
            "seconds": round(float(self.seconds), 3),
            "needs_approval": self.needs_approval,
            "options": [option.to_payload() for option in self.options],
        }
        if self.decision_reason:
            payload["decision_reason"] = self.decision_reason
        if self.recommended_option_id:
            payload["recommended_option_id"] = self.recommended_option_id
        if self.recommendation_reason:
            payload["recommendation_reason"] = self.recommendation_reason
        if self.narrative_terms:
            payload["narrative_terms"] = list(self.narrative_terms)
        if self.notes:
            payload["notes"] = self.notes
        if self.gate_rejections:
            payload["gate_rejections"] = [
                {"candidate": name, "reasons": list(reasons)}
                for name, reasons in self.gate_rejections
            ]
        return payload


def needs_human_approval(
    *,
    material_kinds: Iterable[str],
    plausible_alternatives: int = 1,
) -> tuple[bool, str | None]:
    """Whether this sequence is a decision, and which kind.

    Real project material and diagrams drawn from it are evidence: they are
    used directly. Everything else - an external asset, stock, a metaphor, a
    graphic composition - is taste, and so is a pick between several equally
    plausible frames of the same real material.
    """

    kinds = tuple(material_kinds)
    if not kinds:
        raise CurationError("a sequence has no material")
    unknown = sorted(set(kinds) - set(MATERIAL_PRIORITY))
    if unknown:
        raise CurationError("unknown material kind: " + ", ".join(unknown))
    if "external_asset" in kinds:
        return True, "external_asset"
    if "graphic_composition" in kinds:
        return True, "graphic_composition"
    if "motion_typography" in kinds:
        return True, "visual_metaphor"
    if plausible_alternatives > 1:
        return True, "frame_choice"
    return False, None


@dataclass(frozen=True)
class CurationSet:
    """Every visual decision of one cut, in screen order."""

    set_id: str
    project_id: str
    video_title: str
    decisions: tuple[CurationDecision, ...]

    def __post_init__(self) -> None:
        if not self.set_id.strip():
            raise CurationError("a curation set requires a set id")
        if not self.decisions:
            raise CurationError("a curation set requires at least one decision")
        ids = [decision.sequence_id for decision in self.decisions]
        if len(set(ids)) != len(ids):
            raise CurationError("a curation set repeats a sequence id")

    def decision(self, sequence_id: str) -> CurationDecision:
        for candidate in self.decisions:
            if candidate.sequence_id == sequence_id:
                return candidate
        raise CurationError(f"unknown sequence: {sequence_id}")

    def summary(self) -> dict[str, Any]:
        return {
            "sequences": len(self.decisions),
            "needing_approval": sum(1 for d in self.decisions if d.needs_approval),
            "real_material_only": sum(
                1 for d in self.decisions if d.uses_only_real_material
            ),
            "external_assets": sum(d.external_asset_count for d in self.decisions),
            "shots": sum(len(d.shot_indexes) for d in self.decisions),
            "seconds": round(sum(d.seconds for d in self.decisions), 2),
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "set_id": self.set_id,
            "project_id": self.project_id,
            "video_title": self.video_title,
            "summary": self.summary(),
            "decisions": [decision.to_payload() for decision in self.decisions],
        }


# ---------------------------------------------------------------------------
# The author's answer.
# ---------------------------------------------------------------------------

#: The verdict asking for a fresh round of candidates rather than a choice.
REGENERATE = "regenerate"

_APPROVAL_LINE = re.compile(
    r"""^\s*
    (?:seq[_\s-]*)?(?P<number>\d{1,3})
    \s*(?:->|→|:|=)\s*
    (?P<verdict>[A-Za-z]+)
    \s*(?:\((?P<note>[^)]*)\))?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_REGENERATE_WORDS = frozenset({"regenerate", "regenerar", "refazer", "again", "novo"})


def parse_approvals(text: str) -> dict[str, dict[str, str]]:
    """Read the author's shorthand: ``SEQ 03 -> B``, ``SEQ 07 -> regenerar``.

    Returns ``{sequence_id: {"verdict": ..., "note": ...}}``. An unreadable line
    is an error, never a silent skip - a typo must not approve nothing.
    """

    answers: dict[str, dict[str, str]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _APPROVAL_LINE.match(line)
        if match is None:
            raise CurationError(f"cannot read the approval line: {raw!r}")
        sequence_id = f"seq_{int(match.group('number')):02d}"
        raw_verdict = match.group("verdict")
        folded = fold(raw_verdict)
        if folded in _REGENERATE_WORDS:
            verdict = REGENERATE
        elif re.fullmatch(r"[a-z]", folded):
            verdict = raw_verdict.upper()
        else:
            raise CurationError(f"unknown verdict {raw_verdict!r} for {sequence_id}")
        if sequence_id in answers:
            raise CurationError(f"{sequence_id} was answered twice")
        answers[sequence_id] = {
            "verdict": verdict,
            "note": (match.group("note") or "").strip(),
        }
    if not answers:
        raise CurationError("no approval was given")
    return answers


# ---------------------------------------------------------------------------
# The visual lock.
# ---------------------------------------------------------------------------


def build_visual_lock(
    curation_set: CurationSet,
    approvals: Mapping[str, Mapping[str, str]],
    *,
    approved_by: str,
    approved_at: str,
) -> dict[str, Any]:
    """Freeze the author's decisions into a ``visual-lock.json`` payload.

    Refuses to produce a lock while any decision is unanswered or marked for
    regeneration: a partial lock would let the render fill the hole by itself,
    which is exactly the loop this stage exists to break.
    """

    if not approved_by.strip():
        raise CurationError("a visual lock records who approved it")
    unknown = sorted(set(approvals) - {d.sequence_id for d in curation_set.decisions})
    if unknown:
        raise CurationError("approval for unknown sequences: " + ", ".join(unknown))

    pending: list[str] = []
    regenerate: list[str] = []
    sequences: list[dict[str, Any]] = []
    for decision in curation_set.decisions:
        answer = approvals.get(decision.sequence_id)
        if answer is not None and answer["verdict"] == REGENERATE:
            regenerate.append(decision.sequence_id)
            continue
        if decision.needs_approval and answer is None:
            pending.append(decision.sequence_id)
            continue
        chosen = (
            decision.option(answer["verdict"])
            if answer is not None
            else decision.options[0]
        )
        note = answer.get("note", "") if answer is not None else ""
        sequences.append(
            {
                "sequence_id": decision.sequence_id,
                "block_id": decision.block_id,
                "title": decision.title,
                "shot_indexes": list(decision.shot_indexes),
                "start_seconds": round(float(decision.start_seconds), 3),
                "seconds": round(float(decision.seconds), 3),
                "approved_option": chosen.option_id,
                "material_kind": chosen.material_kind,
                "visual_concept": chosen.visual_concept,
                "origin": chosen.origin,
                "query": chosen.query,
                "assets": [asset.to_payload() for asset in chosen.assets],
                "manual_overrides": [note] if note else [],
                "followed_recommendation": (
                    chosen.option_id == decision.recommended_option_id
                    if decision.needs_approval
                    else None
                ),
            }
        )

    if pending:
        raise CurationError("no decision recorded for: " + ", ".join(pending))
    if regenerate:
        raise CurationError(
            "these sequences were sent back for new candidates: " + ", ".join(regenerate)
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "lock_id": f"{curation_set.project_id}-visual-lock",
        "project_id": curation_set.project_id,
        "video_title": curation_set.video_title,
        "curation_set_id": curation_set.set_id,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "sequences": sequences,
    }


def lock_violations(
    lock: Mapping[str, Any],
    *,
    digest_of: Callable[[str], str | None],
) -> tuple[str, ...]:
    """Fail-closed check of a lock against the files on disk.

    ``digest_of`` returns the SHA-256 of a path, or ``None`` when the file is
    gone. A missing or changed asset is reported, never replaced: an approved
    picture that is no longer there is a stop, not a substitution.
    """

    if lock.get("schema_version") != SCHEMA_VERSION:
        return (f"unsupported visual lock schema: {lock.get('schema_version')!r}",)
    problems: list[str] = []
    for entry in lock.get("sequences", []):
        sequence_id = entry.get("sequence_id", "?")
        assets = entry.get("assets") or []
        if not assets:
            problems.append(f"{sequence_id}: the approved option locks no asset")
        for asset in assets:
            path = asset.get("path", "")
            actual = digest_of(path)
            if actual is None:
                problems.append(f"{sequence_id}: locked asset is missing: {path}")
                continue
            expected = asset.get("sha256")
            if expected and actual != expected:
                problems.append(
                    f"{sequence_id}: locked asset changed on disk: {path} "
                    f"(expected {expected[:12]}, found {actual[:12]})"
                )
    return tuple(problems)


# ---------------------------------------------------------------------------
# Grouping shots into decisions.
# ---------------------------------------------------------------------------


def group_consecutive(
    items: Sequence[Any],
    key: Callable[[Any], Any],
) -> tuple[tuple[Any, tuple[Any, ...]], ...]:
    """Split ``items`` into runs of consecutive entries sharing ``key``.

    Grouping is what keeps the review sheet short: the author is asked about a
    *decision*, not about each of the shots that carries it out.
    """

    groups: list[tuple[Any, list[Any]]] = []
    for item in items:
        marker = key(item)
        if groups and groups[-1][0] == marker:
            groups[-1][1].append(item)
        else:
            groups.append((marker, [item]))
    return tuple((marker, tuple(members)) for marker, members in groups)
