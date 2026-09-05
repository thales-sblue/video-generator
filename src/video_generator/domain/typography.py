"""Editorial Motion Typography v1 — on-screen type as art direction.

A caption transcribes the voice. A :class:`~video_generator.domain.editorial.TextEvent`
lifts one span of it onto the frame. This module does neither: it reads a beat,
decides whether the argument turns hard enough to deserve *type*, and then
composes a small piece of graphic design — a dominant word, a quieter line of
support, a layout, a motion — that the viewer could pause on and still read as
a poster.

The pipeline the module implements, in order::

    narration -> emphasis -> text concept -> text role -> layout -> motion -> timing

Three rules hold the whole thing together and every one of them is enforced,
not merely documented:

* **The narration carries the information.** The type only emphasises, so most
  beats get nothing at all and a 209 s piece gets fifteen to twenty-five
  interventions rather than one per sentence.
* **The type is not the sentence.** Blocks are *derived*: a dominant content
  word, plus either a licensed connector (used only when its own cue is in the
  sentence) or a second content word. ``_is_derived`` refuses any composition
  that reproduces a contiguous run of the narration.
* **Hierarchy is the point.** Every layout assigns at least two different
  weights whenever it has more than one block, because type set all at one
  size is a caption with extra steps.

Pure stdlib, no I/O, deterministic: the same beats and the same timings always
produce the same plan.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from video_generator.domain.editorial import NarrationBeat, VisualStyle
from video_generator.domain.models import EditOperation

SCHEMA_VERSION = 1


class TypographyError(Exception):
    """Raised when a motion-typography value violates its contract."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
# Why the words are on screen at all. The role is decided from the sentence,
# and it is what picks the layout pool below.
TEXT_ROLES = ("hook", "keyword", "contrast", "statement", "question")

# Reusable compositions. Each one is a different answer to "where does the eye
# land first", and each is defined once in the adapter as real pixel geometry.
TEXT_LAYOUTS = (
    "dominant_word",
    "stacked_hierarchy",
    "small_plus_massive",
    "split_statement",
    "edge_aligned",
    "centered_poster",
    "contrast_pair",
)

# Four motions, deliberately. Motion here exists to reveal the hierarchy the
# layout already built — the big word arrives after the small one, never
# alongside it — so a fifth effect would only be decoration.
TEXT_MOTIONS = ("fade_rise", "scale_in", "masked_reveal", "stagger_rise")

# The typographic scale. Four steps, not a continuum: a hierarchy a viewer can
# name at a glance ("small line, huge word") is the one that reads.
BLOCK_WEIGHTS = ("micro", "small", "large", "massive")
_SUPPORT_WEIGHTS = frozenset({"micro", "small"})

# Layouts and how many blocks each can hold. A layout that accepts a count it
# was not composed for is how a considered design turns into a template.
_LAYOUT_BLOCKS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "dominant_word": (1,),
        "edge_aligned": (1, 2),
        "small_plus_massive": (2,),
        "split_statement": (2,),
        "centered_poster": (2,),
        "contrast_pair": (2,),
        "stacked_hierarchy": (2, 3),
    }
)

# The weights a layout gives its blocks, per block count. Every multi-block
# entry mixes a support weight with a display weight: that contrast *is* the
# design.
_LAYOUT_WEIGHTS: Mapping[str, Mapping[int, tuple[str, ...]]] = MappingProxyType(
    {
        "dominant_word": MappingProxyType({1: ("massive",)}),
        "edge_aligned": MappingProxyType({1: ("large",), 2: ("micro", "massive")}),
        "small_plus_massive": MappingProxyType({2: ("small", "massive")}),
        "split_statement": MappingProxyType({2: ("small", "large")}),
        "centered_poster": MappingProxyType({2: ("micro", "massive")}),
        "contrast_pair": MappingProxyType({2: ("large", "massive")}),
        "stacked_hierarchy": MappingProxyType(
            {2: ("micro", "massive"), 3: ("micro", "large", "massive")}
        ),
    }
)

# Which compositions a role may use, in preference order. Pools overlap on
# purpose: the rotation below walks a pool and skips whatever the previous
# event used, so a role that fires twice in a row still changes shape.
_ROLE_LAYOUTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "hook": ("small_plus_massive", "stacked_hierarchy", "dominant_word"),
        "keyword": ("dominant_word", "edge_aligned"),
        "contrast": ("contrast_pair", "split_statement"),
        "statement": ("stacked_hierarchy", "edge_aligned", "small_plus_massive"),
        "question": ("centered_poster", "split_statement"),
    }
)

# One motion per layout. The pairing is not arbitrary: a diagonal composition
# wants a wipe, a stack wants its lines to arrive in order, and a single word
# alone on the frame wants to settle rather than travel.
_LAYOUT_MOTION: Mapping[str, str] = MappingProxyType(
    {
        "dominant_word": "scale_in",
        "stacked_hierarchy": "stagger_rise",
        "small_plus_massive": "stagger_rise",
        "split_statement": "masked_reveal",
        "edge_aligned": "masked_reveal",
        "centered_poster": "fade_rise",
        "contrast_pair": "stagger_rise",
    }
)


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
_STRIP = ".,;:!?…\"'()[]—–-"
_MARKUP = "<>{}"
_WORD_LIMIT = 8
_BLOCK_CHARS = 34


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _bare(token: str) -> str:
    return _fold(token.strip(_STRIP))


