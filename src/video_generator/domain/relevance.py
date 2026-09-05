"""Semantic Visual Relevance v1 — the layer between an editorial beat and the
asset that will stand for it.

The planner already knows *what a cut is about* (:mod:`.editorial`) and the
resolver already knows *whether a file fits* (:mod:`.assets`). Between the two
there was nothing: a beat asked for "a topic" and any picture sharing a word
with that topic won. That is how a script about a human being ends up narrated
over glowing CGI neurons.

This module adds the missing question — **why is the viewer looking at this?** —
in three deterministic steps:

1. :func:`read_visual_intent` classifies *what kind of image* the beat calls
   for (:data:`VISUAL_INTENTS`).
2. :func:`read_visual_role` classifies *what the image has to do* for the
   argument (:data:`VISUAL_ROLES`).
3. :func:`refine_query` rewrites the English asset query so it asks for
   "topic + editorial intention" instead of "topic".

The same two labels then bias the candidate ranking (:func:`relevance_components`)
and can reject a candidate outright (:func:`relevance_rejections`) — a picture
that matches on one stray keyword, a generic stock plate, an image whose mood
contradicts the narration, or the fourth abstract render in a row.

Everything here is a deterministic lexical heuristic over stdlib only. No
model, no embedding, no network. The lexicons are data on
:class:`RelevancePolicy`, so a bad mapping is a one-line edit, and every
decision comes back as a named reason rather than a number nobody can read.

The module is pure and imports no sibling domain module: :mod:`.editorial`,
:mod:`.planning` and :mod:`.assets` all depend on it, never the other way
round.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1

# What kind of picture the beat is asking for.
VISUAL_INTENTS = (
    "literal",
    "metaphorical",
    "emotional",
    "scientific",
    "evidence_or_archive",
    "everyday_human",
    "tension_or_suspense",
)

# What that picture has to accomplish for the argument.
VISUAL_ROLES = (
    "explain",
    "symbolize",
    "shock",
    "humanize",
    "contextualize",
    "build_tension",
    "support_claim",
)

# Every rejection this module can emit, so a caller can enumerate them.
#
# The first five *rank* a candidate out: they say this picture is a poor fit
# for this beat. The ``hard_veto_*`` family says something stronger — this
# picture would damage the piece wherever it landed. A dark documentary essay
# does not survive one glowing CGI brain, one cartoon or one confetti shot,
# however well the rest of the ranking scored it, so those are refused on
# sight rather than penalised and hoped away.
REJECTION_REASONS = (
    "keyword_only_match",
    "generic_stock",
    "tone_conflict",
    "abstract_cgi_mismatch",
    "visual_language_repetition",
    # Editorial Visual Translation v1 asks for these two, and they are
    # comparative in a stronger sense than the five above: they are decided
    # against the *rest of the result set*, not against the beat alone. A
    # stock metaphor or a playful frame loses only while a sober documentary
    # alternative is still standing, which is why they are not vetoes.
    "stock_metaphor_cliche",
    "playful_register_conflict",
    "hard_veto_cgi",
    "hard_veto_cartoon",
    "hard_veto_cheerful",
    "hard_veto_children",
    "hard_veto_neon_scifi",
    "hard_veto_fantasy",
    "hard_veto_commercial",
    "hard_veto_unasked_animal",
)

# The rules whose name starts here are absolute rather than comparative.
HARD_VETO_REASONS = tuple(r for r in REJECTION_REASONS if r.startswith("hard_veto_"))


class RelevanceError(ValueError):
    """Raised when a relevance contract or policy is invalid."""


_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")
_NUMERIC = re.compile(r"\b\d")


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_fold(m.group(0)) for m in _WORD.finditer(text or ""))


def _frozen(words: str) -> frozenset[str]:
    return frozenset(words.split())


def _check_lexicon(value: object, name: str) -> frozenset[str]:
    if isinstance(value, str):
        raise RelevanceError(f"{name} must be a set of tokens, not a string")
    try:
        tokens = frozenset(value)  # type: ignore[arg-type]
    except TypeError as exc:  # pragma: no cover - defensive
        raise RelevanceError(f"{name} must be iterable") from exc
    if any(not isinstance(t, str) or not t.strip() for t in tokens):
        raise RelevanceError(f"{name} must contain non-empty strings")
    return frozenset(_fold(t) for t in tokens)


def _non_negative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RelevanceError(f"{name} must be a number")
    if value < 0:
        raise RelevanceError(f"{name} must not be negative")
    return float(value)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RelevanceError(f"{name} must be an integer")
    if value < 1:
        raise RelevanceError(f"{name} must be at least 1")
    return value


# --------------------------------------------------------------------------- #
# beat-side lexicons — Portuguese, read off the narration
# --------------------------------------------------------------------------- #
# Numbers, dates, records, press: the beat is producing proof.
_PT_ARCHIVE = _frozen(
    """
    documento documentos arquivo arquivos registro registros jornal jornais
    manchete noticia noticias carta cartas patente patentes artigo artigos
    publicou publicado relatorio processo dossie ata certidao historico
    biografia carta_manuscrita manuscrito diario boletim
    """
)

# Laboratory, measurement, formal knowledge.
_PT_SCIENCE = _frozen(
    """
    ciencia cientifico cientifica cientista fisica quimica biologia matematica
    equacao equacoes formula teoria teorema experimento experimentos laboratorio
    pesquisa estudo estudos dados estatistica microscopio celula celulas
    neuronio neuronios cerebro neural sinapse genetica atomo particula
    """
)

# Suspense cues: withholding, threat, secrecy, the unspoken.
_PT_TENSION = _frozen(
    """
    segredo segredos escondeu esconde escondido oculto ninguem nunca jamais
    perigo ameaca ameacou risco medo terror sombrio escuro silencio proibido
    censura apagado sumiu desapareceu misterio suspeita conspiracao
    """
)

# The camera can point at a person doing an ordinary thing.
_PT_EVERYDAY = _frozen(
    """
    pessoa pessoas gente familia crianca criancas filho filha pai mae
    trabalhador trabalhadores morador vizinho rotina casa cozinha rua bairro
    onibus fila mercado cotidiano dia_a_dia multidao populacao cidadao
    """
)

# Abstractions with no filmable referent: they have to be symbolised.
_PT_ABSTRACT = _frozen(
    """
    sistema estrutura poder controle liberdade justica verdade mentira destino
    futuro passado progresso natureza essencia sentido proposito valor moral
    etica cultura civilizacao humanidade desigualdade opressao dominacao
    conceito criterio padrao norma logica ordem caos limite fronteira
    """
)

# A negative emotional colour: cheerful stock contradicts it outright.
_NEGATIVE_EMOTIONS = _frozen(
    """
    rejection rejected frustrated defeated fearful uncertain uncomfortable sad
    lonely angry ashamed tired grief anxious desperate
    """
)


# --------------------------------------------------------------------------- #
# candidate-side lexicons — English, read off provider metadata
# --------------------------------------------------------------------------- #
_INTENT_AFFINITY: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        # "literal" has no affinity list on purpose: a literal beat is served
        # by whatever its own concrete query already names, and inventing an
        # affinity bonus here would only reward vocabulary, not meaning.
        "literal": frozenset(),
        "metaphorical": _frozen(
            """
            symbolic symbol abstract conceptual metaphor silhouette shadow
            shadows mirror maze chain chains door doorway road path horizon
            crossroads scale balance cage bridge
            """
        ),
        "emotional": _frozen(
            """
            face portrait eyes hands alone lonely crying tears quiet intimate
            candid emotion emotional expression solitude reflective pensive
            """
        ),
        "scientific": _frozen(
            """
            laboratory lab science scientific scientist research microscope
            equation equations formula chalkboard blackboard experiment data
            physics chemistry biology telescope specimen
            """
        ),
        "evidence_or_archive": _frozen(
            """
            archive archival document documents paper papers newspaper letter
            manuscript vintage historic historical antique records file folder
            stamp typewriter ledger
            """
        ),
        "everyday_human": _frozen(
            """
            people person man woman child children family street crowd worker
            workers home kitchen commute daily ordinary neighbourhood market
            candid pedestrian
            """
        ),
        "tension_or_suspense": _frozen(
            """
            dark darkness night shadow shadows storm fog mist corridor empty
            moody dim silhouette rain smoke abandoned locked alone
            """
        ),
    }
)

_ROLE_AFFINITY: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "explain": _frozen(
            """
            diagram chart blackboard chalkboard demonstration model illustration
            schematic drawing sketch map graph
            """
        ),
        "symbolize": _frozen(
            """
            symbolic symbol abstract conceptual silhouette metaphor door road
            chain mirror maze horizon scale cage
            """
        ),
        "shock": _frozen(
            """
            dramatic intense destruction collapse explosion crash ruins fire
            wreck violent scream chaos
            """
        ),
        "humanize": _frozen(
            """
            face portrait hands eyes candid child family elderly tears person
            smile embrace closeup
            """
        ),
        "contextualize": _frozen(
            """
            aerial cityscape skyline landscape wide establishing panorama street
            building facade overview horizon
            """
        ),
        "build_tension": _frozen(
            """
            dark shadow night corridor fog storm waiting silhouette dim closed
            empty rain suspense
            """
        ),
        "support_claim": _frozen(
            """
            document documents newspaper chart graph data statistics archive
            report evidence records figures
            """
        ),
    }
)

# Terms that mark an image with real screen presence. Retention is not a
# measurable property of a keyword list, so this is deliberately a small nudge,
# never a decisive weight.
_VISUAL_STRENGTH = _frozen(
    """
    dramatic cinematic silhouette closeup macro aerial storm night motion
    contrast moody backlit timelapse crowd fire rain smoke shadow portrait eyes
    hands golden sunrise sunset reflection texture depth
    """
)

# Catalogue filler: technically a photograph, editorially nothing.
_GENERIC_STOCK = _frozen(
    """
    business businessman businesswoman businesspeople handshake teamwork
    corporate mockup template banner copyspace copy isolated placeholder
    clipart watermark generic thumbsup collage infographic advertisement
    marketing branding lifestyle posed studio
    """
)

# The house style of every AI/neuroscience stock reel. Fine for a scientific
# beat, fatal for a human one.
_ABSTRACT_CGI = _frozen(
    """
    render rendering 3d cgi digital futuristic hologram holographic neuron
    neurons neural synapse brain artificial cyber cyberspace network particles
    particle plexus wireframe glowing hud virtual metaverse
    """
)

# Cheerfulness, which a dark narration cannot carry.
_CHEERFUL = _frozen(
    """
    happy happiness smiling smile cheerful joy joyful fun funny celebration
    party laughing excited playful vacation holiday festive success winning
    """
)

# --------------------------------------------------------------------------- #
# hard veto lexicons — pictures a dark documentary cannot carry at all
# --------------------------------------------------------------------------- #
# Deliberately narrower than _ABSTRACT_CGI: this is the *look*, not the topic.
# "brain" alone is a subject a scientific beat may legitimately want; "glowing
# neural network render" is the stock reel that ruins the shot.
# Single-hit terms: one of these in a candidate's own metadata *is* the look.
# ``abstract`` and ``render`` are here after measurement, not on principle —
# every stock plate that survived the first pass of this veto ("abstract 3d
# geometric waveform", "abstract blue maze", "3d ai imaging") was named by
# exactly one of them. Note that a two-character token like "3d" never reaches
# a candidate's metadata bag, which is why it cannot carry a rule.
_VETO_CGI = _frozen(
    """
    cgi hologram holographic wireframe plexus metaverse cyberspace
    neuron neurons synapse synapses neural
    render rendering abstract generative imaging futuristic
    """
)
# Softer terms: one is a coincidence, two together are a render.
_VETO_CGI_QUALIFIER = _frozen(
    """
    digital glowing glow particles particle generated virtual artificial
    geometric minimalist gradient shapes background
    """
)
_VETO_CARTOON = _frozen(
    """
    cartoon cartoons comic anime manga clipart vector doodle caricature
    illustration illustrated drawing drawn sketch animated animation cute
    mascot emoji
    """
)
_VETO_CHEERFUL = _frozen(
    """
    confetti balloons balloon celebration celebrating party festive cheers
    toast fireworks birthday wedding congratulations thumbsup winner winning
    applause smiling laughing joyful cheerful playful
    """
)
_VETO_CHILDREN = _frozen(
    """
    kid kids child children toddler toddlers kindergarten preschool nursery
    playground playroom crayon crayons toy toys cartoonish schoolkids
    """
)
# The beat may legitimately be about school; then the picture is not a veto.
_CHILD_CONTEXT = _frozen(
    """
    child children kid kids school classroom student students pupil teacher
    education exam lesson escola crianca criancas aluno alunos infancia
    """
)
_VETO_NEON_SCIFI = _frozen(
    """
    neon cyberpunk scifi sciencefiction spaceship spacecraft robot android
    alien galaxy starship laser matrix hud dystopian
    """
)
_VETO_FANTASY = _frozen(
    """
    fantasy dragon dragons wizard magic magical unicorn mythical fairy
    superhero mermaid castle knight potion witch
    """
)
_VETO_COMMERCIAL = _frozen(
    """
    advertisement advertising promo promotional influencer luxury glamour
    fashionable model modeling posing posed showroom shopping ecommerce
    packshot billboard branded sponsored
    """
)
# An animal that nobody asked for is how a serious essay acquires a lizard on a
# chessboard: cheap to spot, and it is never what the beat meant.
_VETO_ANIMALS = _frozen(
    """
    lizard iguana gecko snake frog turtle parrot monkey hamster puppy kitten
    dog dogs cat cats horse cow pig sheep goat duck chicken rabbit squirrel
    dinosaur shark dolphin panda elephant giraffe tiger lion bear
    """
)

# Coarse visual families, checked in order. Two shots in a row from the same
# family read as the same shot, whatever the file names say.
_FAMILY_ORDER: tuple[tuple[str, frozenset[str]], ...] = (
    ("abstract_cgi", _ABSTRACT_CGI),
    (
        "archive_document",
        _frozen(
            """
            document documents paper papers newspaper letter manuscript archive
            archival book books page pages typewriter ledger file
            """
        ),
    ),
    (
        "science_lab",
        _frozen(
            """
            laboratory lab microscope experiment specimen chemistry equation
            chalkboard blackboard telescope
            """
        ),
    ),
    (
        "human_face",
        _frozen(
            """
            face portrait eyes closeup child elderly woman man person hands
            """
        ),
    ),
    ("crowd", _frozen("crowd people pedestrians commuters queue protest march")),
    (
        "cityscape",
        _frozen(
            """
            city cityscape skyline street building buildings facade aerial
            traffic bridge urban
            """
        ),
    ),
    (
        "nature",
        _frozen(
            """
            forest tree trees ocean sea mountain sky clouds field river desert
            storm rain snow
            """
        ),
    ),
    (
        "interior",
        _frozen("room office desk classroom corridor hall library window door"),
    ),
)


# --------------------------------------------------------------------------- #
# query refinement modifiers — at most one per axis, on purpose
# --------------------------------------------------------------------------- #
_INTENT_MODIFIER: Mapping[str, str] = MappingProxyType(
    {
        "literal": "",
        "metaphorical": "symbolic",
        "emotional": "intimate",
        "scientific": "scientific",
        "evidence_or_archive": "archival",
        "everyday_human": "candid",
        "tension_or_suspense": "dark",
    }
)

_ROLE_MODIFIER: Mapping[str, str] = MappingProxyType(
    {
        "explain": "",
        "symbolize": "conceptual",
        "shock": "dramatic",
        "humanize": "closeup",
        "contextualize": "wide",
        "build_tension": "moody",
        "support_claim": "documentary",
    }
)

# Used only when a beat has no filmable concept at all, so the alternative is
# an empty search rather than a diluted one.
_INTENT_FALLBACK: Mapping[str, str] = MappingProxyType(
    {
        "literal": "documentary still life",
        "metaphorical": "symbolic silhouette",
        "emotional": "lonely person portrait",
        "scientific": "laboratory close up",
        "evidence_or_archive": "archival documents",
        "everyday_human": "people on a street",
        "tension_or_suspense": "dark empty corridor",
    }
)


@dataclass(frozen=True, slots=True)
class RelevancePolicy:
    """Relevance knobs and lexicons, as data rather than scattered constants."""

    # --- beat side ---------------------------------------------------------- #
    archive_lexicon: frozenset[str] = field(default_factory=lambda: _PT_ARCHIVE)
    science_lexicon: frozenset[str] = field(default_factory=lambda: _PT_SCIENCE)
    tension_lexicon: frozenset[str] = field(default_factory=lambda: _PT_TENSION)
    everyday_lexicon: frozenset[str] = field(default_factory=lambda: _PT_EVERYDAY)
    abstract_lexicon: frozenset[str] = field(default_factory=lambda: _PT_ABSTRACT)
    # A refined query longer than this stops being a query and becomes a
    # sentence: every extra word divides the match ratio of the words that
    # actually mattered. This is the single knob that keeps refinement from
    # turning into dilution.
    max_query_words: int = 7
    # How many words a base query needs before refinement is allowed to add to
    # it. Below this there is no phrase to sharpen, only a noun to bury.
    min_refinable_words: int = 2

    # --- candidate side ----------------------------------------------------- #
    weight_intent_affinity: float = 2.5
    weight_role_affinity: float = 1.5
    weight_visual_strength: float = 1.0
    generic_stock_penalty: float = 3.0
    weak_metaphor_penalty: float = 1.5
    visual_language_repetition_penalty: float = 1.5
    # How many generic-catalogue words it takes before a candidate is filler
    # rather than a photograph that happens to use the word "business".
    generic_stock_reject_hits: int = 2
    # How many CGI words it takes before an image *is* an abstract render.
    abstract_cgi_reject_hits: int = 2
    # A query of at least this many terms that matched on exactly one of them,
    # with nothing shared with the purpose, matched on a keyword, not a meaning.
    keyword_only_min_query_terms: int = 3
    # How many times in the recent window a visual family may repeat before the
    # next candidate of that family is refused outright.
    max_same_family_run: int = 2
    # --- hard veto ---------------------------------------------------------- #
    # Absolute refusals, on by default: coverage is not quality, and a
    # ``needs_editorial_override`` is a better outcome than a cartoon in a
    # serious beat. Turn it off to reproduce a pre-veto run exactly.
    hard_veto: bool = True
    veto_cgi_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_CGI)
    veto_cgi_qualifier_lexicon: frozenset[str] = field(
        default_factory=lambda: _VETO_CGI_QUALIFIER
    )
    veto_cartoon_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_CARTOON)
    veto_cheerful_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_CHEERFUL)
    veto_children_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_CHILDREN)
    child_context_lexicon: frozenset[str] = field(default_factory=lambda: _CHILD_CONTEXT)
    veto_neon_scifi_lexicon: frozenset[str] = field(
        default_factory=lambda: _VETO_NEON_SCIFI
    )
    veto_fantasy_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_FANTASY)
    veto_commercial_lexicon: frozenset[str] = field(
        default_factory=lambda: _VETO_COMMERCIAL
    )
    veto_animal_lexicon: frozenset[str] = field(default_factory=lambda: _VETO_ANIMALS)
    # A scientific beat is the one place a diagram-like render can be the
    # honest picture, so the CGI veto stands down there — and only there.
    cgi_veto_exempt_intents: tuple[str, ...] = ("scientific",)
    # Master switch. False makes every candidate-side component zero and every
    # rejection empty, which is what a plan without relevance labels gets.
    enabled: bool = True

    _LEXICON_FIELDS = (
        "archive_lexicon",
        "science_lexicon",
        "tension_lexicon",
        "everyday_lexicon",
        "abstract_lexicon",
        "veto_cgi_lexicon",
        "veto_cgi_qualifier_lexicon",
        "veto_cartoon_lexicon",
        "veto_cheerful_lexicon",
        "veto_children_lexicon",
        "child_context_lexicon",
        "veto_neon_scifi_lexicon",
        "veto_fantasy_lexicon",
        "veto_commercial_lexicon",
        "veto_animal_lexicon",
    )
    _FLOAT_FIELDS = (
        "weight_intent_affinity",
        "weight_role_affinity",
        "weight_visual_strength",
        "generic_stock_penalty",
        "weak_metaphor_penalty",
        "visual_language_repetition_penalty",
    )
    _INT_FIELDS = (
        "max_query_words",
        "min_refinable_words",
        "generic_stock_reject_hits",
        "abstract_cgi_reject_hits",
        "keyword_only_min_query_terms",
        "max_same_family_run",
    )

    def __post_init__(self) -> None:
        for name in self._LEXICON_FIELDS:
            object.__setattr__(self, name, _check_lexicon(getattr(self, name), name))
        for name in self._FLOAT_FIELDS:
            object.__setattr__(self, name, _non_negative(getattr(self, name), name))
        for name in self._INT_FIELDS:
            _positive_int(getattr(self, name), name)
        if not isinstance(self.enabled, bool):
            raise RelevanceError("enabled must be a boolean")
        if not isinstance(self.hard_veto, bool):
            raise RelevanceError("hard_veto must be a boolean")
        exempt = tuple(self.cgi_veto_exempt_intents)
        unknown = [name for name in exempt if name not in VISUAL_INTENTS]
        if unknown:
            raise RelevanceError(
                f"cgi_veto_exempt_intents has unknown intents: {', '.join(unknown)}"
            )
        object.__setattr__(self, "cgi_veto_exempt_intents", exempt)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            name: getattr(self, name) for name in self._FLOAT_FIELDS
        }
        payload.update({name: getattr(self, name) for name in self._INT_FIELDS})
        payload["enabled"] = self.enabled
        payload["hard_veto"] = self.hard_veto
        payload["cgi_veto_exempt_intents"] = list(self.cgi_veto_exempt_intents)
        return payload

    @classmethod
    def from_dict(cls, data: "Mapping[str, Any]") -> "RelevancePolicy":
        """Merge a partial policy over the defaults; lexicons stay as they are.

        Weights, thresholds and the veto switch are what a project retunes; the
        lexicons are large enough that overriding one wholesale from JSON is a
        way to lose rules by accident rather than a way to configure.
        """

        if not isinstance(data, Mapping):
            raise RelevanceError("policy payload must be an object")
        allowed = set(cls().to_dict())
        unknown = set(data) - allowed
        if unknown:
            raise RelevanceError(
                f"unknown relevance policy fields: {', '.join(sorted(unknown))}"
            )
        merged = cls().to_dict()
        merged.update(data)
        merged["cgi_veto_exempt_intents"] = tuple(merged["cgi_veto_exempt_intents"])
        return cls(**merged)


DEFAULT_RELEVANCE_POLICY = RelevancePolicy()


# --------------------------------------------------------------------------- #
# 1. the reading of a beat
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VisualReading:
    """The relevance reading of one beat: what kind of picture, doing what job,
    asked for how."""

    visual_intent_class: str
    visual_role: str
    refined_query: str
    fallback_queries: tuple[str, ...]
    rationale: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.visual_intent_class not in VISUAL_INTENTS:
            raise RelevanceError(
                f"visual_intent_class must be one of {', '.join(VISUAL_INTENTS)}"
            )
        if self.visual_role not in VISUAL_ROLES:
            raise RelevanceError(f"visual_role must be one of {', '.join(VISUAL_ROLES)}")
        if not isinstance(self.refined_query, str) or not self.refined_query.strip():
            raise RelevanceError("refined_query must be a non-empty string")
        object.__setattr__(self, "refined_query", self.refined_query.strip())
        fallbacks = tuple(self.fallback_queries)
        if any(not isinstance(q, str) or not q.strip() for q in fallbacks):
            raise RelevanceError("fallback_queries must be non-empty strings")
        if len(set(fallbacks)) != len(fallbacks):
            raise RelevanceError("fallback_queries must be unique")
        object.__setattr__(self, "fallback_queries", fallbacks)
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise RelevanceError("rationale must be a non-empty string")

    def queries(self) -> tuple[str, ...]:
        """Every query to try, best first, refined query leading."""

        ordered = [self.refined_query] + [
            q for q in self.fallback_queries if q != self.refined_query
        ]
        return tuple(dict.fromkeys(ordered))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "visual_intent_class": self.visual_intent_class,
            "visual_role": self.visual_role,
            "refined_query": self.refined_query,
            "fallback_queries": list(self.fallback_queries),
            "rationale": self.rationale,
        }


def read_visual_intent(
    narration: str,
    *,
    concept: str = "",
    emotion: str | None = None,
    editorial_role: str | None = None,
    has_concrete_concept: bool = True,
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> tuple[str, str]:
    """Classify what kind of image the beat calls for.

    Returns ``(visual_intent_class, rule_name)``. The rules are checked in a
    fixed order and the first hit wins, so the classification of any beat can
    be replayed by reading the list from the top.
    """

    words = set(_tokens(narration)) | set(_tokens(concept))
    has_number = bool(_NUMERIC.search(narration or ""))

    if words & policy.tension_lexicon:
        return "tension_or_suspense", "tension_cue"
    if words & policy.science_lexicon:
        return "scientific", "science_lexicon"
    if words & policy.archive_lexicon or (has_number and editorial_role == "evidence"):
        return "evidence_or_archive", "archive_or_evidence"
    if emotion:
        return "emotional", "emotion_present"
    if words & policy.everyday_lexicon:
        return "everyday_human", "everyday_lexicon"
    if words & policy.abstract_lexicon or not has_concrete_concept:
        return "metaphorical", "abstract_or_unfilmable"
    return "literal", "default"


def read_visual_role(
    visual_intent_class: str,
    *,
    editorial_role: str | None = None,
    importance: float = 0.5,
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> tuple[str, str]:
    """Classify what the image has to *do*. Returns ``(visual_role, rule_name)``.

    The editorial role (where the beat sits in the argument) outranks the
    visual intent for the two positions that exist to move the viewer — the
    opening and the turn — because there the job of the picture is decided by
    the argument, not by the vocabulary.
    """

    if visual_intent_class not in VISUAL_INTENTS:
        raise RelevanceError(f"unknown visual_intent_class: {visual_intent_class}")

    if editorial_role in ("hook", "open_loop"):
        return "build_tension", "opening_beat"
    if visual_intent_class == "tension_or_suspense":
        return "build_tension", "tension_intent"
    if editorial_role == "contrast" and importance >= 0.5:
        return "shock", "high_importance_contrast"
    if editorial_role == "evidence" or visual_intent_class == "evidence_or_archive":
        return "support_claim", "evidence_beat"
    if visual_intent_class in ("emotional", "everyday_human"):
        return "humanize", "human_intent"
    if visual_intent_class == "metaphorical":
        return "symbolize", "metaphorical_intent"
    if visual_intent_class == "scientific":
        return "explain", "scientific_intent"
    if editorial_role in ("close", "transition"):
        return "contextualize", "closing_beat"
    return "explain", "default"


def refine_query(
    base_query: str,
    *,
    visual_intent_class: str,
    visual_role: str,
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> str:
    """Rewrite an English asset query as *topic + editorial intention*.

    At most one modifier per axis is added, and only while the query stays
    under :attr:`RelevancePolicy.max_query_words`. Both limits exist for the
    same reason: a search engine scores by term overlap, so every word that is
    not carrying meaning is actively lowering the rank of the words that are.
    """

    if visual_intent_class not in VISUAL_INTENTS:
        raise RelevanceError(f"unknown visual_intent_class: {visual_intent_class}")
    if visual_role not in VISUAL_ROLES:
        raise RelevanceError(f"unknown visual_role: {visual_role}")

    words = (base_query or "").split()
    if not words:
        words = _INTENT_FALLBACK[visual_intent_class].split()
    elif len(words) < policy.min_refinable_words:
        # Refinement sharpens a visual phrase; it cannot manufacture one. A
        # base query of a single bare noun — which is what a beat with no
        # filmable concept produces, and in this project's script that noun is
        # usually Portuguese — is returned untouched, so the resolver's
        # sanitisation still sees it for what it is and asks for an editorial
        # override. Prefixing two English modifiers would only have unblocked
        # the search and handed the provider one word it does not understand.
        return " ".join(words)
    present = {_fold(w) for w in words}
    for modifier in (
        _INTENT_MODIFIER[visual_intent_class],
        _ROLE_MODIFIER[visual_role],
    ):
        if not modifier or _fold(modifier) in present:
            continue
        if len(words) + 1 > policy.max_query_words:
            break
        words = [modifier] + words
        present.add(_fold(modifier))
    return " ".join(words)


def read_relevance(
    narration: str,
    *,
    concept: str = "",
    base_queries: Sequence[str] = (),
    emotion: str | None = None,
    editorial_role: str | None = None,
    importance: float = 0.5,
    has_concrete_concept: bool = True,
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> VisualReading:
    """The whole beat-side reading in one call: intent, role, refined query and
    the ordered fallbacks, with the rule names that produced them."""

    intent, intent_rule = read_visual_intent(
        narration,
        concept=concept,
        emotion=emotion,
        editorial_role=editorial_role,
        has_concrete_concept=has_concrete_concept,
        policy=policy,
    )
    role, role_rule = read_visual_role(
        intent, editorial_role=editorial_role, importance=importance, policy=policy
    )
    ordered = [q for q in base_queries if isinstance(q, str) and q.strip()]
    base = ordered[0] if ordered else ""
    refined = refine_query(
        base, visual_intent_class=intent, visual_role=role, policy=policy
    )
    fallbacks = [q for q in ordered if q != refined]
    intent_fallback = _INTENT_FALLBACK[intent]
    if intent_fallback != refined and intent_fallback not in fallbacks:
        fallbacks.append(intent_fallback)
    return VisualReading(
        visual_intent_class=intent,
        visual_role=role,
        refined_query=refined,
        fallback_queries=tuple(fallbacks),
        rationale=f"intent:{intent_rule} role:{role_rule}",
    )


# --------------------------------------------------------------------------- #
# 2. the reading of a candidate
# --------------------------------------------------------------------------- #
def visual_family(bag: "frozenset[str] | set[str]") -> str:
    """The coarse visual family of a candidate, from its metadata bag.

    Checked in a fixed order so a "brain scan in a laboratory" is filed as CGI
    rather than as science: the family exists to catch *repetition of visual
    language*, and the CGI look is what a viewer notices repeating.
    """

    tokens = frozenset(bag)
    for name, lexicon in _FAMILY_ORDER:
        if tokens & lexicon:
            return name
    return "other"


@dataclass(frozen=True, slots=True)
class RelevanceAssessment:
    """The explainable verdict on one candidate for one beat."""

    components: Mapping[str, float]
    rejection_reasons: tuple[str, ...]
    family: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        reasons = tuple(self.rejection_reasons)
        unknown = [r for r in reasons if r not in REJECTION_REASONS]
        if unknown:
            raise RelevanceError(f"unknown rejection reasons: {', '.join(unknown)}")
        object.__setattr__(self, "rejection_reasons", reasons)

    @property
    def total(self) -> float:
        return sum(self.components.values())

    @property
    def rejected(self) -> bool:
        return bool(self.rejection_reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": dict(self.components),
            "rejection_reasons": list(self.rejection_reasons),
            "family": self.family,
        }


def hard_veto_reasons(
    tokens: "frozenset[str]",
    *,
    visual_intent_class: str | None,
    query_terms: Sequence[str] = (),
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> tuple[str, ...]:
    """Absolute refusals: pictures this channel cannot use anywhere.

    Every rule reads the candidate's own metadata and, where the beat could
    legitimately have asked for the thing, the beat's own query. A metaphorical
    beat still gets metaphors — what it stops getting is the nearest abstract
    render the catalogue had lying around.
    """

    if not policy.hard_veto:
        return ()
    asked = {_fold(term) for term in query_terms}
    reasons: list[str] = []

    cgi_hits = (tokens & policy.veto_cgi_lexicon) - asked
    qualifier_hits = tokens & policy.veto_cgi_qualifier_lexicon
    exempt = visual_intent_class in policy.cgi_veto_exempt_intents
    if not exempt and (
        cgi_hits or (len(qualifier_hits) >= 2 and not (asked & qualifier_hits))
    ):
        # either an unmistakable CGI subject, or two independent "abstract 3D
        # render" words the beat never asked for
        reasons.append("hard_veto_cgi")
    if tokens & policy.veto_cartoon_lexicon and not (
        asked & policy.veto_cartoon_lexicon
    ):
        reasons.append("hard_veto_cartoon")
    if tokens & policy.veto_cheerful_lexicon:
        reasons.append("hard_veto_cheerful")
    if (tokens & policy.veto_children_lexicon) and not (
        asked & policy.child_context_lexicon
    ):
        reasons.append("hard_veto_children")
    if tokens & policy.veto_neon_scifi_lexicon:
        reasons.append("hard_veto_neon_scifi")
    if tokens & policy.veto_fantasy_lexicon:
        reasons.append("hard_veto_fantasy")
    if len(tokens & policy.veto_commercial_lexicon) >= 2:
        reasons.append("hard_veto_commercial")
    animals = tokens & policy.veto_animal_lexicon
    if animals and not (asked & policy.veto_animal_lexicon):
        reasons.append("hard_veto_unasked_animal")
    return tuple(reasons)


def assess_candidate(
    bag: "frozenset[str] | set[str]",
    *,
    visual_intent_class: str | None,
    visual_role: str | None,
    emotion: str | None = None,
    query_terms: Sequence[str] = (),
    shared_query_terms: int = 0,
    shared_context_terms: int = 0,
    recent_families: Sequence[str] = (),
    policy: RelevancePolicy = DEFAULT_RELEVANCE_POLICY,
) -> RelevanceAssessment:
    """Score and vet one candidate against one beat's visual intent and role.

    ``bag`` is the candidate's folded metadata tokens. ``shared_query_terms``
    and ``shared_context_terms`` are how many of the requirement's query and
    purpose terms the bag already matched — the caller computes them once for
    its own scoring, and passing them in keeps this function from re-deriving a
    different answer.

    Returns an assessment whose components are additive score contributions
    (positive and negative) and whose ``rejection_reasons`` are *disqualifying*:
    a candidate with any reason must not be selected, however high it scored.
    """

    tokens = frozenset(bag)
    zero = {
        "intent_affinity": 0.0,
        "role_affinity": 0.0,
        "visual_strength": 0.0,
        "generic_stock_penalty": 0.0,
        "weak_metaphor_penalty": 0.0,
        "visual_language_repetition_penalty": 0.0,
    }
    if not policy.enabled or visual_intent_class is None or visual_role is None:
        return RelevanceAssessment(zero, (), visual_family(tokens))
    if visual_intent_class not in VISUAL_INTENTS:
        raise RelevanceError(f"unknown visual_intent_class: {visual_intent_class}")
    if visual_role not in VISUAL_ROLES:
        raise RelevanceError(f"unknown visual_role: {visual_role}")

    components = dict(zero)
    family = visual_family(tokens)

    intent_hits = len(tokens & _INTENT_AFFINITY[visual_intent_class])
    role_hits = len(tokens & _ROLE_AFFINITY[visual_role])
    strength_hits = len(tokens & _VISUAL_STRENGTH)
    generic_hits = len(tokens & _GENERIC_STOCK)
    cgi_hits = len(tokens & _ABSTRACT_CGI)
    cheerful_hits = len(tokens & _CHEERFUL)

    # Affinity saturates fast: two matching words say as much as five, and a
    # linear count would let a keyword-stuffed caption outrank a real match.
    components["intent_affinity"] = policy.weight_intent_affinity * min(
        1.0, intent_hits / 2.0
    )
    components["role_affinity"] = policy.weight_role_affinity * min(
        1.0, role_hits / 2.0
    )
    components["visual_strength"] = policy.weight_visual_strength * min(
        1.0, strength_hits / 2.0
    )
    components["generic_stock_penalty"] = -policy.generic_stock_penalty * min(
        1.0, generic_hits / max(1, policy.generic_stock_reject_hits)
    )

    # A metaphorical beat that found nothing symbolic found a topic, not a
    # metaphor: the picture will read as tangential.
    if visual_intent_class == "metaphorical" and intent_hits == 0:
        components["weak_metaphor_penalty"] = -policy.weak_metaphor_penalty

    repeats = sum(1 for f in recent_families if f == family and f != "other")
    if repeats:
        components["visual_language_repetition_penalty"] = (
            -policy.visual_language_repetition_penalty * repeats
        )

    # --- rejections -------------------------------------------------------- #
    reasons: list[str] = []
    terms = tuple(query_terms)
    if (
        len(terms) >= policy.keyword_only_min_query_terms
        and shared_query_terms == 1
        and shared_context_terms == 0
    ):
        reasons.append("keyword_only_match")
    if generic_hits >= policy.generic_stock_reject_hits:
        reasons.append("generic_stock")
    negative_tone = (
        (emotion or "").strip().lower() in _NEGATIVE_EMOTIONS
        or visual_intent_class == "tension_or_suspense"
        or visual_role in ("shock", "build_tension")
    )
    if negative_tone and cheerful_hits:
        reasons.append("tone_conflict")
    if (
        visual_intent_class in ("emotional", "everyday_human")
        or visual_role == "humanize"
    ) and cgi_hits >= policy.abstract_cgi_reject_hits:
        reasons.append("abstract_cgi_mismatch")
    if repeats >= policy.max_same_family_run:
        reasons.append("visual_language_repetition")
    reasons.extend(
        hard_veto_reasons(
            tokens,
            visual_intent_class=visual_intent_class,
            query_terms=terms,
            policy=policy,
        )
    )

    return RelevanceAssessment(components, tuple(reasons), family)
