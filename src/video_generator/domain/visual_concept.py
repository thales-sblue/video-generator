"""Editorial Visual Translation v1 — the layer between what a beat *means* and
what a camera could have pointed at.

:mod:`.editorial` already reads a narration slice into a beat, and
:mod:`.relevance` already reads what *kind* of picture that beat wants and what
that picture has to *do*. Between the two there was still a hole, and the last
production cut measured it exactly:

* **17 of 59 shots** had no filmable concept at all. The concept lexicon is
  keyed on single Portuguese nouns, and an essay sentence like *"o momento em
  que você se considera imune à manipulação"* contains none of them. The
  planner fell back to a bare Portuguese token (``momento``, ``informacao``,
  ``gostamos``) and a human had to write the query by hand.
* **13 more shots asked for a stock prop by name**, because the lexicon itself
  encodes the obvious metaphor: ``verdade`` → *magnifying glass over a
  document* (six magnifying glasses in one cut, including a man holding one in
  front of a mirror), ``inteligencia`` → *chess board mid game* (four chess
  boards), ``pergunta`` → *question mark chalked on a board* (which is how
  coloured chalk on a pavement ended up under the strongest line of the close).

Both are the same failure: the pipeline asked *"which noun is in this
sentence?"* instead of *"what could be filmed to communicate this thought, in
this tone, at this moment?"*.

This module answers the second question, positively, before the provider is
ever asked. It has two halves.

**Beat side.** :func:`translate_beat` returns one :class:`FilmableConcept`: a
concrete, English, filmable scene chosen from a bank of *semantic fields* —
sets of Portuguese vocabulary that name a recurring move in this channel's
argument (self-deception, belief and identity, algorithmic attention,
scrutiny of evidence…), each offering several different scenes that could
carry it. A field is matched on the sentence, not on one noun, so a beat with
no lexicon noun still gets a concept; and the field rotates through its scenes
across the script, so three consecutive shots of one scene stop asking for the
same picture. When nothing matches, a fallback ladder steps down — field →
lexicon (only if it is not a stock prop) → intent scene → tone floor — and
every rung is authored English, so **a Portuguese token can never reach the
provider through this path**.

**Candidate side.** :func:`assess_editorial_fit` reads a candidate's own
metadata and reports what can honestly be inferred from it: whether a person is
in it, whether it plausibly looks like a real observed place, how much of the
chosen concept it actually shares — and, on the negative side, whether it is a
stock metaphor for an abstraction the beat never named, whether its register is
playful, and whether it looks staged. A signal that the metadata cannot support
is reported as **unknown** rather than guessed at.

This complements the hard vetoes in :mod:`.relevance`; it does not replace
them. A veto says *this picture is unusable anywhere*. What is added here is
comparative: a cliché or a playful frame loses **when a sober documentary
alternative exists**, which is why nothing here is a blacklist of slugs.

Pure stdlib, deterministic, no model and no network. The module imports one
sibling, :mod:`.relevance`, for the two closed vocabularies and the shared
rejection names; nothing imports it back.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from video_generator.domain.relevance import (
    VISUAL_INTENTS,
    VISUAL_ROLES,
    REJECTION_REASONS,
)

SCHEMA_VERSION = 1

# How a picture reads next to a narration. Only ``sober`` and ``neutral`` are
# usable by this channel; ``light`` exists so a concept can be *declared*
# unusable rather than silently omitted.
VISUAL_REGISTERS = ("sober", "neutral", "light")

# Which rung of the fallback ladder produced a concept. Reported on every
# concept, so a run can be counted rung by rung instead of guessed at.
CONCEPT_SOURCES = (
    "semantic_field",
    "concept_lexicon",
    "intent_scene",
    "tone_floor",
)

# What :func:`assess_editorial_fit` can say about a candidate. The first three
# are positive, the last three negative; each is in [0, 1] and each may instead
# be reported unknown.
EDITORIAL_FIT_SIGNALS = (
    "human_presence",
    "documentary_plausibility",
    "concept_affinity",
    "literalness_risk",
    "playful_register",
    "staged_artifice",
)

# The two comparative refusals this module can ask for. They are declared in
# :data:`~video_generator.domain.relevance.REJECTION_REASONS` because that
# module owns the vocabulary every layer reports against.
CLICHE_REJECTION = "stock_metaphor_cliche"
PLAYFUL_REJECTION = "playful_register_conflict"

# One vocabulary, one place. If a rename ever splits the two modules apart,
# this fails at import time rather than in a report nobody reads.
for _reason in (CLICHE_REJECTION, PLAYFUL_REJECTION):
    if _reason not in REJECTION_REASONS:  # pragma: no cover - import guard
        raise ImportError(f"relevance.REJECTION_REASONS is missing {_reason}")


class TranslationError(ValueError):
    """Raised when a translation contract or policy is invalid."""


_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_fold(m.group(0)) for m in _WORD.finditer(text or ""))


def _frozen(words: str) -> frozenset[str]:
    return frozenset(words.split())


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranslationError(f"{name} must be a non-empty string")
    return value.strip()


# --------------------------------------------------------------------------- #
# the semantic field bank
# --------------------------------------------------------------------------- #
# A field is *a recurring move in the argument*, named by the Portuguese
# vocabulary that makes it, and answered by several different things a camera
# could be pointed at. Two properties matter and neither is negotiable:
#
# 1. Every scene is a **situation**, not a prop standing for an idea. "man
#    alone reviewing documents in a dim room" is a scene; "magnifying glass" is
#    a prop pretending to be one. This is the whole point of the module: the
#    stock metaphor is not something to filter out later, it is something to
#    stop asking for.
# 2. Every scene is **sober**. A field never offers a cheerful alternative, so
#    the tone of the piece survives the fallback ladder rather than depending
#    on which rung it landed on.
#
# Fields are checked in declaration order and the one sharing most vocabulary
# with the beat wins, so the order below only breaks ties.
@dataclass(frozen=True, slots=True)
class SemanticField:
    """One recurring editorial move and the scenes that can carry it."""

    name: str
    lexicon: frozenset[str]
    scenes: tuple[tuple[str, str], ...]  # (filmable scene, visual family)
    meaning: str

    def __post_init__(self) -> None:
        _text(self.name, "field name")
        _text(self.meaning, "field meaning")
        if not self.scenes:
            raise TranslationError(f"field {self.name} needs at least one scene")
        for scene, family in self.scenes:
            _text(scene, "field scene")
            _text(family, "field scene family")
        if not self.lexicon:
            raise TranslationError(f"field {self.name} needs a lexicon")


def _field(name: str, meaning: str, words: str, *scenes: tuple[str, str]) -> SemanticField:
    return SemanticField(
        name=name,
        lexicon=frozenset(_fold(w) for w in words.split()),
        scenes=tuple(scenes),
        meaning=meaning,
    )


_DEFAULT_FIELDS: tuple[SemanticField, ...] = (
    _field(
        "self_deception",
        "self-confidence becoming a blind spot",
        """
        racional racionais racionalidade imune esperto espertos orgulho ego
        justifico justifica justificar justificativas convincente convence
        convencer defende defender defendeu defendia acho considera considero
        certas certeza infalivel superior
        """,
        ("man alone reviewing documents in a dim room", "solitary_work"),
        ("person studying their own reflection in dark glass", "reflection"),
        ("hands arranging papers under a desk lamp", "hands_detail"),
        ("person sitting still in an empty room at night", "empty_interior"),
        ("a man rereading the same page under a lamp", "solitary_work"),
        ("figure at a window with the room dark behind", "reflection"),
    ),
    _field(
        "belief_and_identity",
        "a belief that is glued to who someone is",
        """
        crenca crencas acredita acreditar acreditam identidade grupo grupos
        pertencer respeita respeitam lado lados opiniao opinioes coladas
        comunidade tribo
        """,
        ("people gathered in a dim room listening", "group"),
        ("person standing apart from a group on a street", "group"),
        ("quiet crowd seen from behind at dusk", "crowd"),
        ("rows of empty chairs in a meeting hall", "empty_interior"),
        ("two people talking in a doorway at night", "group"),
        # Deliberately a *situation* and not a still life: this field answers
        # "everyday_human" beats, and a rare object with no people in it is
        # both hard to find as footage and the wrong picture for one.
        ("people leaving a hall one by one", "group"),
    ),
    _field(
        "information_and_algorithm",
        "attention shaped by what a feed decides to show",
        """
        informacao informacoes algoritmo algoritmos recomendacao recomendacoes
        feed video videos comentario comentarios rede redes plataforma
        plataformas tela telas celular corte cortes mostrar lugares
        """,
        ("hand scrolling a phone screen in the dark", "screen"),
        ("face lit only by a screen at night", "screen"),
        ("rows of identical monitors glowing in a dark room", "screen"),
        ("person walking at night looking at a phone", "street_night"),
        ("a screen reflected in a dark window", "reflection"),
        ("cables and routers blinking in a dark room", "screen"),
    ),
    _field(
        "evidence_and_scrutiny",
        "the moment a claim is put on trial",
        """
        evidencia evidencias prova provas fonte fontes estudo estudos dados
        contexto interrogatorio confiavel contradiz contradizer checar
        verificar relatorio pesquisa
        """,
        ("printed pages spread across a desk in low light", "documents"),
        ("hands turning the pages of a report", "hands_detail"),
        ("red marks across a printed page", "documents"),
        ("empty room with a single lamp over a table", "empty_interior"),
        ("a filing drawer open in a dim archive", "documents"),
        ("one page held up against a window", "hands_detail"),
    ),
    _field(
        "repetition_and_familiarity",
        "something felt true because it was seen often",
        """
        repeticao repete repetida repetido familiar familiaridade vezes muitas
        comum comuns habito frequente frequencia parecer novamente
        """,
        ("identical posters repeated along a wall", "repetition"),
        ("long row of identical windows on a facade", "repetition"),
        ("the same headline on a stack of newspapers", "documents"),
        ("a corridor of repeating doorways", "corridor"),
    ),
    _field(
        "attention_and_watching",
        "being observed, and watching without noticing",
        """
        atencao observa observar observando prende vendo veem olhar olhos
        assistir assiste assistindo percebe percebendo notar
        """,
        ("extreme close up of an eye in shadow", "human_detail"),
        ("person watching from a dark doorway", "threshold"),
        ("security monitors in a dim control room", "screen"),
        ("face half in shadow looking off camera", "portrait"),
    ),
    _field(
        "manipulation_and_control",
        "influence exercised on someone who cannot see it",
        """
        manipulacao manipulado manipulada manipular controle controlar poder
        influencia influenciar ingenua ingenuo enganar enganado enganada
        exploram explorar
        """,
        ("hands moving objects on a table seen from above", "hands_detail"),
        ("silhouette pulling a heavy door open", "threshold"),
        ("figure seen from behind through a doorway", "threshold"),
        ("empty stage under a single work light", "empty_interior"),
    ),
    _field(
        "thinking_alone",
        "a mind working on itself with nobody watching",
        """
        pensa pensam pensamos pensamento pensar mente mentes imagina imaginava
        imaginamos imaginar gostamos analise analisar analisa raciocinio
        conclusao conclusoes reflexao refletir
        """,
        ("person alone thinking by a window in a dim room", "solitary_work"),
        ("person at a desk staring at nothing", "solitary_work"),
        ("notebook and a cold cup on a desk at night", "hands_detail"),
        ("empty chair beside a lit window", "empty_interior"),
        ("a person walking slowly down an empty corridor", "corridor"),
        ("an unmade bed in a room with the light on", "empty_interior"),
    ),
    _field(
        "institution_and_authority",
        "the building the knowledge is kept in",
        """
        psicologia sistema sistemas instituicao instituicoes academia
        universidade autoridade oficial oficialmente disciplina campo
        """,
        ("empty lecture hall in low light", "empty_interior"),
        ("institutional corridor at night", "corridor"),
        ("shelves of bound journals in a library", "documents"),
        ("stone building facade in flat grey light", "architecture"),
    ),
    _field(
        "time_passing",
        "the interval in which something quietly changed",
        """
        semanas semana meses mes anos ano dias dia tempo depois lentamente
        gradualmente eventualmente
        """,
        ("light moving across the wall of an empty room", "empty_interior"),
        ("an empty street at dawn", "street_night"),
        ("dust settled on an untouched desk", "hands_detail"),
        ("long shadows across a bare wall", "shadow"),
    ),
    _field(
        "crowd_and_society",
        "many people, none of them the subject",
        """
        pessoas gente sociedade multidao populacao publico cidadao cidadaos
        todos maioria coletivo
        """,
        ("people walking on a street at dusk", "crowd"),
        ("commuters waiting on a platform", "crowd"),
        ("faces passing in a crowd, none in focus", "crowd"),
        ("pedestrians crossing in low light", "crowd"),
    ),
    _field(
        "error_and_admission",
        "the cost of saying it was wrong",
        """
        errou erro erros errada errado enganou admitir admite mudar mudanca
        reconhecer voltar atras corrigir arrependimento
        """,
        ("crossed out lines in a notebook", "documents"),
        ("a crumpled page on a desk", "hands_detail"),
        ("person pausing in a doorway", "threshold"),
        ("hand hesitating over a written page", "hands_detail"),
    ),
    _field(
        "absurd_belief",
        "a conclusion held with no ground under it",
        """
        absurdo absurda estranha estranho bizarro conspiracao teoria teorias
        delirio irracionalidade irracional inacreditavel
        """,
        ("handwritten notes pinned across a wall", "documents"),
        ("stacks of paper covering a whole table", "documents"),
        ("a diagram drawn on a fogged window", "hands_detail"),
        ("an empty room with papers on the floor", "empty_interior"),
    ),
    _field(
        "human_close",
        "the ordinary person the argument is about",
        """
        humano humana humanos ser seres entender entendemos idealiza idealizar
        desumanizando pessoa individuo alguem
        """,
        ("ordinary person walking alone on a quiet street", "street_night"),
        ("hands resting on a table", "hands_detail"),
        ("portrait of a tired face in soft light", "portrait"),
        ("a lone figure crossing an empty square", "architecture"),
    ),
)


# --------------------------------------------------------------------------- #
# stock metaphors — props that stand in for an abstraction
# --------------------------------------------------------------------------- #
# The prop is not banned. A beat that literally talks about chess may have a
# chess board, and a lecture beat may have a blackboard. What is refused is the
# *lazy substitution*: an abstract beat that never mentioned the object getting
# the catalogue's favourite symbol for it. Every entry below was either
# measured in the last cut or named by the editor as a stock metaphor to watch.
_STOCK_METAPHOR: Mapping[str, str] = MappingProxyType(
    {
        "magnifying": "investigation",
        "magnifier": "investigation",
        "magnify": "investigation",
        "loupe": "investigation",
        "chess": "intelligence",
        "chessboard": "intelligence",
        "pawn": "intelligence",
        "checkmate": "intelligence",
        "lightbulb": "idea",
        "bulb": "idea",
        "maze": "confusion",
        "labyrinth": "confusion",
        "brain": "thought",
        "handshake": "trust",
        "puzzle": "solution",
        "jigsaw": "solution",
        "crayon": "creativity",
        "crayons": "creativity",
        "chalk": "creativity",
        "chalks": "creativity",
        # The verb form is what a lexicon phrase uses ("question mark chalked
        # on a board"), and it is the prop just the same.
        "chalked": "creativity",
        "scales": "justice",
        "gavel": "justice",
        "target": "goal",
        "dartboard": "goal",
        "bullseye": "goal",
        "compass": "direction",
        "hourglass": "time",
        "domino": "consequence",
        "dominoes": "consequence",
        "iceberg": "hidden",
        "mask": "identity",
        "chain": "oppression",
        "chains": "oppression",
        "rocket": "growth",
    }
)

# The **light register**: a tone a grave essay cannot carry, and that no
# ``children`` or ``cheerful`` token announces. Two families sit here because
# both were measured entering this cut through the same blind spot — the
# playful ("coloured chalk drawings on a pavement", under the strongest line of
# the close) and the sentimental ("a hand drawing a heart on a fogged window at
# sunset", 24 seconds into an essay about self-deception). Neither is a slug
# blacklist: these are the words that name a register, and a candidate wearing
# them loses only while a sober alternative is standing.
_PLAYFUL = _frozen(
    """
    colorful colourful coloring colouring colored coloured chalk chalks crayon
    crayons craft crafts sticker stickers rainbow vibrant cheerful playful cute
    festive balloon balloons toy toys hopscotch doodle confetti whimsical
    heart hearts romantic romance valentine kiss kissing hug hugging cuddle
    sweetheart adorable dreamy
    """
)

# Words that mark a photograph as made for a catalogue rather than observed.
_STAGED = _frozen(
    """
    posed posing studio isolated mockup template placeholder copyspace model
    modeling modelling advertisement commercial promotional stockphoto
    """
)

# A person is in the frame.
_HUMAN_PRESENCE = _frozen(
    """
    person people man woman men women hands hand face faces portrait eyes
    silhouette figure walker walkers worker workers pedestrian crowd someone
    """
)

# A place a documentary camera could plausibly have stood in.
_DOCUMENTARY = _frozen(
    """
    street room office corridor hallway window desk table chair doorway door
    night dark dim city building station library apartment stairs wall floor
    candid documentary outdoor indoor interior urban platform pavement bench
    """
)

# Authored, sober scenes for each visual intent class. Rung three of the
# ladder: still English, still a situation, still in tone.
_INTENT_SCENES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "literal": (
            "a quiet room photographed in low light",
            "an ordinary desk with papers, lit from one side",
            "an empty street in flat grey light",
        ),
        "metaphorical": (
            "a lone silhouette in a large empty space",
            "an open door onto a dark corridor",
            "a figure reflected in dark glass",
        ),
        "emotional": (
            "portrait of a person alone, looking away",
            "hands still on a table in low light",
            "a face half in shadow, unsmiling",
        ),
        "scientific": (
            "printed pages of a study on a desk",
            "hands measuring something on a workbench",
            "shelves of bound journals in low light",
        ),
        "evidence_or_archive": (
            "archive folders stacked on a shelf",
            "an old document under a desk lamp",
            "hands turning the pages of a record",
        ),
        "everyday_human": (
            "people walking on a street at dusk",
            "a person waiting alone at a bus stop",
            "commuters on a platform, none in focus",
        ),
        "tension_or_suspense": (
            "an empty corridor lit at one end",
            "a closed door at the end of a dark hallway",
            "an empty room with the light left on",
        ),
    }
)

# Rung four. When even the intent is unknown, the piece still has a tone, and
# these are the plainest sober human situations that carry it.
_TONE_FLOOR: tuple[str, ...] = (
    "a person alone in a dim room",
    "an empty corridor at night",
    "hands on a desk in low light",
    "a lone figure on an empty street",
)

# The English vocabulary every query this module can emit is built from: the
# words of every authored scene above, plus the closed-class words those scenes
# use. It is not a dictionary of English and does not pretend to be one — it is
# the *complete* vocabulary of this module's own output, which is what makes
# the guard below exact for the thing it guards.
_ENGLISH_FUNCTION = _frozen(
    """
    a an the of in on at by to for with from into onto over under across
    behind beside between through along around and or but not no only their
    its his her them they it this that these those one two three own up down
    off out
    """
)


def _authored_vocabulary() -> frozenset[str]:
    words: set[str] = set()
    for entry in _DEFAULT_FIELDS:
        for scene, _family in entry.scenes:
            words.update(_tokens(scene))
    for scenes in _INTENT_SCENES.values():
        for scene in scenes:
            words.update(_tokens(scene))
    for scene in _TONE_FLOOR:
        words.update(_tokens(scene))
    return frozenset(words) | _ENGLISH_FUNCTION


_AUTHORED_VOCABULARY = _authored_vocabulary()


def non_english_tokens(
    query: str, *, vocabulary: "frozenset[str] | None" = None
) -> tuple[str, ...]:
    """Tokens in ``query`` that this module did not author.

    A token is reported when it carries a diacritic — the narration's own
    spelling — or when it is not in the module's authored English vocabulary.
    This is deliberately a **whitelist**, not a language detector: the
    invariant worth guarding is *"every query this layer sends to a provider
    was written in English by this module"*, and a whitelist states exactly
    that, where a Portuguese stopword list would have let ``gostamos``,
    ``informacao`` and ``momento`` through — the three that actually reached
    the provider in the last cut.

    Pass ``vocabulary`` to check a query against a different authored set (a
    hand-written editorial override, for instance, is English but is not this
    module's English).
    """

    allowed = _AUTHORED_VOCABULARY if vocabulary is None else frozenset(vocabulary)
    out: list[str] = []
    for match in _WORD.finditer(query or ""):
        raw = match.group(0)
        folded = _fold(raw)
        if raw.lower() != folded or folded not in allowed:
            out.append(raw)
    return tuple(out)


# --------------------------------------------------------------------------- #
# the concept
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class FilmableConcept:
    """What could be filmed to communicate this beat, and how it was decided.

    Small on purpose. A concept is not a second script: it is one scene, the
    alternatives that could have stood in for it, the abstractions this beat
    must *not* be handed a prop for, and the name of the rule that produced it.
    """

    concept_id: str
    visual_meaning: str
    scene: str
    family: str
    register: str
    queries: tuple[str, ...]
    avoid: tuple[str, ...]
    source: str
    rationale: str
    # Which counter this concept advanced. Two beats that land on the same
    # rung of the ladder share a key, and the caller keeps one tally per key,
    # which is what makes the rotation spread scenes across the whole script
    # instead of restarting at every scene.
    rotation_key: str = "tone_floor"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "concept_id",
            "visual_meaning",
            "scene",
            "family",
            "rationale",
            "rotation_key",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.register not in VISUAL_REGISTERS:
            raise TranslationError(
                f"register must be one of {', '.join(VISUAL_REGISTERS)}"
            )
        if self.source not in CONCEPT_SOURCES:
            raise TranslationError(
                f"source must be one of {', '.join(CONCEPT_SOURCES)}"
            )
        queries = tuple(dict.fromkeys(q.strip() for q in self.queries if q and q.strip()))
        if not queries:
            raise TranslationError("a concept needs at least one query")
        if queries[0] != self.scene:
            raise TranslationError("the first query must be the chosen scene")
        object.__setattr__(self, "queries", queries)
        avoid = tuple(dict.fromkeys(a.strip() for a in self.avoid if a and a.strip()))
        object.__setattr__(self, "avoid", avoid)

    def concept_terms(self) -> frozenset[str]:
        """The folded content tokens of the chosen scene, for candidate scoring."""

        return frozenset(t for t in _tokens(self.scene) if len(t) >= 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "concept_id": self.concept_id,
            "visual_meaning": self.visual_meaning,
            "scene": self.scene,
            "family": self.family,
            "register": self.register,
            "queries": list(self.queries),
            "avoid": list(self.avoid),
            "source": self.source,
            "rationale": self.rationale,
            "rotation_key": self.rotation_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FilmableConcept":
        if not isinstance(data, Mapping):
            raise TranslationError("a filmable concept must be an object")
        return cls(
            concept_id=data.get("concept_id", ""),
            visual_meaning=data.get("visual_meaning", ""),
            scene=data.get("scene", ""),
            family=data.get("family", ""),
            register=data.get("register", "sober"),
            queries=tuple(data.get("queries", ()) or ()),
            avoid=tuple(data.get("avoid", ()) or ()),
            source=data.get("source", "tone_floor"),
            rationale=data.get("rationale", "unknown"),
            rotation_key=data.get("rotation_key", "tone_floor"),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class TranslationPolicy:
    """Editorial Visual Translation knobs and lexicons, as data."""

    enabled: bool = True
    fields: tuple[SemanticField, ...] = field(default_factory=lambda: _DEFAULT_FIELDS)
    stock_metaphors: Mapping[str, str] = field(
        default_factory=lambda: _STOCK_METAPHOR
    )
    playful_lexicon: frozenset[str] = field(default_factory=lambda: _PLAYFUL)
    staged_lexicon: frozenset[str] = field(default_factory=lambda: _STAGED)
    human_lexicon: frozenset[str] = field(default_factory=lambda: _HUMAN_PRESENCE)
    documentary_lexicon: frozenset[str] = field(default_factory=lambda: _DOCUMENTARY)
    # Registers this channel will accept from a concept.
    accepted_registers: tuple[str, ...] = ("sober", "neutral")
    # How many queries a concept offers the resolver. Three is the measured
    # sweet spot: the scene, one sibling from the same field so a retry is a
    # different picture rather than the same one again, and one tone-safe
    # fallback.
    queries_per_concept: int = 3
    # A candidate metadata bag thinner than this cannot support an inference.
    # Below it every signal is reported unknown instead of guessed.
    min_bag_tokens: int = 3
    # Score weights. Positive fit is a nudge of the same order as the existing
    # relevance affinities; the two register penalties are larger, because a
    # cliché or a playful frame is a defect the viewer sees, not a preference.
    weight_editorial_fit: float = 3.0
    penalty_literalness: float = 4.0
    penalty_playful: float = 5.0
    penalty_staged: float = 2.0
    # Comparative refusal thresholds. A flagged candidate is refused only when
    # an unflagged one survived the same ranking — which is what keeps this a
    # judgement about *this* shot rather than a blacklist.
    cliche_reject_at: float = 1.0
    playful_reject_at: float = 0.5

    _FLOAT_FIELDS = (
        "weight_editorial_fit",
        "penalty_literalness",
        "penalty_playful",
        "penalty_staged",
        "cliche_reject_at",
        "playful_reject_at",
    )

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TranslationError("enabled must be a boolean")
        if not self.fields:
            raise TranslationError("at least one semantic field is required")
        for entry in self.fields:
            if not isinstance(entry, SemanticField):
                raise TranslationError("fields must be SemanticField instances")
        object.__setattr__(self, "fields", tuple(self.fields))
        for name in self._FLOAT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TranslationError(f"{name} must be a number")
            if value < 0:
                raise TranslationError(f"{name} must not be negative")
            object.__setattr__(self, name, float(value))
        for name in ("queries_per_concept", "min_bag_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise TranslationError(f"{name} must be a positive integer")
        registers = tuple(self.accepted_registers)
        unknown = [r for r in registers if r not in VISUAL_REGISTERS]
        if unknown:
            raise TranslationError(f"unknown registers: {', '.join(unknown)}")
        if not registers:
            raise TranslationError("at least one accepted register is required")
        object.__setattr__(self, "accepted_registers", registers)
        object.__setattr__(
            self, "stock_metaphors", MappingProxyType(dict(self.stock_metaphors))
        )
        for name in (
            "playful_lexicon",
            "staged_lexicon",
            "human_lexicon",
            "documentary_lexicon",
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                raise TranslationError(f"{name} must be a set of tokens, not a string")
            object.__setattr__(self, name, frozenset(_fold(t) for t in value))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"enabled": self.enabled}
        payload.update({name: getattr(self, name) for name in self._FLOAT_FIELDS})
        payload["queries_per_concept"] = self.queries_per_concept
        payload["min_bag_tokens"] = self.min_bag_tokens
        payload["accepted_registers"] = list(self.accepted_registers)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TranslationPolicy":
        if not isinstance(data, Mapping):
            raise TranslationError("a translation policy must be an object")
        known = {
            "enabled",
            "queries_per_concept",
            "min_bag_tokens",
            "accepted_registers",
            *cls._FLOAT_FIELDS,
        }
        unknown = set(data) - known
        if unknown:
            raise TranslationError(f"unknown policy keys: {', '.join(sorted(unknown))}")
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            kwargs[key] = tuple(value) if key == "accepted_registers" else value
        return cls(**kwargs)


DEFAULT_TRANSLATION_POLICY = TranslationPolicy()


# --------------------------------------------------------------------------- #
# beat side — meaning to filmable concept
# --------------------------------------------------------------------------- #
def stock_metaphor_props(
    text: str, policy: TranslationPolicy = DEFAULT_TRANSLATION_POLICY
) -> tuple[str, ...]:
    """The stock-metaphor props named in ``text``, in order of appearance."""

    seen: dict[str, None] = {}
    for token in _tokens(text):
        if token in policy.stock_metaphors:
            seen.setdefault(token, None)
    return tuple(seen)


def _match_field(
    tokens: "frozenset[str]", policy: TranslationPolicy
) -> "tuple[SemanticField | None, int]":
    """The field this beat's vocabulary belongs to, if any.

    Most hits wins. On a tie the **more specific** field wins — the one with
    the smaller lexicon — because a sentence that shares one word with a broad
    field and one with a narrow one is being named by the narrow one. Ties
    beyond that fall to declaration order, so the choice stays deterministic.
    """

    best: "SemanticField | None" = None
    best_key: "tuple[int, int] | None" = None
    for entry in policy.fields:
        hits = len(tokens & entry.lexicon)
        if not hits:
            continue
        key = (-hits, len(entry.lexicon))
        if best_key is None or key < best_key:
            best, best_key = entry, key
    return best, (0 if best_key is None else -best_key[0])


def translate_beat(
    narration: str,
    *,
    concept: str = "",
    lexicon_scene: str = "",
    context: str = "",
    visual_intent_class: str | None = None,
    visual_role: str | None = None,
    lexicon_repeat: bool = False,
    rotation: int = 0,
    rotations: "Mapping[str, int] | None" = None,
    policy: TranslationPolicy = DEFAULT_TRANSLATION_POLICY,
) -> FilmableConcept:
    """Translate one beat into the concrete thing a camera could point at.

    The ladder is **lexicon first**, and that order was decided by measurement
    rather than by taste. When the concept lexicon already has a concrete
    answer for a noun the narration actually said — *"Duas pessoas leem a mesma
    notícia"* → a newspaper headline — that answer is more faithful than any
    field the sentence also happens to match, and replacing it made the cut
    worse in exactly those places. So ``lexicon_scene`` leads, and the semantic
    field takes over in the three cases where the lexicon is the problem:

    * there is no lexicon hit at all (the 17 shots that needed a hand-written
      query in the last cut);
    * the lexicon's answer is a **stock metaphor prop** the beat never said —
      ``verdade`` → *magnifying glass*, ``inteligencia`` → *chess board*,
      ``pergunta`` → *question mark chalked on a board*;
    * the lexicon has already given this same scene to an earlier beat
      (``lexicon_repeat``), which is how one scene ended up asking for the same
      picture three times running.

    The rung is chosen first and the rotation second, so a caller that keeps a
    tally per :attr:`FilmableConcept.rotation_key` (pass it as ``rotations``)
    spreads a field's scenes across the whole script: three consecutive shots
    of one scene ask for three different pictures. ``rotation`` is the explicit
    form of the same thing, for a caller with one beat and no tally.

    The ladder never falls through to the narration's own words, which is the
    property that removes the bare-Portuguese query entirely.
    """

    if visual_intent_class is not None and visual_intent_class not in VISUAL_INTENTS:
        raise TranslationError(f"unknown visual_intent_class: {visual_intent_class}")
    if visual_role is not None and visual_role not in VISUAL_ROLES:
        raise TranslationError(f"unknown visual_role: {visual_role}")
    if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation < 0:
        raise TranslationError("rotation must be a non-negative integer")

    words = frozenset(_tokens(narration))
    matched, hits = _match_field(words, policy)
    scope = "beat"
    if matched is None and context and context != narration:
        matched, hits = _match_field(frozenset(_tokens(context)), policy)
        scope = "scene"

    intent = visual_intent_class or "metaphorical"
    intent_scenes = _INTENT_SCENES[intent]
    named = frozenset(stock_metaphor_props(narration, policy)) | frozenset(
        stock_metaphor_props(context, policy)
    )
    avoid = tuple(sorted({policy.stock_metaphors[p] for p in named}))
    lexicon_props = stock_metaphor_props(lexicon_scene, policy)
    lexicon_usable = bool(lexicon_scene) and (
        not lexicon_props or set(lexicon_props) <= named
    )

    if lexicon_repeat:
        lexicon_usable = False

    # --- pick the rung, then the rotation ---------------------------------- #
    if lexicon_usable:
        key = f"lexicon:{_fold(concept) or 'scene'}"
    elif matched is not None:
        key = f"field:{matched.name}"
    elif visual_intent_class is not None:
        key = f"intent:{visual_intent_class}"
    else:
        key = "tone_floor"
    if rotations is not None:
        value = rotations.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TranslationError("rotations values must be non-negative integers")
        rotation = value

    if lexicon_usable:
        queries = [lexicon_scene, intent_scenes[rotation % len(intent_scenes)]]
        return FilmableConcept(
            concept_id=f"lexicon/{_fold(concept) or 'scene'}",
            visual_meaning=concept or "the beat's own concept",
            scene=lexicon_scene,
            family="lexicon",
            register="sober",
            queries=tuple(queries[: policy.queries_per_concept]),
            avoid=avoid,
            source="concept_lexicon",
            rationale=f"lexicon:{_fold(concept) or 'scene'} prop_free",
            rotation_key=key,
        )

    if matched is not None:
        index = rotation % len(matched.scenes)
        scene, family = matched.scenes[index]
        sibling = matched.scenes[(index + 1) % len(matched.scenes)][0]
        queries = [scene, sibling, intent_scenes[rotation % len(intent_scenes)]]
        return FilmableConcept(
            concept_id=f"{matched.name}/{index + 1}",
            visual_meaning=matched.meaning,
            scene=scene,
            family=family,
            register="sober",
            queries=tuple(queries[: policy.queries_per_concept]),
            # A field's own scenes never name a prop, so what the beat has to
            # be protected from is whatever it was about to be handed instead.
            avoid=avoid or _abstractions_for(matched, policy),
            source="semantic_field",
            rationale=(
                f"field:{matched.name} hits:{hits} scope:{scope} rot:{index}"
                + (
                    f" over-lexicon:{','.join(lexicon_props)}"
                    if lexicon_props
                    else (" over-lexicon:repeat" if lexicon_repeat and lexicon_scene else "")
                )
            ),
            rotation_key=key,
        )

    blocked = ""
    if lexicon_props:
        blocked = f" blocked:{','.join(lexicon_props)}"
    elif lexicon_repeat and lexicon_scene:
        blocked = " blocked:lexicon_repeat"
    if visual_intent_class is not None:
        index = rotation % len(intent_scenes)
        queries = [
            intent_scenes[index],
            intent_scenes[(index + 1) % len(intent_scenes)],
            _TONE_FLOOR[rotation % len(_TONE_FLOOR)],
        ]
        return FilmableConcept(
            concept_id=f"intent/{visual_intent_class}/{index + 1}",
            visual_meaning=(
                f"a {visual_intent_class.replace('_', ' ')} beat with no named concept"
            ),
            scene=intent_scenes[index],
            family="intent",
            register="sober",
            queries=tuple(queries[: policy.queries_per_concept]),
            avoid=avoid
            or tuple(sorted({policy.stock_metaphors[p] for p in lexicon_props})),
            source="intent_scene",
            rationale=f"intent:{visual_intent_class} rot:{index}{blocked}",
            rotation_key=key,
        )

    index = rotation % len(_TONE_FLOOR)
    queries = [_TONE_FLOOR[index], _TONE_FLOOR[(index + 1) % len(_TONE_FLOOR)]]
    return FilmableConcept(
        concept_id=f"tone_floor/{index + 1}",
        visual_meaning="no readable concept; the tone is all that is known",
        scene=_TONE_FLOOR[index],
        family="tone_floor",
        register="sober",
        queries=tuple(queries[: policy.queries_per_concept]),
        avoid=avoid,
        source="tone_floor",
        rationale=f"tone_floor rot:{index}{blocked}",
        rotation_key=key,
    )


def _abstractions_for(
    matched: SemanticField, policy: TranslationPolicy
) -> tuple[str, ...]:
    """The stock abstractions a field is most likely to be handed a prop for."""

    hint = {
        "self_deception": ("investigation", "thought"),
        "thinking_alone": ("thought", "idea"),
        "evidence_and_scrutiny": ("investigation", "justice"),
        "absurd_belief": ("confusion", "thought"),
        "error_and_admission": ("solution",),
        "manipulation_and_control": ("oppression", "identity"),
        "attention_and_watching": ("investigation",),
        "repetition_and_familiarity": ("time",),
        "belief_and_identity": ("identity", "trust"),
        "institution_and_authority": ("intelligence",),
    }.get(matched.name, ())
    known = set(policy.stock_metaphors.values())
    return tuple(sorted(a for a in hint if a in known))


# --------------------------------------------------------------------------- #
# candidate side — positive editorial assessment
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EditorialFit:
    """What can honestly be said about one candidate from its own metadata.

    Every named signal is either in ``signals`` with a value in [0, 1] or in
    ``unknown``. Nothing is both, and nothing is invented: a candidate whose
    provider published three words of metadata reports six unknowns rather
    than six confident zeroes.
    """

    signals: Mapping[str, float]
    unknown: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        signals = dict(self.signals)
        for name, value in signals.items():
            if name not in EDITORIAL_FIT_SIGNALS:
                raise TranslationError(f"unknown editorial fit signal: {name}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TranslationError(f"signal {name} must be a number")
            if not 0.0 <= float(value) <= 1.0:
                raise TranslationError(f"signal {name} must lie in [0, 1]")
            signals[name] = float(value)
        unknown = tuple(dict.fromkeys(self.unknown))
        bad = [u for u in unknown if u not in EDITORIAL_FIT_SIGNALS]
        if bad:
            raise TranslationError(f"unknown editorial fit signal: {', '.join(bad)}")
        overlap = set(unknown) & set(signals)
        if overlap:
            raise TranslationError(
                f"a signal cannot be both known and unknown: {', '.join(sorted(overlap))}"
            )
        object.__setattr__(self, "signals", MappingProxyType(signals))
        object.__setattr__(self, "unknown", unknown)
        object.__setattr__(self, "notes", tuple(self.notes))

    def get(self, name: str) -> float:
        """The signal's value, or 0.0 when it could not be inferred."""

        return float(self.signals.get(name, 0.0))

    def components(
        self, policy: TranslationPolicy = DEFAULT_TRANSLATION_POLICY
    ) -> dict[str, float]:
        """The additive score contributions this fit makes to a candidate."""

        positive = (
            0.35 * self.get("human_presence")
            + 0.35 * self.get("documentary_plausibility")
            + 0.30 * self.get("concept_affinity")
        )
        return {
            "editorial_fit": policy.weight_editorial_fit * positive,
            "literalness_penalty": -policy.penalty_literalness
            * self.get("literalness_risk"),
            "playful_register_penalty": -policy.penalty_playful
            * self.get("playful_register"),
            "staged_artifice_penalty": -policy.penalty_staged
            * self.get("staged_artifice"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": {k: round(v, 4) for k, v in self.signals.items()},
            "unknown": list(self.unknown),
            "notes": list(self.notes),
        }


def assess_editorial_fit(
    bag: "frozenset[str] | set[str]",
    *,
    concept_terms: "frozenset[str] | set[str]" = frozenset(),
    query_terms: Sequence[str] = (),
    narration_terms: "frozenset[str] | set[str]" = frozenset(),
    visual_intent_class: str | None = None,
    policy: TranslationPolicy = DEFAULT_TRANSLATION_POLICY,
) -> EditorialFit:
    """Read one candidate positively, and name what cannot be read.

    ``query_terms`` and ``narration_terms`` are what the beat *asked for*: a
    prop the beat itself named is not a lazy substitution, so it carries no
    literalness risk. ``concept_terms`` are the tokens of the filmable concept
    that was chosen, which is a stricter thing to share than the query.
    """

    tokens = frozenset(_fold(t) for t in bag)
    if not policy.enabled:
        return EditorialFit({}, tuple(EDITORIAL_FIT_SIGNALS), ("translation disabled",))
    if len(tokens) < policy.min_bag_tokens:
        return EditorialFit(
            {},
            tuple(EDITORIAL_FIT_SIGNALS),
            (f"metadata bag has {len(tokens)} tokens; nothing can be inferred",),
        )

    asked = {_fold(t) for t in query_terms} | {_fold(t) for t in narration_terms}
    signals: dict[str, float] = {}
    notes: list[str] = []
    unknown: list[str] = []

    human = len(tokens & policy.human_lexicon)
    signals["human_presence"] = min(1.0, human / 2.0)

    documentary = len(tokens & policy.documentary_lexicon)
    staged = len(tokens & policy.staged_lexicon)
    signals["documentary_plausibility"] = max(
        0.0, min(1.0, documentary / 2.0) - min(1.0, staged / 2.0)
    )
    signals["staged_artifice"] = min(1.0, staged / 2.0)

    concept = frozenset(_fold(t) for t in concept_terms)
    if concept:
        shared = len(tokens & concept)
        signals["concept_affinity"] = min(1.0, shared / min(3.0, float(len(concept))))
    else:
        unknown.append("concept_affinity")
        notes.append("no filmable concept was supplied for this requirement")

    # A prop only counts when the beat never named it, and only when the beat
    # is abstract: a literal beat about a chess match is allowed a chess board.
    props = [
        token
        for token in tokens
        if token in policy.stock_metaphors and token not in asked
    ]
    literal_beat = visual_intent_class in ("literal", "scientific", "evidence_or_archive")
    if props and not literal_beat:
        signals["literalness_risk"] = min(1.0, len(props) / 1.0)
        notes.append(
            "stock metaphor for "
            + ", ".join(sorted({policy.stock_metaphors[p] for p in props}))
            + f" ({', '.join(sorted(props))}) the beat never named"
        )
    else:
        signals["literalness_risk"] = 0.0
        if props:
            notes.append(
                f"prop {', '.join(sorted(props))} allowed: the beat is {visual_intent_class}"
            )

    playful = len(tokens & policy.playful_lexicon)
    signals["playful_register"] = min(1.0, playful / 2.0)
    if playful:
        notes.append(
            "playful register: " + ", ".join(sorted(tokens & policy.playful_lexicon))
        )

    return EditorialFit(signals, tuple(unknown), tuple(notes))


def comparative_rejections(
    fits: "Sequence[tuple[str, EditorialFit]]",
    policy: TranslationPolicy = DEFAULT_TRANSLATION_POLICY,
) -> dict[str, tuple[str, ...]]:
    """Which candidates lose *because a better-registered alternative exists*.

    ``fits`` is ``(candidate_id, fit)`` for every candidate that survived the
    structural and hard-veto checks. A candidate flagged for cliché or for a
    playful register is refused only while at least one unflagged candidate is
    still standing — so a beat whose entire result set is clichéd still gets a
    picture, and the report says why it is the one it is.
    """

    if not policy.enabled:
        return {}
    flagged: dict[str, list[str]] = {}
    clean = 0
    for candidate_id, fit in fits:
        reasons: list[str] = []
        if fit.get("literalness_risk") >= policy.cliche_reject_at:
            reasons.append(CLICHE_REJECTION)
        if fit.get("playful_register") >= policy.playful_reject_at:
            reasons.append(PLAYFUL_REJECTION)
        if reasons:
            flagged[candidate_id] = reasons
        else:
            clean += 1
    if not clean:
        return {}
    return {cid: tuple(reasons) for cid, reasons in flagged.items()}


__all__ = [
    "SCHEMA_VERSION",
    "VISUAL_REGISTERS",
    "CONCEPT_SOURCES",
    "EDITORIAL_FIT_SIGNALS",
    "CLICHE_REJECTION",
    "PLAYFUL_REJECTION",
    "TranslationError",
    "SemanticField",
    "FilmableConcept",
    "TranslationPolicy",
    "DEFAULT_TRANSLATION_POLICY",
    "EditorialFit",
    "translate_beat",
    "assess_editorial_fit",
    "comparative_rejections",
    "stock_metaphor_props",
    "non_english_tokens",
]