# Words that never earn the frame on their own. Function words plus the thin
# verbs a Portuguese sentence leans on; a beat whose only candidate is one of
# these gets no type at all, which is the correct outcome.
_STOPWORDS = frozenset(
    """
    a o e as os um uma uns umas de do da dos das no na nos nas em para por com
    que se ao aos sua seu suas seus meu minha nossa nosso isso isto aquilo
    ele ela eles elas voce voces eu me te lhe mais menos muito pouco pra
    ja nao sim tambem como quando onde porque pois mas ou entao talvez sobre
    ser estar ter haver fazer poder ir vir dar ver ate entre sem foi era sao
    tinha havia esta este esse essa aquele aquela seja fosse ficou vai vao
    aqui ali la hoje ontem agora nunca sempre existe existem tem toda todo
    todas todos outra outro outras outros mesma mesmo mesmas mesmos assim
    depois antes ainda so apenas cada qual quais nada tudo alguem algo algum
    alguma alguns algumas nem nos nas dele dela deles delas disso desse dessa
    quanto quantos serve servem passa passam chama chamam torna tornam coloque
    dentro fora perto longe atras nisso naquilo daquilo disto neste nesta
    nessa nesse vezes vez gostamos gosta gostam gostar parece parecer parecia
    comeca comecam comeco recebe recebem vira viram existe talvez sendo
    lembra lembram lembro ouviu ouve ouvir ouvem acha acham achava
    quanto quando enquanto durante depois seria serao somos estamos estao
    imagina imaginamos imaginava imaginar admitir admite admitem colocar
    jeito jeitos modo modos forma formas coisa coisas lugar lugares parte
    partes caso casos tipo tipos ponto pontos hora horas dia dias
    """.split()
)

# Verb endings that mark a conjugated form rather than the thing being named.
# A gerund is deliberately absent: "PENSANDO" is exactly the kind of word this
# layer wants, while "GOSTAMOS" is not.
_VERB_ENDINGS = ("amos", "emos", "imos", "aram", "eram", "iram", "aria", "eria")
# What a dominant word has to be worth before it may hold the frame alone. Set
# just above "a moderately long word with nothing else going for it", which is
# what kept putting prepositions on screen.
_MIN_DOMINANT_SCORE = 1.7
# At or under this many spoken words the sentence is already a headline, and
# the frame belongs to a single word — but only if that word can carry it. A
# short sentence made of thin verbs ("Você não lembra onde ouviu") gets the
# ordinary two-block treatment instead of LEMBRA at two hundred pixels.
_HEADLINE_WORDS = 7
_HEADLINE_MIN_SCORE = 2.6

# Suffixes that mark an abstract noun in Portuguese. These are the words this
# channel is actually about — irracionalidade, repetição, identidade — so they
# outrank a longer but emptier token.
_NOUN_SUFFIXES = (
    "cao", "coes", "dade", "dades", "encia", "encias", "ancia", "ancias",
    "mento", "mentos", "ismo", "ismos", "agem", "eza", "ura", "uras",
    "anca", "ancas",
)
# An adverb is never the word. "COMPLETAMENTE" set at 200 px is a modifier
# holding the frame while the thing it modifies sits in the voice-over.
_ADVERB_SUFFIX = "mente"

# A connector may only be used when its own cue is present in the sentence.
# That is the whole reason this table is allowed to exist: the small line is a
# re-rendering of a turn the narration actually makes, never invented copy.
_CONNECTORS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("repeticao", "repete", "repetida", "repetido", "muitas vezes"), "QUANDO SE REPETE"),
    (("talvez",), "TALVEZ"),
    (("porem", "contudo", "entretanto", "todavia"), "SÓ QUE"),
    (("mas",), "MAS"),
    (("quando",), "QUANDO"),
    (("porque", "portanto"), "PORQUE"),
    (("comeca", "comecam", "comeco"), "COMEÇA A"),
    (("parece", "parecer", "parecia"), "PARECE"),
    (("tambem",), "TAMBÉM"),
    (("nunca",), "NUNCA"),
    (("sempre",), "SEMPRE"),
    (("depois",), "DEPOIS"),
    (("agora",), "AGORA"),
)

# Where a sentence turns against itself. Conjunctions only: a marker is *cut
# out* of the split, so a content word here ("contrário") would delete the very
# term the left pole was about.
_CONTRAST_MARKERS = ("so que", "mas", "porem", "contudo", "entretanto", "todavia")

_SENTENCE_SPLIT = re.compile(r"[.!?;]+")
_CLAUSE_SPLIT = re.compile(r"[.!?;,:]+")


def _words(text: str) -> list[str]:
    return [w for w in text.replace("\n", " ").split() if w.strip(_STRIP)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _is_noun_like(folded: str) -> bool:
    return any(folded.endswith(suffix) for suffix in _NOUN_SUFFIXES)


def _score_word(
    surface: str,
    index: int,
    total: int,
    themes: frozenset[str],
    motifs: Mapping[str, int],
) -> float:
    """How much this word deserves to be the one on screen.

    Length is a weak signal, so it is weighted weakly. What actually decides
    the word is whether the beat's own semantic reading already named it, and
    whether the script keeps coming back to it — a motif is the closest thing
    a text has to a thesis.
    """

    folded = _bare(surface)
    if not folded or folded in _STOPWORDS or len(folded) < 4:
        return -1.0
    if not any(ch.isalpha() for ch in folded):
        return -1.0
    score = 0.16 * len(folded)
    if folded in themes:
        score += 2.2
    if _is_noun_like(folded):
        score += 1.7
    if any(folded.endswith(ending) for ending in _VERB_ENDINGS):
        score -= 1.4
    if folded.endswith(_ADVERB_SUFFIX):
        score -= 2.4
    repeats = motifs.get(folded, 0)
    if repeats > 1:
        score += min(2.0, 0.8 * (repeats - 1))
    # a word at the end of the sentence is what the sentence was driving at
    if total > 1:
        score += 1.2 * (index / (total - 1))
    return score


def _stem(word: str) -> str:
    """Enough of a word to tell ``IMAGINAMOS`` from ``IMAGINAVA``.

    Not a real stemmer and not trying to be: it exists only to stop two
    inflections of the same verb being set as if they were a contrast.
    """

    folded = _bare(word)
    return folded[:5] if len(folded) > 5 else folded


def _distinct(first: str, second: str) -> bool:
    """Whether two blocks say different things.

    Two blocks that share a stem are one block with a typo in it.
    """

    left = {_stem(w) for w in _words(first)}
    right = {_stem(w) for w in _words(second)}
    return not (left & right)


def _ranked_words(
    text: str,
    themes: frozenset[str],
    motifs: Mapping[str, int],
    *,
    exclude: frozenset[str] = frozenset(),
) -> list[tuple[str, float, int]]:
    """Every word that could hold the frame, best first, with its position."""

    words = _words(text)
    scored: list[tuple[str, float, int]] = []
    seen: set[str] = set()
    for index, surface in enumerate(words):
        folded = _bare(surface)
        if folded in exclude or folded in seen:
            continue
        score = _score_word(surface, index, len(words), themes, motifs)
        if score <= 0:
            continue
        seen.add(folded)
        scored.append((surface.strip(_STRIP), score, index))
    scored.sort(key=lambda row: (-row[1], row[2]))
    return scored


def _best_word(
    text: str,
    themes: frozenset[str],
    motifs: Mapping[str, int],
    *,
    exclude: frozenset[str] = frozenset(),
) -> "tuple[str, float] | None":
    ranked = _ranked_words(text, themes, motifs, exclude=exclude)
    return (ranked[0][0], ranked[0][1]) if ranked else None


def _connector(text: str) -> str | None:
    folded = " ".join(_bare(w) for w in _words(text))
    for cues, connector in _CONNECTORS:
        for cue in cues:
            if re.search(rf"(?:^| ){re.escape(cue)}(?:$| )", folded):
                return connector
    return None


def _split_on_contrast(text: str) -> "tuple[str, str] | None":
    """The two poles of a sentence that turns against itself."""

    folded_words = [_bare(w) for w in _words(text)]
    joined = " ".join(folded_words)
    for marker in _CONTRAST_MARKERS:
        position = joined.find(f" {marker} ")
        if position < 0:
            continue
        # translate the character offset back into a word offset
        cut = joined[:position].count(" ") + 1
        left = " ".join(_words(text)[:cut])
        right = " ".join(_words(text)[cut + len(marker.split()):])
        if len(_words(left)) >= 2 and len(_words(right)) >= 2:
            return left, right
    sentences = _sentences(text)
    if len(sentences) >= 2:
        left = " ".join(sentences[:-1])
        right = sentences[-1]
        if len(_words(left)) >= 3 and len(_words(right)) >= 3:
            return left, right
    return None


# How a second sentence announces that it is arguing with the first. Without
# this test every adjacent pair of sentences reads as a "contrast", which is
# how a contrast layout stops meaning anything.
_OPPOSING_OPENERS = (
    "mas", "so que", "porem", "contudo", "entretanto", "todavia", "ou pior",
    "ja nao", "nao porque",
)
# An adversative the writer put in the middle of the second sentence rather
# than at its front. "Muitas vezes é o contrário" is a reversal wherever the
# word sits.
_OPPOSING_TERMS = ("contrario", "ao inves", "ao contrario")


def _opposes(first: str, second: str) -> bool:
    """Whether the second sentence turns against the first.

    Deliberately narrow. A looser test — "exactly one of them contains *não*" —
    was tried and it labelled half the script a contrast, which is how a
    contrast layout stops meaning anything. Only two signals survive: an
    adversative the writer wrote, and a parallel construction they built
    ("Também querem pertencer. Também gostam de estar certas.").
    """

    left = [_bare(w) for w in _words(first)]
    right = [_bare(w) for w in _words(second)]
    if not left or not right:
        return False
    opener = " ".join(right[:3])
    if any(opener.startswith(cue) for cue in _OPPOSING_OPENERS):
        return True
    if any(term in " ".join(right) for term in _OPPOSING_TERMS):
        return True
    return left[0] == right[0] and left[0] not in ("a", "o", "e", "que")


def _is_derived(texts: Sequence[str], narration: str) -> bool:
    """Whether this composition is a reading of the sentence, not a copy of it.

    Refuses two things: a block set whose words appear as one contiguous run in
    the narration (that is a caption, cropped), and a composition long enough
    to be one (the type is emphasis, and emphasis is short).

    One word is the exception, and has to be: a single word lifted out of a
    sentence *is* emphasis by construction — there is no run to reproduce — and
    without this exemption the single-word layouts could never fire at all.
    """

    total_words = sum(len(_words(t)) for t in texts)
    if total_words > _WORD_LIMIT + 1:
        return False
    source = " ".join(_bare(w) for w in _words(narration))
    joined = " ".join(_bare(w) for t in texts for w in _words(t))
    if not joined:
        return False
    if total_words == 1:
        return True
    return f" {joined} " not in f" {source} "


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TextBlock:
    """One run of type inside an event, with the weight it is set in."""

    text: str
    weight: str
    accent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise TypographyError("block text must be a non-empty string")
        text = " ".join(self.text.split())
        if len(text) > _BLOCK_CHARS:
            raise TypographyError(
                f"block text must be at most {_BLOCK_CHARS} characters"
            )
        if len(text.split()) > _WORD_LIMIT:
            raise TypographyError(f"a block must hold at most {_WORD_LIMIT} words")
        if any(ord(character) < 32 for character in text):
            raise TypographyError("block text must not contain control characters")
        if any(character in text for character in _MARKUP):
            # the rule every text layer in this project obeys: copy is data and
            # must never be able to become subtitle or filter-graph syntax
            raise TypographyError("block text must not contain markup characters")
        object.__setattr__(self, "text", text)
        if self.weight not in BLOCK_WEIGHTS:
            raise TypographyError(f"weight must be one of {', '.join(BLOCK_WEIGHTS)}")
        if not isinstance(self.accent, bool):
            raise TypographyError("accent must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "weight": self.weight, "accent": self.accent}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TextBlock":
        unknown = set(data) - {"text", "weight", "accent"}
        if unknown:
            raise TypographyError(f"block does not accept: {', '.join(sorted(unknown))}")
        if "text" not in data or "weight" not in data:
            raise TypographyError("a block requires text and weight")
        return cls(data["text"], data["weight"], bool(data.get("accent", False)))


@dataclass(frozen=True, slots=True)
class MotionTextEvent:
    """One typographic intervention: several blocks, one layout, one motion."""

    event_id: str
    blocks: tuple[TextBlock, ...]
    role: str
    layout: str
    motion: str
    start_seconds: float
    end_seconds: float
    beat_id: str | None = None
    importance: float = 0.0
    rationale: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise TypographyError("event_id must be a non-empty string")
        object.__setattr__(self, "event_id", self.event_id.strip())
        blocks = tuple(self.blocks)
        if not blocks or len(blocks) > 3:
            raise TypographyError("an event holds between one and three blocks")
        if not all(isinstance(b, TextBlock) for b in blocks):
            raise TypographyError("blocks must be TextBlock values")
        object.__setattr__(self, "blocks", blocks)
        if self.role not in TEXT_ROLES:
            raise TypographyError(f"role must be one of {', '.join(TEXT_ROLES)}")
        if self.layout not in TEXT_LAYOUTS:
            raise TypographyError(f"layout must be one of {', '.join(TEXT_LAYOUTS)}")
        if self.motion not in TEXT_MOTIONS:
            raise TypographyError(f"motion must be one of {', '.join(TEXT_MOTIONS)}")
        if len(blocks) not in _LAYOUT_BLOCKS[self.layout]:
            raise TypographyError(
                f"layout {self.layout} does not compose {len(blocks)} blocks"
            )
        if len(blocks) > 1 and len({b.weight for b in blocks}) < 2:
            # the one invariant that separates this layer from a caption
            raise TypographyError("a multi-block event must set at least two weights")
        for name in ("start_seconds", "end_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypographyError(f"{name} must be a number")
            if value < 0:
                raise TypographyError(f"{name} must not be negative")
            object.__setattr__(self, name, float(value))
        if self.end_seconds - self.start_seconds < 0.4:
            raise TypographyError("a typographic event must last at least 400 ms")
        if self.beat_id is not None and (
            not isinstance(self.beat_id, str) or not self.beat_id.strip()
        ):
            raise TypographyError("beat_id must be a non-empty string")
        importance = self.importance
        if isinstance(importance, bool) or not isinstance(importance, (int, float)):
            raise TypographyError("importance must be a number")
        if not 0.0 <= float(importance) <= 1.0:
            raise TypographyError("importance must lie in [0, 1]")
        object.__setattr__(self, "importance", float(importance))
        if self.rationale is not None and not isinstance(self.rationale, str):
            raise TypographyError("rationale must be a string")

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def text(self) -> str:
        """Every block joined, for logging and for duplication checks."""

        return " ".join(block.text for block in self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "blocks": [block.to_dict() for block in self.blocks],
            "role": self.role,
            "layout": self.layout,
            "motion": self.motion,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "beat_id": self.beat_id,
            "importance": self.importance,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MotionTextEvent":
        required = {
            "event_id", "blocks", "role", "layout", "motion",
            "start_seconds", "end_seconds",
        }
        optional = {"beat_id", "importance", "rationale", "schema_version"}
        missing = required - set(data)
        if missing:
            raise TypographyError(f"event requires {', '.join(sorted(missing))}")
        unknown = set(data) - required - optional
        if unknown:
            raise TypographyError(f"event does not accept: {', '.join(sorted(unknown))}")
        blocks = data["blocks"]
        if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
            raise TypographyError("blocks must be an array")
        return cls(
            event_id=data["event_id"],
            blocks=tuple(TextBlock.from_dict(b) for b in blocks),
            role=data["role"],
            layout=data["layout"],
            motion=data["motion"],
            start_seconds=data["start_seconds"],
            end_seconds=data["end_seconds"],
            beat_id=data.get("beat_id"),
            importance=data.get("importance", 0.0),
            rationale=data.get("rationale"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class MotionTypographyPolicy:
    """How often type may interrupt, and how long it stays.

    ``min_events`` exists because the failure this layer is most likely to have
    is silence: a strict gap plus a strict importance floor can easily produce
    four interventions in three and a half minutes. When the first pass falls
    short the gap is relaxed in steps rather than the quality bar, so the
    events that get added are the next-best ones rather than the next-loudest.
    """

    min_events: int = 18
    max_events: int = 24
    min_gap_seconds: float = 6.5
    min_importance: float = 0.38
    hook_seconds: float = 30.0
    hook_min_gap_seconds: float = 5.0
    hook_min_importance: float = 0.3
    hold_seconds: float = 3.0
    min_hold_seconds: float = 1.4
    lead_in_seconds: float = 0.12
    accent_importance: float = 0.72

    def __post_init__(self) -> None:
        for name in ("min_events", "max_events"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise TypographyError(f"{name} must be a positive integer")
        if self.min_events > self.max_events:
            raise TypographyError("min_events must not exceed max_events")
        for name in (
            "min_gap_seconds", "hook_seconds", "hook_min_gap_seconds",
            "hold_seconds", "min_hold_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypographyError(f"{name} must be a number")
            if value <= 0:
                raise TypographyError(f"{name} must be greater than zero")
            object.__setattr__(self, name, float(value))
        for name in (
            "min_importance", "hook_min_importance", "accent_importance",
            "lead_in_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypographyError(f"{name} must be a number")
            if not 0.0 <= float(value) <= 1.0:
                raise TypographyError(f"{name} must lie in [0, 1]")
            object.__setattr__(self, name, float(value))
        if self.min_hold_seconds > self.hold_seconds:
            raise TypographyError("min_hold_seconds must not exceed hold_seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_events": self.min_events,
            "max_events": self.max_events,
            "min_gap_seconds": self.min_gap_seconds,
            "min_importance": self.min_importance,
            "hook_seconds": self.hook_seconds,
            "hook_min_gap_seconds": self.hook_min_gap_seconds,
            "hook_min_importance": self.hook_min_importance,
            "hold_seconds": self.hold_seconds,
            "min_hold_seconds": self.min_hold_seconds,
            "lead_in_seconds": self.lead_in_seconds,
            "accent_importance": self.accent_importance,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MotionTypographyPolicy":
        defaults = cls()
        unknown = set(data) - set(defaults.to_dict())
        if unknown:
            raise TypographyError(f"policy does not accept: {', '.join(sorted(unknown))}")
        merged = defaults.to_dict()
        merged.update(data)
        return cls(**merged)


DEFAULT_TYPOGRAPHY_POLICY = MotionTypographyPolicy()


# --------------------------------------------------------------------------- #
# Timing against the real voice
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SpokenWord:
    """One word of the narration, at the time it is actually spoken."""

    folded: str
    start_seconds: float
    end_seconds: float


def spoken_words(
    cues: "Sequence[Mapping[str, Any] | tuple[str, float, float]]",
) -> tuple[SpokenWord, ...]:
    """Turn force-aligned caption cues into a per-word timeline.

    A cue holds several words and one interval, so the words inside it are
    spread by character length. That is an approximation *within* a cue only —
    every cue boundary is a real measurement — and it is what lets an event
    land on its own word instead of on the shot that happens to contain it.
    """

    out: list[SpokenWord] = []
    previous_end = 0.0
    for index, cue in enumerate(cues):
        if isinstance(cue, Mapping):
            text = cue.get("text")
            start = cue.get("start_seconds")
            end = cue.get("end_seconds")
        else:
            values = tuple(cue)
            if len(values) != 3:
                raise TypographyError("a caption cue must be (text, start, end)")
            text, start, end = values
        if not isinstance(text, str) or not text.strip():
            raise TypographyError(f"caption cue {index} must carry text")
        for name, value in (("start", start), ("end", end)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypographyError(f"caption cue {index} {name} must be a number")
        start = float(start)
        end = float(end)
        if end <= start:
            raise TypographyError(f"caption cue {index} must end after it starts")
        if start < previous_end - 1e-6:
            raise TypographyError("caption cues must be ordered")
        previous_end = end
        words = _words(text)
        if not words:
            continue
        weights = [max(1, len(w.strip(_STRIP))) for w in words]
        total = float(sum(weights))
        cursor = start
        span = end - start
        for word, weight in zip(words, weights):
            share = span * weight / total
            out.append(SpokenWord(_bare(word), cursor, cursor + share))
            cursor += share
    return tuple(out)


def stream_alignment(
    stream: Sequence[str], words: Sequence[SpokenWord]
) -> float:
    """How much of the narration lines up with the measured word timeline.

    The two are the same script read in the same order, so the check is a plain
    index-for-index comparison rather than a search. It exists because the
    consequence of a silent drift is type landing on the wrong sentence: below
    a three-quarters match the caller must fall back to shot timings.
    """

    if not stream or not words:
        return 0.0
    compared = min(len(stream), len(words))
    matched = sum(
        1 for index in range(compared) if _bare(stream[index]) == words[index].folded
    )
    return matched / float(max(len(stream), len(words)))


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Unit:
    """One sentence of the narration, and where it sits in the word stream.

    Beats are shot-sized slices, so a beat routinely stops mid-clause: reading
    them directly is what produced "COMEÇA A / INFORMAÇÃO" for a sentence whose
    actual point was *familiar*. Type answers a sentence, so the planner
    re-assembles sentences from the beat stream and reads those instead. The
    beats keep owning importance, themes and timing.
    """

    beat: NarrationBeat
    text: str
    first: int
    stop: int
    importance: float
    window: tuple[float, float]


@dataclass(frozen=True, slots=True)
class _Candidate:
    unit: _Unit
    texts: tuple[str, ...]
    role: str
    anchor: str
    score: float


_SENTENCE_END = ".!?;"


def _sentence_units(
    beats: Sequence[NarrationBeat], timings: Mapping[str, tuple[float, float]]
) -> tuple[tuple[_Unit, ...], tuple[str, ...]]:
    """Re-assemble the narration into sentences, keeping word indices intact.

    The second return value is the flat word stream, whose indices line up with
    a force-aligned caption timeline word for word.
    """

    surfaces: list[str] = []
    owner: list[int] = []
    for index, beat in enumerate(beats):
        for word in _words(beat.narration):
            surfaces.append(word)
            owner.append(index)

    spans: list[tuple[int, int]] = []
    start = 0
    for position, surface in enumerate(surfaces):
        if surface.rstrip("\"')]").endswith(tuple(_SENTENCE_END)):
            spans.append((start, position + 1))
            start = position + 1
    if start < len(surfaces):
        spans.append((start, len(surfaces)))

    # where each beat's words begin, so a sentence can be placed in time even
    # without a measured word timeline
    beat_first: list[int] = []
    beat_len: list[int] = []
    cursor = 0
    for beat in beats:
        count = len(_words(beat.narration))
        beat_first.append(cursor)
        beat_len.append(count)
        cursor += count

    def _at(position: int) -> float:
        index = owner[min(position, len(owner) - 1)]
        window = timings.get(beats[index].beat_id)
        if window is None:
            return 0.0
        span = float(window[1]) - float(window[0])
        count = max(1, beat_len[index])
        offset = min(count, max(0, position - beat_first[index]))
        return float(window[0]) + span * offset / count

    units: list[_Unit] = []
    for first, stop in spans:
        if stop - first < 3:
            continue
        covered = sorted({owner[i] for i in range(first, stop)})
        if not covered or any(beats[i].beat_id not in timings for i in covered):
            continue
        importance = max(beats[i].importance for i in covered)
        window = (_at(first), _at(stop))
        if window[1] - window[0] < 0.5:
            continue
        units.append(
            _Unit(
                beat=beats[owner[first]],
                text=" ".join(surfaces[first:stop]),
                first=first,
                stop=stop,
                importance=importance,
                window=window,
            )
        )
    return tuple(units), tuple(surfaces)


def _motifs(beats: Sequence[NarrationBeat]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for beat in beats:
        for word in _words(beat.narration):
            folded = _bare(word)
            if folded and folded not in _STOPWORDS and len(folded) >= 4:
                counts[folded] = counts.get(folded, 0) + 1
    return counts


def _themes(beat: NarrationBeat) -> frozenset[str]:
    values = [beat.concept, *beat.entities]
    return frozenset(_bare(w) for value in values for w in _words(value) if _bare(w))


def _compose(
    narration: str, themes: frozenset[str], motifs: Mapping[str, int], in_hook: bool
) -> "tuple[tuple[str, ...], str, str] | None":
    """The text concept: which words go on screen, why, and which one anchors.

    Returns ``(texts, role, anchor)`` where ``anchor`` is the word whose spoken
    moment the event is timed against. Everything here is derivation, never
    transcription: a contrast becomes its two poles, a question becomes its
    subject, and anything else becomes a dominant word plus either a licensed
    connector or the sentence's second-strongest word.
    """

    narration = narration.strip()
    if not narration:
        return None

    # 1. a sentence that turns against itself is the strongest thing type can do
    poles = _split_on_contrast(narration)
    if poles is not None:
        left = _best_word(poles[0], themes, motifs)
        right = _best_word(poles[1], themes, motifs)
        if (
            left
            and right
            and right[1] >= _MIN_DOMINANT_SCORE
            and left[1] >= _MIN_DOMINANT_SCORE
            and _distinct(left[0], right[0])
        ):
            texts = (left[0].upper(), right[0].upper())
            if _is_derived(texts, narration):
                return texts, "contrast", right[0]

    # 2. a question is asked of the viewer, so it is set as one
    if "?" in narration:
        best = _best_word(narration, themes, motifs)
        if best is not None and best[1] >= _MIN_DOMINANT_SCORE:
            connector = _connector(narration) or "A PERGUNTA"
            texts = (connector, best[0].upper())
            if _distinct(connector, best[0]) and _is_derived(texts, narration):
                return texts, "question", best[0]

    # 3. everything else: a dominant word, plus support if the sentence offers it
    ranked = _ranked_words(narration, themes, motifs)
    if not ranked or ranked[0][1] < _MIN_DOMINANT_SCORE:
        return None
    best = ranked[0]
    role = "hook" if in_hook else "statement"

    # A short sentence is already a headline. Setting a support block under it
    # would only repeat, in small type, what the sentence just said — so the
    # whole frame goes to one word.
    if len(_words(narration)) <= _HEADLINE_WORDS and best[1] >= _HEADLINE_MIN_SCORE:
        return (best[0].upper(),), ("hook" if in_hook else "keyword"), best[0]

    # A connector is a turn the sentence itself makes, re-set as the quiet
    # line. It always leads, because that is the order it is spoken in.
    connector = _connector(narration)
    if connector is not None and _distinct(connector, best[0]):
        texts = (connector, best[0].upper())
        if _is_derived(texts, narration):
            return texts, role, best[0]

    second = next(
        (
            row
            for row in ranked[1:]
            if row[1] >= _MIN_DOMINANT_SCORE * 0.72 and _distinct(row[0], best[0])
        ),
        None,
    )
    # A word that towers over everything else in its own sentence does not need
    # a second block: one word, the whole frame.
    towers = second is None or second[1] < best[1] * 0.55
    if second is not None and not towers:
        # Reading order, not ranking order: the pair is set in the order the
        # sentence says it, and the display weight lands on the later word —
        # which is where a Portuguese sentence usually puts its payoff.
        pair = sorted((best, second), key=lambda row: row[2])
        texts = (pair[0][0].upper(), pair[1][0].upper())
        if _is_derived(texts, narration):
            return texts, role, pair[1][0]

    if _is_derived((best[0].upper(),), narration):
        return (best[0].upper(),), ("hook" if in_hook else "keyword"), best[0]
    return None


def _choose_layout(role: str, block_count: int, used: Mapping[str, int], previous: str | None) -> str:
    pool = [
        layout
        for layout in _ROLE_LAYOUTS[role]
        if block_count in _LAYOUT_BLOCKS[layout]
    ]
    if not pool:
        pool = [
            layout
            for layout in TEXT_LAYOUTS
            if block_count in _LAYOUT_BLOCKS[layout]
        ]
    rotated = pool[used.get(role, 0) % len(pool):] + pool[: used.get(role, 0) % len(pool)]
    for layout in rotated:
        if layout != previous:
            return layout
    return rotated[0]


def plan_motion_typography(
    beats: Sequence[NarrationBeat],
    timings: Mapping[str, tuple[float, float]],
    *,
    policy: MotionTypographyPolicy = DEFAULT_TYPOGRAPHY_POLICY,
    words: Sequence[SpokenWord] = (),
) -> tuple[MotionTextEvent, ...]:
    """Compose the typographic layer for a narrated piece.

    ``timings`` maps a ``beat_id`` to the ``(start, end)`` its shot occupies, so
    this function still computes no timing of its own. When ``words`` carries a
    force-aligned word timeline the events are additionally snapped onto the
    moment their anchor word is actually spoken, which is the difference
    between type that lands on a word and type that lands near it.
    """

    if not isinstance(policy, MotionTypographyPolicy):
        raise TypographyError("policy must be a MotionTypographyPolicy")
    ordered = [b for b in beats if isinstance(b, NarrationBeat)]
    if len(ordered) != len(list(beats)):
        raise TypographyError("beats must be NarrationBeat values")
    motifs = _motifs(ordered)
    units, stream = _sentence_units(ordered, timings)
    measured = tuple(words) if stream_alignment(stream, words) >= 0.75 else ()
    timeline_end = max((float(v[1]) for v in timings.values()), default=0.0)

    def _consider(unit: _Unit) -> "_Candidate | None":
        in_hook = unit.window[0] < policy.hook_seconds
        floor = policy.hook_min_importance if in_hook else policy.min_importance
        if unit.importance < floor:
            return None
        composed = _compose(unit.text, _themes(unit.beat), motifs, in_hook)
        if composed is None:
            return None
        texts, role, anchor = composed
        score = unit.importance
        # the two shapes that earn the frame most: a turn in the argument, and
        # a question aimed at the viewer
        if role == "contrast":
            score += 0.10
        elif role == "question":
            score += 0.22
        if in_hook:
            score += 0.10
        return _Candidate(unit, texts, role, anchor, score)

    candidates: list[_Candidate] = []
    for index, unit in enumerate(units):
        candidate = _consider(unit)
        if candidate is not None:
            candidates.append(candidate)
        # Two short sentences side by side are how this script states most of
        # its oppositions ("Para uma, é confirmação. Para a outra, é
        # propaganda."), and neither half carries the contrast alone.
        if index + 1 < len(units):
            nxt = units[index + 1]
            joined = f"{unit.text} {nxt.text}"
            if (
                _opposes(unit.text, nxt.text)
                and len(_words(joined)) <= 24
                and nxt.window[0] - unit.window[1] < 2.0
            ):
                pair = _Unit(
                    beat=unit.beat,
                    text=joined,
                    first=unit.first,
                    stop=nxt.stop,
                    importance=max(unit.importance, nxt.importance),
                    window=(unit.window[0], nxt.window[1]),
                )
                paired = _consider(pair)
                if paired is not None and paired.role == "contrast":
                    candidates.append(paired)

    def _timed(candidate: _Candidate) -> tuple[float, float]:
        start, end = candidate.unit.window
        if measured:
            first = min(candidate.unit.first, len(measured) - 1)
            stop = min(candidate.unit.stop, len(measured))
            anchor = _bare(candidate.anchor)
            spoken = next(
                (w for w in measured[first:stop] if w.folded == anchor), None
            )
            start = spoken.start_seconds if spoken else measured[first].start_seconds
            start = max(0.0, start - policy.lead_in_seconds)
            end = measured[max(first, stop - 1)].end_seconds
        hold = min(policy.hold_seconds, max(policy.min_hold_seconds, end - start + 0.8))
        finish = start + hold
        if timeline_end:
            finish = min(finish, timeline_end)
        return start, finish

    placed = [(candidate, *_timed(candidate)) for candidate in candidates]

    accepted: list[tuple[_Candidate, float, float]] = []
    claimed: set[str] = set()

    def _fits(start: float, end: float, gap: float) -> bool:
        for _c, other_start, other_end in accepted:
            if start < other_end + gap and other_start < end + gap:
                return False
        return True

    def _fresh(candidate: _Candidate) -> bool:
        """Whether this event says something the layer has not said already.

        The same dominant word twice is the failure mode of any keyword-driven
        emphasis layer: a script about intelligence will happily put
        INTELIGÊNCIA on screen six times and call it a motif.
        """

        return _stem(candidate.texts[-1]) not in claimed

    def _accept(row: tuple[_Candidate, float, float]) -> None:
        accepted.append(row)
        claimed.add(_stem(row[0].texts[-1]))

    # The opening is taken in time order: a viewer who has not decided to stay
    # is the one case where the earlier word is worth more than the better one.
    for row in placed:
        if row[1] >= policy.hook_seconds:
            continue
        if len(accepted) >= policy.max_events:
            break
        if _fresh(row[0]) and _fits(row[1], row[2], policy.hook_min_gap_seconds):
            _accept(row)

    body = [row for row in placed if row[1] >= policy.hook_seconds]
    ranked = sorted(body, key=lambda r: (-r[0].score, r[1]))
    for relaxation in (1.0, 0.7, 0.5):
        gap = policy.min_gap_seconds * relaxation
        for row in ranked:
            if len(accepted) >= policy.max_events:
                break
            if any(row[0] is other[0] for other in accepted):
                continue
            if _fresh(row[0]) and _fits(row[1], row[2], gap):
                _accept(row)
        if len(accepted) >= policy.min_events:
            break

    accepted.sort(key=lambda r: r[1])
    # A late event may still have grown into its successor once the anchors
    # moved; the earlier one yields, because it has already been read.
    trimmed: list[tuple[_Candidate, float, float]] = []
    for index, (candidate, start, end) in enumerate(accepted):
        if index + 1 < len(accepted):
            end = min(end, accepted[index + 1][1] - 0.2)
        if end - start < policy.min_hold_seconds * 0.7:
            continue
        trimmed.append((candidate, start, end))

    events: list[MotionTextEvent] = []
    used: dict[str, int] = {}
    previous_layout: str | None = None
    previous_accent = False
    for index, (candidate, start, end) in enumerate(trimmed, start=1):
        layout = _choose_layout(candidate.role, len(candidate.texts), used, previous_layout)
        used[candidate.role] = used.get(candidate.role, 0) + 1
        previous_layout = layout
        weights = _LAYOUT_WEIGHTS[layout][len(candidate.texts)]
        # The accent is a colour this channel owns, so it is spent sparingly:
        # never twice running, however many events deserve it on merit.
        accent_last = not previous_accent and (
            candidate.role in ("contrast", "question")
            or candidate.unit.importance >= policy.accent_importance
        )
        previous_accent = accent_last
        blocks = tuple(
            TextBlock(
                text=text,
                weight=weight,
                accent=accent_last and position == len(candidate.texts) - 1,
            )
            for position, (text, weight) in enumerate(zip(candidate.texts, weights))
        )
        events.append(
            MotionTextEvent(
                event_id=f"type_{index:03d}",
                blocks=blocks,
                role=candidate.role,
                layout=layout,
                motion=_LAYOUT_MOTION[layout],
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                beat_id=candidate.unit.beat.beat_id,
                importance=candidate.unit.importance,
                rationale=(
                    f"{candidate.role} · {layout} · {_LAYOUT_MOTION[layout]} · "
                    f"anchor:{candidate.anchor} · importance "
                    f"{candidate.unit.importance:.2f} · from: {candidate.unit.text}"
                ),
            )
        )
    return tuple(events)


# --------------------------------------------------------------------------- #
# The renderer's operation
# --------------------------------------------------------------------------- #
def _ass_colour(hex_colour: str) -> str:
    """``#RRGGBB`` -> ``&H00BBGGRR``, the order an ASS style wants."""

    digits = hex_colour.lstrip("#")
    if len(digits) != 6:
        raise TypographyError("colour must be #RRGGBB")
    return f"&H00{digits[4:6]}{digits[2:4]}{digits[0:2]}".upper()


def motion_typography_operation(
    events: Sequence[MotionTextEvent],
    *,
    visual_style: "VisualStyle | None" = None,
    display_font: str | None = None,
    support_font: str | None = None,
    muted: str | None = None,
    operation_id: str = "motion_typography",
) -> EditOperation:
    """Turn planned events into the renderer's ``motion_typography`` operation.

    Two font names, not one: the whole visual idea rests on a display face for
    the dominant word and a lighter face for the support line, and a single
    family cannot carry that contrast on its own.
    """

    items: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, MotionTextEvent):
            raise TypographyError("events must be MotionTextEvent values")
        items.append(
            {
                "start_seconds": event.start_seconds,
                "end_seconds": event.end_seconds,
                "layout": event.layout,
                "motion": event.motion,
                "blocks": [block.to_dict() for block in event.blocks],
            }
        )
    if not items:
        raise TypographyError("motion_typography_operation needs at least one event")
    parameters: dict[str, Any] = {"items": items}
    style: dict[str, Any] = {}
    if visual_style is not None:
        if not isinstance(visual_style, VisualStyle):
            raise TypographyError("visual_style must be a VisualStyle")
        style.update(
            {
                "font_name": visual_style.font_name,
                "foreground": _ass_colour(visual_style.foreground),
                "accent": _ass_colour(visual_style.accent),
                "safe_margin_fraction": visual_style.safe_margin_fraction,
            }
        )
    if display_font is not None:
        style["font_name"] = display_font
    if support_font is not None:
        style["support_font_name"] = support_font
    if muted is not None:
        style["muted"] = _ass_colour(muted) if muted.startswith("#") else muted
    if style:
        parameters["style"] = style
    return EditOperation(
        operation_id=operation_id, kind="motion_typography", parameters=parameters
    )
