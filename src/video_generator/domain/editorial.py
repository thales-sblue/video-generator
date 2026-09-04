"""Editorial semantics for a narrated script.

This module answers a question the rhythm planner never asked: *what is this
stretch of narration trying to say, and what should the viewer be looking at
while it is said?*

The planner already decides **when** to cut. Here we decide **what a cut is
about**. A :class:`NarrationBeat` is the semantic reading of one narration
slice: its concept, the entities and emotion in it, the visual intent it
implies, the ordered asset queries most likely to find a picture of that
intent, how important it is, and the editorial role it plays.

Two deliberate choices:

* **Queries are English, intent is Portuguese.** The narration and the
  editorial reading stay in the script's language, because a human reviews
  them. The asset queries do not: every stock library this project can reach
  indexes its material in English, so a Portuguese query is matched against
  English metadata and scores near zero. Translating at the query boundary is
  what makes the difference between "a picture of Einstein" and "a picture of
  an exam".
* **Everything is a deterministic lexical heuristic.** No model, no embedding,
  no network. The lexicons are data on :class:`EditorialPolicy`, so a bad
  mapping is a one-line edit and every decision is inspectable after the fact.

The module is pure: stdlib only, no I/O, and no import of a sibling domain
module. :mod:`video_generator.domain.planning` depends on this one, never the
other way round.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from video_generator.domain.relevance import (
    DEFAULT_RELEVANCE_POLICY,
    VISUAL_INTENTS,
    VISUAL_ROLES,
    RelevancePolicy,
    read_relevance,
)

SCHEMA_VERSION = 1

# What a beat is doing for the viewer. Ordered from "opens" to "closes".
EDITORIAL_ROLES = (
    "hook",
    "open_loop",
    "claim",
    "evidence",
    "contrast",
    "consequence",
    "payoff",
    "transition",
    "close",
)

# On-screen emphasis categories. Deliberately few: each one has to earn a
# distinct visual treatment, and a category nobody can tell apart is noise.
TEXT_EVENT_CATEGORIES = ("emphasis", "keyword", "number", "date", "question")
TEXT_EVENT_ANIMATIONS = ("fade", "pop", "slide", "highlight")
TEXT_EVENT_POSITIONS = ("top", "middle", "lower")


class EditorialError(ValueError):
    """Raised when an editorial contract or policy is invalid."""


# --------------------------------------------------------------------------- #
# small shared helpers (mirrors of the sibling modules' — a domain module must
# not import a sibling's private names)
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[0-9A-Za-zÀ-ÿ]+")
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_DIGITS = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
# Portuguese finite-verb and participle endings. Only ever used to reject a
# capitalised sentence opener as a proper noun: a name is capitalised because
# it is a name, a verb only because a sentence started.
_VERB_SHAPED = re.compile(
    r"(?:ou|ar|er|ir|am|em|ia|iam|ram|sse|ndo|mos|ava|avam)$"
)


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EditorialError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _unit_interval(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditorialError(f"{field_name} must be a number")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise EditorialError(f"{field_name} must lie in [0, 1]")
    return number


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise EditorialError("payload must be an object")
    missing = required - set(data)
    if missing:
        raise EditorialError(f"missing fields: {', '.join(sorted(missing))}")
    unknown = set(data) - required - optional
    if unknown:
        raise EditorialError(f"unknown fields: {', '.join(sorted(unknown))}")


# Closed-class Portuguese words: never a visual concept, never an entity.
_STOPWORDS = frozenset(
    """
    a o e as os um uma uns umas de do da dos das no na nos nas em para por com
    que se ao aos sua seu suas seus meu minha nossa nosso isso isto aquilo
    ele ela eles elas voce voces eu me te lhe mais menos muito pouco pra
    ja nao sim tambem como quando onde porque pois mas ou entao talvez sobre
    ser estar ter haver fazer poder ir vir dar ver ate entre sem foi era sao
    tinha havia esta este esse essa aquele aquela seja fosse ficou vai vao
    aqui ali la hoje ontem agora nunca sempre existe existem guarde quantos
    the of to in on and or for with that as at by is are be this it an was
    """.split()
)

# Discourse markers that classify a beat's editorial role.
_CONTRAST_CUES = frozenset(
    "mas porem contudo entretanto todavia embora apesar so apenas nada".split()
)
_CONSEQUENCE_CUES = frozenset(
    "porque portanto logo assim resultado consequencia porisso entao causa".split()
)
_PAYOFF_CUES = frozenset(
    "depois finalmente enfim resultado descobriu recebeu publicou tornou".split()
)


@dataclass(frozen=True, slots=True)
class ConceptEntry:
    """One lexicon row: a Portuguese concept and the concrete, filmable English
    scene that stands for it, plus the shot type that scene naturally is."""

    visual: str
    shot_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "visual", _text(self.visual, "concept visual"))
        object.__setattr__(
            self, "shot_type", _optional_text(self.shot_type, "concept shot_type")
        )


def _entry(visual: str, shot_type: str | None = None) -> ConceptEntry:
    return ConceptEntry(visual, shot_type)


# The concept lexicon: abstract Portuguese noun -> a thing a camera can point
# at. This is the single highest-leverage table in the module. "reprovação" is
# not filmable; "a failed exam paper" is.
_DEFAULT_CONCEPT_LEXICON: Mapping[str, ConceptEntry] = MappingProxyType(
    {
        # school, exams, admission
        "exame": _entry("student taking a written exam", "b_roll_human"),
        "exames": _entry("students taking a written exam", "b_roll_human"),
        "prova": _entry("exam paper and pencil on a desk", "document"),
        "provas": _entry("exam papers stacked on a desk", "document"),
        "reprovado": _entry("failed exam paper with red marks", "document"),
        "reprovada": _entry("failed exam paper with red marks", "document"),
        "reprovacao": _entry("rejected application letter", "document"),
        "aprovado": _entry("acceptance letter close up", "document"),
        "admissao": _entry("university admission office desk", "environment"),
        "escola": _entry("empty classroom with wooden desks", "environment"),
        "escolar": _entry("empty classroom with wooden desks", "environment"),
        "universidade": _entry("historic university building facade", "establishing"),
        "politecnico": _entry("historic university building facade", "establishing"),
        "faculdade": _entry("university lecture hall", "environment"),
        "estudante": _entry("student studying alone at night", "b_roll_human"),
        "estudantes": _entry("students in a lecture hall", "b_roll_human"),
        "aluno": _entry("student studying alone at night", "b_roll_human"),
        "alunos": _entry("students in a lecture hall", "b_roll_human"),
        "professor": _entry("teacher writing on a blackboard", "b_roll_human"),
        "professores": _entry("teachers in a faculty meeting", "b_roll_human"),
        "nota": _entry("school report card close up", "document"),
        "notas": _entry("school report card close up", "document"),
        "diploma": _entry("graduation diploma close up", "document"),
        "aula": _entry("empty lecture hall", "environment"),
        "aulas": _entry("empty lecture hall", "environment"),
        "matematica": _entry("blackboard covered in equations", "close_detail"),
        "fisica": _entry("blackboard covered in physics equations", "close_detail"),
        "ciencia": _entry("laboratory glassware close up", "object"),
        "botanica": _entry("pressed plant specimens on paper", "object"),
        "zoologia": _entry("antique zoological illustration", "object"),
        "frances": _entry("open french dictionary page", "document"),
        "memorizacao": _entry("hand copying text into a notebook", "close_detail"),
        "experimento": _entry("laboratory experiment close up", "object"),
        "ensaio": _entry("handwritten manuscript page", "document"),
        "artigo": _entry("scientific paper on a desk", "document"),
        "artigos": _entry("stack of scientific papers", "document"),
        "teoria": _entry("handwritten equations on paper", "document"),
        "luz": _entry("beam of light in a dark room", "close_detail"),
        "universo": _entry("night sky full of stars", "establishing"),
        # work and institutions
        "emprego": _entry("empty office desk with a chair", "environment"),
        "trabalho": _entry("person working late at a desk", "b_roll_human"),
        "escritorio": _entry("empty office at night", "environment"),
        "carreira": _entry("long office corridor", "environment"),
        "patente": _entry("old patent document close up", "document"),
        "patentes": _entry("old patent documents stacked", "document"),
        "repartição": _entry("government office counter", "environment"),
        "reparticao": _entry("government office counter", "environment"),
        "vaga": _entry("empty chair in an office", "object"),
        "assistente": _entry("person working at a desk", "b_roll_human"),
        "sistema": _entry("institutional building facade", "establishing"),
        "instituicao": _entry("institutional building facade", "establishing"),
        "governo": _entry("government building facade", "establishing"),
        "regra": _entry("open rule book page", "document"),
        "regras": _entry("open rule book page", "document"),
        "lei": _entry("law book on a desk", "document"),
        "filtro": _entry("light through a narrow slit", "symbolic"),
        "filtros": _entry("light through a narrow slit", "symbolic"),
        "avaliacao": _entry("marking papers with a red pen", "close_detail"),
        "desempenho": _entry("marking papers with a red pen", "close_detail"),
        "diagnostico": _entry("medical chart close up", "document"),
        # mind, thought, belief
        "mente": _entry("person staring out of a window", "b_roll_human"),
        "pensamento": _entry("person thinking alone at a desk", "b_roll_human"),
        "ideia": _entry("hand writing in a notebook", "close_detail"),
        "ideias": _entry("notebook pages covered in writing", "close_detail"),
        "memoria": _entry("old photographs spread on a table", "object"),
        "duvida": _entry("person hesitating in a doorway", "b_roll_human"),
        "verdade": _entry("magnifying glass over a document", "close_detail"),
        "mentira": _entry("crossed out text on paper", "close_detail"),
        "mentem": _entry("crossed out text on paper", "close_detail"),
        "erro": _entry("crossed out handwriting", "close_detail"),
        "razao": _entry("handwritten notes on paper", "document"),
        "razoes": _entry("handwritten notes on paper", "document"),
        "logica": _entry("chalkboard with a diagram", "simple_graphic"),
        "inteligencia": _entry("chess board mid game", "object"),
        "capacidade": _entry("hands assembling a small mechanism", "close_detail"),
        "talento": _entry("hands playing a piano", "close_detail"),
        "genio": _entry("archive portrait photograph", "photography"),
        "historia": _entry("archive documents in a box", "object"),
        "versao": _entry("two newspaper pages side by side", "document"),
        "pergunta": _entry("question mark chalked on a board", "on_screen_text"),
        "perguntas": _entry("question mark chalked on a board", "on_screen_text"),
        "resposta": _entry("hand writing an answer", "close_detail"),
        "escolha": _entry("fork in an empty road", "symbolic"),
        "destino": _entry("empty road at dawn", "symbolic"),
        "regua": _entry("ruler and pencil on paper", "object"),
        # people, society, feeling
        "pessoa": _entry("person alone in a quiet room", "b_roll_human"),
        "pessoas": _entry("crowd walking in a city street", "b_roll_human"),
        "multidao": _entry("dense crowd of people", "b_roll_human"),
        "sociedade": _entry("busy city street from above", "establishing"),
        "cidade": _entry("city skyline at dusk", "establishing"),
        "medo": _entry("person alone in a dark corridor", "b_roll_human"),
        "fracasso": _entry("crumpled paper in a waste bin", "object"),
        "sucesso": _entry("sunrise over a city skyline", "establishing"),
        "solidao": _entry("empty bench in a park", "environment"),
        "silencio": _entry("empty room with soft light", "environment"),
        # time and number
        "tempo": _entry("old clock face close up", "close_detail"),
        "ano": _entry("calendar pages turning", "close_detail"),
        "anos": _entry("calendar pages turning", "close_detail"),
        "decada": _entry("vintage archive photographs", "photography"),
        "seculo": _entry("vintage archive photographs", "photography"),
        "futuro": _entry("empty road stretching ahead", "symbolic"),
        "passado": _entry("dusty archive boxes on a shelf", "object"),
        "data": _entry("date stamped on a document", "close_detail"),
        # media and objects
        "noticia": _entry("newspaper headline close up", "document"),
        "jornal": _entry("newspaper on a wooden table", "document"),
        "livro": _entry("open book pages", "object"),
        "livros": _entry("library shelves full of books", "environment"),
        "documento": _entry("stack of old documents", "document"),
        "documentos": _entry("stack of old documents", "document"),
        "carta": _entry("handwritten letter on a desk", "document"),
        "papel": _entry("sheet of paper on a desk", "object"),
        "mesa": _entry("wooden desk with a lamp", "environment"),
        "janela": _entry("window with morning light", "environment"),
        "porta": _entry("closed wooden door", "object"),
        "sala": _entry("empty room with rows of desks", "environment"),
        "corredor": _entry("long empty corridor", "environment"),
        "predio": _entry("historic stone building facade", "establishing"),
        "trem": _entry("vintage train on a platform", "b_roll_human"),
        "dinheiro": _entry("banknotes and coins close up", "object"),
    }
)

# Emotional colour, folded into the query as an English modifier.
_DEFAULT_EMOTION_LEXICON: Mapping[str, str] = MappingProxyType(
    {
        "reprovado": "rejection",
        "reprovada": "rejection",
        "reprovacao": "rejection",
        "recusado": "rejection",
        "recusada": "rejection",
        "rejeitado": "rejection",
        "negado": "rejection",
        "frustrado": "frustrated",
        "frustracao": "frustrated",
        "fracasso": "defeated",
        "medo": "fearful",
        "duvida": "uncertain",
        "incomoda": "uncomfortable",
        "desconfortavel": "uncomfortable",
        "triste": "sad",
        "sozinho": "lonely",
        "solidao": "lonely",
        "raiva": "angry",
        "surpresa": "surprised",
        "esperanca": "hopeful",
        "orgulho": "proud",
        "vergonha": "ashamed",
        "cansado": "tired",
    }
)


@dataclass(frozen=True, slots=True)
class EditorialPolicy:
    """Editorial reading knobs and lexicons, as data rather than constants."""

    concept_lexicon: Mapping[str, ConceptEntry] = field(
        default_factory=lambda: _DEFAULT_CONCEPT_LEXICON
    )
    emotion_lexicon: Mapping[str, str] = field(
        default_factory=lambda: _DEFAULT_EMOTION_LEXICON
    )
    # Proper nouns pass through untouched unless the operator maps one (for
    # example a place whose English name differs). Empty by default: guessing
    # translations of names is how a query stops meaning anything.
    entity_aliases: Mapping[str, str] = field(default_factory=dict)
    max_queries_per_beat: int = 3
    # A term appearing in more than this fraction of the script's beats is the
    # subject of the whole video, not of this beat: it may still enter a query,
    # but it never leads one. This is what stops "Einstein" from being every
    # query.
    dominant_term_document_ratio: float = 0.25
    # Importance at or above this makes a beat eligible for on-screen emphasis.
    text_event_min_importance: float = 0.6
    # How hard a beat's shot-type hint pulls the planner's weighted draw. Large
    # enough to usually win against the base weights (1-3), small enough that
    # it stays a bias: the planner's run limits still forbid two of a type in a
    # row, so a whole script about documents does not become a wall of paper.
    shot_type_hint_bonus: float = 6.0
    # Semantic Visual Relevance v1, opt-in. When true every beat is also read
    # for *what kind* of picture it wants and *what that picture has to do*,
    # and its leading query is refined accordingly. Off by default so an
    # existing plan keeps producing byte-identical queries.
    visual_relevance: bool = False
    relevance_policy: RelevancePolicy = field(
        default_factory=lambda: DEFAULT_RELEVANCE_POLICY
    )

    def __post_init__(self) -> None:
        concepts = dict(self.concept_lexicon)
        for key, value in concepts.items():
            if not isinstance(key, str) or not key:
                raise EditorialError("concept_lexicon keys must be non-empty strings")
            if not isinstance(value, ConceptEntry):
                raise EditorialError("concept_lexicon values must be ConceptEntry")
        emotions = dict(self.emotion_lexicon)
        for key, value in emotions.items():
            if not isinstance(key, str) or not key:
                raise EditorialError("emotion_lexicon keys must be non-empty strings")
            _text(value, f"emotion_lexicon[{key}]")
        aliases = dict(self.entity_aliases)
        for key, value in aliases.items():
            if not isinstance(key, str) or not key:
                raise EditorialError("entity_aliases keys must be non-empty strings")
            _text(value, f"entity_aliases[{key}]")
        if not isinstance(self.max_queries_per_beat, int) or isinstance(
            self.max_queries_per_beat, bool
        ):
            raise EditorialError("max_queries_per_beat must be an integer")
        if not 1 <= self.max_queries_per_beat <= 6:
            raise EditorialError("max_queries_per_beat must lie in [1, 6]")
        _unit_interval(
            self.dominant_term_document_ratio, "dominant_term_document_ratio"
        )
        _unit_interval(self.text_event_min_importance, "text_event_min_importance")
        bonus = self.shot_type_hint_bonus
        if isinstance(bonus, bool) or not isinstance(bonus, (int, float)):
            raise EditorialError("shot_type_hint_bonus must be a number")
        if bonus < 0.0:
            raise EditorialError("shot_type_hint_bonus must not be negative")
        object.__setattr__(self, "shot_type_hint_bonus", float(bonus))
        if not isinstance(self.visual_relevance, bool):
            raise EditorialError("visual_relevance must be a boolean")
        if not isinstance(self.relevance_policy, RelevancePolicy):
            raise EditorialError("relevance_policy must be a RelevancePolicy")
        object.__setattr__(self, "concept_lexicon", MappingProxyType(concepts))
        object.__setattr__(self, "emotion_lexicon", MappingProxyType(emotions))
        object.__setattr__(self, "entity_aliases", MappingProxyType(aliases))


DEFAULT_EDITORIAL_POLICY = EditorialPolicy()


# --------------------------------------------------------------------------- #
# NarrationBeat
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NarrationBeat:
    """The semantic reading of one narration slice.

    ``beat_id`` is the id of the structure this beat explains — a shot id, in
    practice. A beat owns no timing of its own: the shot plan remains the only
    source of truth for when anything happens.
    """

    beat_id: str
    narration: str
    concept: str
    entities: tuple[str, ...]
    emotion: str | None
    visual_intent: str
    asset_queries: tuple[str, ...]
    importance: float
    editorial_role: str
    shot_type_hint: str | None = None
    # --- Semantic Visual Relevance v1 (optional) --------------------------- #
    # Present only when the editorial policy enables it. ``visual_intent`` above
    # stays what it always was: the Portuguese, human-readable "mostrar: …"
    # line. These three add the machine-readable half — which *kind* of picture
    # the beat wants, what that picture has to *do*, and the query rewritten to
    # ask for both. ``relevance_rationale`` names the rules that fired.
    visual_intent_class: str | None = None
    visual_role: str | None = None
    refined_query: str | None = None
    relevance_rationale: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "beat_id", _text(self.beat_id, "beat_id"))
        object.__setattr__(self, "narration", _text(self.narration, "narration"))
        object.__setattr__(self, "concept", _text(self.concept, "concept"))
        entities = tuple(self.entities)
        if any(not isinstance(e, str) or not e for e in entities):
            raise EditorialError("entities must be non-empty strings")
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "emotion", _optional_text(self.emotion, "emotion"))
        object.__setattr__(
            self, "visual_intent", _text(self.visual_intent, "visual_intent")
        )
        queries = tuple(self.asset_queries)
        if not queries:
            raise EditorialError("asset_queries must contain at least one query")
        if any(not isinstance(q, str) or not q.strip() for q in queries):
            raise EditorialError("asset_queries must be non-empty strings")
        if len(set(queries)) != len(queries):
            raise EditorialError("asset_queries must be unique")
        object.__setattr__(self, "asset_queries", queries)
        object.__setattr__(
            self, "importance", _unit_interval(self.importance, "importance")
        )
        if self.editorial_role not in EDITORIAL_ROLES:
            raise EditorialError(
                f"editorial_role must be one of {', '.join(EDITORIAL_ROLES)}"
            )
        object.__setattr__(
            self, "shot_type_hint", _optional_text(self.shot_type_hint, "shot_type_hint")
        )
        if self.visual_intent_class is not None and (
            self.visual_intent_class not in VISUAL_INTENTS
        ):
            raise EditorialError(
                f"visual_intent_class must be one of {', '.join(VISUAL_INTENTS)}"
            )
        if self.visual_role is not None and self.visual_role not in VISUAL_ROLES:
            raise EditorialError(
                f"visual_role must be one of {', '.join(VISUAL_ROLES)}"
            )
        object.__setattr__(
            self, "refined_query", _optional_text(self.refined_query, "refined_query")
        )
        object.__setattr__(
            self,
            "relevance_rationale",
            _optional_text(self.relevance_rationale, "relevance_rationale"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "beat_id": self.beat_id,
            "narration": self.narration,
            "concept": self.concept,
            "entities": list(self.entities),
            "emotion": self.emotion,
            "visual_intent": self.visual_intent,
            "asset_queries": list(self.asset_queries),
            "importance": self.importance,
            "editorial_role": self.editorial_role,
            "shot_type_hint": self.shot_type_hint,
            "visual_intent_class": self.visual_intent_class,
            "visual_role": self.visual_role,
            "refined_query": self.refined_query,
            "relevance_rationale": self.relevance_rationale,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrationBeat":
        _keys(
            data,
            required={
                "beat_id", "narration", "concept", "entities", "emotion",
                "visual_intent", "asset_queries", "importance", "editorial_role",
            },
            optional={
                "schema_version", "shot_type_hint", "visual_intent_class",
                "visual_role", "refined_query", "relevance_rationale",
            },
        )
        return cls(
            beat_id=data["beat_id"],
            narration=data["narration"],
            concept=data["concept"],
            entities=tuple(data["entities"]),
            emotion=data["emotion"],
            visual_intent=data["visual_intent"],
            asset_queries=tuple(data["asset_queries"]),
            importance=data["importance"],
            editorial_role=data["editorial_role"],
            shot_type_hint=data.get("shot_type_hint"),
            visual_intent_class=data.get("visual_intent_class"),
            visual_role=data.get("visual_role"),
            refined_query=data.get("refined_query"),
            relevance_rationale=data.get("relevance_rationale"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


# --------------------------------------------------------------------------- #
# reading a beat
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    return [_fold(m.group(0)) for m in _WORD.finditer(text or "")]


def _capitalised(text: str) -> "tuple[dict[str, str], set[str], set[str]]":
    """Split a text's capitalised words into mid-sentence ones (a strong proper
    noun signal), sentence-initial ones (capitalised by grammar alone), and the
    set of every word seen in lower case."""

    mid_sentence: dict[str, str] = {}
    sentence_initial: dict[str, str] = {}
    lowercased: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(text or ""):
        words = re.findall(r"[A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ]*", sentence)
        for position, word in enumerate(words):
            folded = _fold(word)
            if not word[:1].isupper():
                lowercased.add(folded)
                continue
            if folded in _STOPWORDS:
                continue
            if position == 0:
                sentence_initial.setdefault(folded, word)
            else:
                mid_sentence.setdefault(folded, word)
    return mid_sentence, set(sentence_initial), lowercased


def _entity_vocabulary(
    texts: Sequence[str], policy: EditorialPolicy
) -> "dict[str, str]":
    """The script's proper nouns, folded -> display form.

    Two signals, because Portuguese capitalises the first word of every
    sentence: a word seen capitalised *mid*-sentence anywhere in the script is
    a name; a word only ever seen sentence-initial is a name only if the script
    never uses it in lower case, and it is not a concept the lexicon knows.
    """

    joined = "\n".join(texts)
    mid_sentence, sentence_initial, lowercased = _capitalised(joined)
    vocabulary: dict[str, str] = dict(mid_sentence)
    for folded in sentence_initial:
        if folded in vocabulary or folded in lowercased:
            continue
        if folded in policy.concept_lexicon or folded in _NUMBER_WORDS:
            continue
        if len(folded) < 4 or _VERB_SHAPED.search(folded):
            # "Estudou." opens a sentence and is capitalised for that reason
            # alone; treating it as a name puts a verb in front of a query
            continue
        vocabulary[folded] = _first_form(joined, folded)
    return {
        folded: policy.entity_aliases.get(folded, form)
        for folded, form in vocabulary.items()
    }


def _first_form(text: str, folded: str) -> str:
    for match in re.finditer(r"[A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ]*", text):
        if _fold(match.group(0)) == folded:
            return match.group(0)
    return folded


def _entities_in(
    text: str, vocabulary: "Mapping[str, str]"
) -> tuple[str, ...]:
    """The script's known proper nouns that appear in this slice, in order."""

    out: list[str] = []
    for match in re.finditer(r"[A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ]*", text or ""):
        display = vocabulary.get(_fold(match.group(0)))
        if display is not None:
            out.append(display)
    return tuple(dict.fromkeys(out))


def _numbers_in(text: str) -> tuple[str, ...]:
    years = _YEAR.findall(text or "")
    numbers = [n for n in _DIGITS.findall(text or "") if n not in years]
    return tuple(dict.fromkeys(list(years) + numbers))


def _role_for(text: str, position: float, has_number: bool) -> str:
    """Where this beat sits in the argument. Position is 0..1 through the script."""

    folded = set(_tokens(text))
    if "?" in text:
        return "open_loop"
    if position < 0.12:
        return "hook"
    if position > 0.92:
        return "close"
    if folded & _CONTRAST_CUES:
        return "contrast"
    if folded & _CONSEQUENCE_CUES:
        return "consequence"
    if folded & _PAYOFF_CUES:
        return "payoff"
    if has_number:
        return "evidence"
    return "claim"


def _importance_for(
    *, role: str, has_number: bool, entities: tuple[str, ...], concept_hit: bool,
    word_count: int,
) -> float:
    """A small additive score in [0, 1]. Deliberately coarse: it ranks beats
    against each other, it does not pretend to measure anything absolute."""

    score = 0.25
    if role in ("hook", "open_loop"):
        score += 0.30
    elif role in ("contrast", "payoff"):
        score += 0.20
    elif role in ("evidence", "consequence"):
        score += 0.15
    if has_number:
        score += 0.20
    if entities:
        score += 0.10
    if concept_hit:
        score += 0.10
    if word_count <= 8:
        score += 0.05  # a short line is usually a deliberate punch
    return min(1.0, round(score, 4))


def _concept_hits(
    tokens: Sequence[str], policy: EditorialPolicy
) -> list[tuple[str, ConceptEntry]]:
    seen: dict[str, ConceptEntry] = {}
    for token in tokens:
        entry = policy.concept_lexicon.get(token)
        if entry is not None:
            seen.setdefault(token, entry)
    return list(seen.items())


def _emotion_hit(tokens: Sequence[str], policy: EditorialPolicy) -> str | None:
    for token in tokens:
        hit = policy.emotion_lexicon.get(token)
        if hit is not None:
            return hit
    return None


def _join(prefix: str, phrase: str) -> str:
    """Prepend ``prefix`` to ``phrase``, dropping words the phrase already has.

    Only words carried over from the prefix are removed. A lexicon phrase is
    never de-duplicated against itself, so ``side by side`` survives intact.
    """

    words = phrase.split()
    present = {_fold(w) for w in words}
    kept = [w for w in prefix.split() if _fold(w) not in present]
    return " ".join(kept + words)


def read_beats(
    slices: Sequence[tuple[str, ...]],
    *,
    policy: EditorialPolicy = DEFAULT_EDITORIAL_POLICY,
) -> tuple[NarrationBeat, ...]:
    """Read every narration slice of one script into a :class:`NarrationBeat`.

    ``slices`` is an ordered sequence of ``(beat_id, narration_text)`` or
    ``(beat_id, narration_text, context_text)``. The whole script is read at
    once on purpose: a term's importance to *this* beat depends on how common
    it is across *all* of them, which is what keeps the video's subject from
    swallowing every query.

    A slice is cut on a duration boundary, not a grammatical one, so it can
    land on a fragment with no concept in it at all. ``context_text`` — the
    surrounding scene — is consulted only in that case, which keeps a fragment
    like "Ele pediu uma exceção" from asking for a picture of "pediu".
    """

    rows: list[tuple[str, str, str]] = []
    for row in slices:
        if len(row) == 2:
            beat_id, text = row
            context = text
        elif len(row) == 3:
            beat_id, text, context = row
        else:
            raise EditorialError("each slice must be a 2- or 3-tuple")
        rows.append((str(beat_id), text or "", context or text or ""))
    if not rows:
        raise EditorialError("read_beats needs at least one slice")

    # document frequency of every concept term, for the dominance rule
    document_count = len(rows)
    frequency: dict[str, int] = {}
    for _, text, _context in rows:
        for token in set(_tokens(text)):
            frequency[token] = frequency.get(token, 0) + 1
    dominant = {
        token
        for token, count in frequency.items()
        if count / document_count > policy.dominant_term_document_ratio
    }
    # proper nouns are a property of the whole script, not of one slice
    vocabulary = _entity_vocabulary([text for _, text, _ in rows], policy)

    beats: list[NarrationBeat] = []
    previous_visual: str | None = None
    previous_query: str | None = None
    for index, (beat_id, text, context) in enumerate(rows):
        position = index / max(1, document_count - 1)
        tokens = _tokens(text)
        numbers = _numbers_in(text)
        entities = _entities_in(text, vocabulary)
        hits = _concept_hits(tokens, policy)
        emotion = _emotion_hit(tokens, policy)

        # A concept term that leads the query should be specific to this beat.
        leading = [(t, e) for t, e in hits if t not in dominant] or hits
        if not leading and context != text:
            # nothing filmable in this fragment; borrow the scene's concept
            context_hits = _concept_hits(_tokens(context), policy)
            leading = [(t, e) for t, e in context_hits if t not in dominant] or context_hits
            emotion = emotion or _emotion_hit(_tokens(context), policy)
        # Consecutive fragments of one scene borrow the same scene concept, and
        # a row of identical queries is a row of identical pictures. Rotate to
        # the next concept the scene offers before repeating the last one.
        if len(leading) > 1 and leading[0][1].visual == previous_visual:
            leading = leading[1:] + leading[:1]
        primary = leading[0] if leading else None
        secondary = leading[1] if len(leading) > 1 else None

        if primary is not None:
            concept = primary[0]
            visual = primary[1].visual
            shot_type_hint = primary[1].shot_type
        else:
            fallback = next(
                (
                    t for t in tokens
                    if len(t) >= 5 and t not in _STOPWORDS and t not in dominant
                ),
                None,
            )
            concept = fallback or (entities[0].lower() if entities else "cena")
            visual = ""
            shot_type_hint = None

        # --- candidate queries, best first ---------------------------------- #
        queries: list[str] = []

        def _add(phrase: str) -> None:
            cleaned = " ".join(phrase.split())
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

        # 1. the concrete scene, coloured by emotion — what the beat is *about*
        if visual:
            _add(_join(emotion, visual) if emotion else visual)
        # 2. the same scene tied to the beat's entity, when there is one
        if visual and entities:
            _add(_join(entities[0], visual))
        elif entities and emotion:
            _add(_join(emotion, entities[0]))
        # 3. a second concept in the beat, or the emotion on its own
        if secondary is not None:
            _add(secondary[1].visual)
        elif emotion and visual:
            _add(f"{emotion} person")
        # 4. last resort: the entity alone, or the raw concept term
        if not queries:
            if entities:
                _add(entities[0])
            _add(concept)

        # last defence against two shots in a row asking for the same picture:
        # when the scene offers only one concept, lead with the variant that
        # carries this beat's own entity instead
        if len(queries) > 1 and queries[0] == previous_query:
            queries = queries[1:] + queries[:1]
        queries = queries[: policy.max_queries_per_beat]
        previous_visual = visual or None
        previous_query = queries[0]

        role = _role_for(text, position, bool(numbers))
        importance = _importance_for(
            role=role,
            has_number=bool(numbers),
            entities=entities,
            concept_hit=primary is not None,
            word_count=len(tokens),
        )
        intent_bits = [visual or concept]
        if entities:
            intent_bits.append(entities[0])
        if emotion:
            intent_bits.append(emotion)

        # --- Semantic Visual Relevance v1 (opt-in) ------------------------- #
        # The queries above already say what the beat is *about*. The reading
        # below says what kind of picture that has to be and what it has to do,
        # and puts the refined query at the head of the list the resolver walks.
        intent_class = visual_role = refined = rationale = None
        if policy.visual_relevance:
            reading = read_relevance(
                text,
                concept=concept,
                base_queries=queries,
                emotion=emotion,
                editorial_role=role,
                importance=importance,
                has_concrete_concept=primary is not None,
                policy=policy.relevance_policy,
            )
            intent_class = reading.visual_intent_class
            visual_role = reading.visual_role
            refined = reading.refined_query
            rationale = reading.rationale
            queries = list(reading.queries())[: policy.max_queries_per_beat]
            previous_query = queries[0]

        beats.append(
            NarrationBeat(
                beat_id=beat_id,
                narration=text.strip() or beat_id,
                concept=concept,
                entities=entities,
                emotion=emotion,
                visual_intent="mostrar: " + " / ".join(intent_bits),
                asset_queries=tuple(queries),
                importance=importance,
                editorial_role=role,
                shot_type_hint=shot_type_hint,
                visual_intent_class=intent_class,
                visual_role=visual_role,
                refined_query=refined,
                relevance_rationale=rationale,
            )
        )
    return tuple(beats)


# --------------------------------------------------------------------------- #
# TextEvent — editorial emphasis, deliberately not a caption
# --------------------------------------------------------------------------- #
# A caption transcribes the voice. A text event argues with it: it lifts one
# number, one date, one turn in the argument onto the screen so the eye catches
# what the ear might not. The two layers are planned separately, styled
# separately and may overlap in time, which is why this is not a caption cue.
_TEXT_EVENT_MAX_CHARS = 32
_MARKUP = "<>{}"


@dataclass(frozen=True, slots=True)
class TextEvent:
    """One piece of on-screen editorial emphasis."""

    event_id: str
    text: str
    start_seconds: float
    end_seconds: float
    category: str
    importance: float
    position: str
    animation: str
    beat_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        text = _text(self.text, "text event text")
        if len(text) > _TEXT_EVENT_MAX_CHARS:
            raise EditorialError(
                f"text event text must be at most {_TEXT_EVENT_MAX_CHARS} characters"
            )
        if any(ord(character) < 32 for character in text):
            raise EditorialError("text event text must not contain control characters")
        if any(character in text for character in _MARKUP):
            # the same rule the caption layer enforces: emphasis text is data,
            # and must never be able to become subtitle or filter-graph syntax
            raise EditorialError("text event text must not contain markup characters")
        object.__setattr__(self, "text", text)
        for name in ("start_seconds", "end_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditorialError(f"{name} must be a number")
            if value < 0:
                raise EditorialError(f"{name} must not be negative")
            object.__setattr__(self, name, float(value))
        if self.end_seconds - self.start_seconds < 0.2:
            raise EditorialError("a text event must last at least 200 ms")
        if self.category not in TEXT_EVENT_CATEGORIES:
            raise EditorialError(
                f"category must be one of {', '.join(TEXT_EVENT_CATEGORIES)}"
            )
        object.__setattr__(
            self, "importance", _unit_interval(self.importance, "importance")
        )
        if self.position not in TEXT_EVENT_POSITIONS:
            raise EditorialError(
                f"position must be one of {', '.join(TEXT_EVENT_POSITIONS)}"
            )
        if self.animation not in TEXT_EVENT_ANIMATIONS:
            raise EditorialError(
                f"animation must be one of {', '.join(TEXT_EVENT_ANIMATIONS)}"
            )
        object.__setattr__(self, "beat_id", _optional_text(self.beat_id, "beat_id"))

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "text": self.text,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "category": self.category,
            "importance": self.importance,
            "position": self.position,
            "animation": self.animation,
            "beat_id": self.beat_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TextEvent":
        _keys(
            data,
            required={
                "event_id", "text", "start_seconds", "end_seconds",
                "category", "importance", "position", "animation",
            },
            optional={"beat_id", "schema_version"},
        )
        return cls(
            event_id=data["event_id"],
            text=data["text"],
            start_seconds=data["start_seconds"],
            end_seconds=data["end_seconds"],
            category=data["category"],
            importance=data["importance"],
            position=data["position"],
            animation=data["animation"],
            beat_id=data.get("beat_id"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class HookPolicy:
    """How the opening of the video is allowed to behave differently.

    The opening earns a policy of its own because the viewer has not decided to
    stay yet. It is a *bias*, not a second planner: the same shots, the same
    rhythm rules, with a tighter ceiling, a lower bar for emphasis and no asset
    reuse. Note that ``max_shot_seconds`` caps the long shots without flooring
    the short ones — an opening cut to a uniform two seconds is monotony, not
    pace.
    """

    hook_seconds: float = 40.0
    max_shot_seconds: float = 4.5
    text_event_min_importance: float = 0.45
    min_gap_seconds: float = 2.5
    forbid_asset_reuse: bool = True

    def __post_init__(self) -> None:
        for name in ("hook_seconds", "max_shot_seconds", "min_gap_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditorialError(f"{name} must be a number")
            if value <= 0:
                raise EditorialError(f"{name} must be greater than zero")
            object.__setattr__(self, name, float(value))
        _unit_interval(self.text_event_min_importance, "text_event_min_importance")
        if not isinstance(self.forbid_asset_reuse, bool):
            raise EditorialError("forbid_asset_reuse must be a boolean")

    def covers(self, start_seconds: float) -> bool:
        return start_seconds < self.hook_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_seconds": self.hook_seconds,
            "max_shot_seconds": self.max_shot_seconds,
            "text_event_min_importance": self.text_event_min_importance,
            "min_gap_seconds": self.min_gap_seconds,
            "forbid_asset_reuse": self.forbid_asset_reuse,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HookPolicy":
        _keys(data, required=set(), optional=set(cls().to_dict()))
        merged = cls().to_dict()
        merged.update(data)
        return cls(**merged)


DEFAULT_HOOK_POLICY = HookPolicy()


# --------------------------------------------------------------------------- #
# planning the emphasis layer
# --------------------------------------------------------------------------- #
_NUMBER_WORDS = frozenset(
    """
    zero dois duas tres quatro cinco seis sete oito nove dez onze doze
    treze quatorze catorze quinze dezesseis dezessete dezoito dezenove vinte
    trinta quarenta cinquenta sessenta setenta oitenta noventa cem cento
    duzentos trezentos mil milhao milhoes bilhao bilhoes primeiro primeira
    terceiro terceira dobro metade
    """.split()
)
_CONTRAST_LEAD = ("mas", "porem", "contudo", "entretanto", "apesar")
_STRIP = ".,;:!?…\"'()"
# Punctuation that ends the thought, and punctuation that merely pauses it.
# An emphasis span may not cross either: "Em 1896, ele se formou" put on screen
# as "1896, ELE SE" is a fragment, not an emphasis.
_CLAUSE_END = ".,;:!?…"
# Words that carry nothing at the end of a line on screen.
_WEAK_TAIL = frozenset(
    """
    estava estavam naquela naquele nessa nesse dele dela deles delas
    daquela daquele daqueles daquelas desta deste dessa desse disso
    """.split()
) | _STOPWORDS


def _shorten(words: Sequence[str], limit: int = _TEXT_EVENT_MAX_CHARS) -> str:
    out: list[str] = []
    for word in words:
        candidate = " ".join(out + [word])
        if len(candidate) > limit:
            break
        out.append(word)
    return " ".join(out)


def _emphasis_span(
    words: Sequence[str],
    start: int,
    limit: int = _TEXT_EVENT_MAX_CHARS,
    max_words: int | None = None,
) -> str:
    """The words from ``start`` that belong on screen together.

    Stops at the first clause boundary, then drops any trailing word that
    carries no meaning on its own. What is left is a phrase a viewer can read
    in one glance, not the first N words that happened to follow.
    """

    out: list[str] = []
    for word in words[start:]:
        bare = word.strip(_STRIP)
        if bare:
            if max_words is not None and len(out) >= max_words:
                break
            candidate = " ".join(out + [bare])
            if len(candidate) > limit:
                break
            out.append(bare)
        if any(character in word for character in _CLAUSE_END):
            break
    while out and _fold(out[-1]) in _WEAK_TAIL:
        out.pop()
    return " ".join(out)


def _emphasis_text(beat: NarrationBeat) -> tuple[str, str] | None:
    """The words to put on screen for this beat, and their category.

    Always a span taken verbatim from the narration — never invented copy, so
    the emphasis can never contradict what is being said.
    """

    words = beat.narration.replace("\n", " ").split()
    if not words:
        return None
    folded = [_fold(w.strip(_STRIP)) for w in words]

    for index, bare in enumerate(folded):
        # A year stands on its own: it is already the strongest thing on the
        # line, and whatever follows it is a sentence, not a caption.
        if _YEAR.fullmatch(bare):
            return bare, "date"
        # A figure needs its unit to mean anything — "vinte e seis" is not the
        # point, "vinte e seis anos depois" is — so it keeps a short span.
        if _DIGITS.fullmatch(bare) or bare in _NUMBER_WORDS:
            span = _emphasis_span(words, index, max_words=4)
            if span:
                return span.upper(), "number"

    if beat.editorial_role == "open_loop" and "?" in beat.narration:
        question = beat.narration.split("?")[0].split(".")[-1].strip()
        span = _shorten(question.split()).strip(_STRIP)
        if span:
            return span.upper(), "question"

    for index, bare in enumerate(folded):
        if bare in _CONTRAST_LEAD:
            span = _shorten(words[index + 1 : index + 5]).strip(_STRIP)
            if span:
                return span.upper(), "emphasis"

    content = [
        w.strip(_STRIP)
        for w, b in zip(words, folded)
        if len(b) >= 4 and b not in _STOPWORDS
    ]
    span = _shorten([w for w in content if w][:3]).strip(_STRIP)
    if span:
        return span.upper(), "keyword"
    return None


_CATEGORY_ANIMATION = MappingProxyType(
    {
        "number": "pop",
        "date": "pop",
        "question": "fade",
        "emphasis": "slide",
        "keyword": "highlight",
    }
)
_CATEGORY_POSITION = MappingProxyType(
    {
        "number": "middle",
        "date": "middle",
        "question": "top",
        "emphasis": "top",
        "keyword": "top",
    }
)


def plan_text_events(
    beats: Sequence[NarrationBeat],
    timings: Mapping[str, tuple[float, float]],
    *,
    policy: EditorialPolicy = DEFAULT_EDITORIAL_POLICY,
    hook_policy: "HookPolicy | None" = DEFAULT_HOOK_POLICY,
    max_events: int | None = None,
) -> tuple[TextEvent, ...]:
    """Choose which beats earn on-screen emphasis, and when it shows.

    ``timings`` maps a ``beat_id`` to the ``(start, end)`` its shot occupies on
    the timeline: this function computes no timing of its own, so the shot plan
    stays the single source of truth for when anything happens.

    Most beats get nothing. An event is emitted only when the beat is important
    enough *and* enough time has passed since the last one — emphasis that
    never stops is wallpaper.
    """

    events: list[TextEvent] = []
    last_end = float("-inf")
    body_gap = max(4.0, policy.text_event_min_importance * 8.0)
    for beat in beats:
        window = timings.get(beat.beat_id)
        if window is None:
            continue
        start, end = float(window[0]), float(window[1])
        if end - start < 0.4:
            continue
        in_hook = hook_policy is not None and hook_policy.covers(start)
        threshold = (
            hook_policy.text_event_min_importance
            if in_hook and hook_policy is not None
            else policy.text_event_min_importance
        )
        gap = hook_policy.min_gap_seconds if in_hook and hook_policy is not None else body_gap
        if beat.importance < threshold:
            continue
        if start - last_end < gap:
            continue
        picked = _emphasis_text(beat)
        if picked is None:
            continue
        text, category = picked
        # hold it for the shot, capped so a long shot does not park a word on
        # screen, and never past the shot's own end
        event_end = min(end, start + min(2.6, max(0.6, (end - start) * 0.9)))
        if event_end - start < 0.4:
            continue
        events.append(
            TextEvent(
                event_id=f"text_{len(events) + 1:03d}",
                text=text,
                start_seconds=round(start, 3),
                end_seconds=round(event_end, 3),
                category=category,
                importance=beat.importance,
                position=_CATEGORY_POSITION[category],
                animation=_CATEGORY_ANIMATION[category],
                beat_id=beat.beat_id,
            )
        )
        last_end = event_end
        if max_events is not None and len(events) >= max_events:
            break
    return tuple(events)


# --------------------------------------------------------------------------- #
# VisualStyle — a reusable brand kit, applied *around* the assets
# --------------------------------------------------------------------------- #
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True, slots=True)
class VisualStyle:
    """The video's visual language: typography, colour and framing.

    Deliberately *not* a look-up table for choosing assets. Identity here is
    what surrounds the picture — type, accent, spacing, a restrained overlay —
    so a shot is never picked because it matches a palette. Semantic relevance
    picks the asset; this dresses it.

    ``vignette_strength`` is the one treatment that touches the picture itself,
    and it is capped low on purpose: consistency is supposed to come from
    composition and typography, not from crushing everyone's photographs into
    the same colour.
    """

    style_id: str = "dark-documentary-v1"
    background: str = "#0B0B0D"
    foreground: str = "#F5F3EE"
    accent: str = "#E5A33C"
    font_name: str = "Sans"
    emphasis_font_scale: float = 1.6
    safe_margin_fraction: float = 0.06
    vignette_strength: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "style_id", _text(self.style_id, "style_id"))
        for name in ("background", "foreground", "accent"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX.match(value):
                raise EditorialError(f"{name} must be a #RRGGBB colour")
            object.__setattr__(self, name, value.upper())
        object.__setattr__(self, "font_name", _text(self.font_name, "font_name"))
        scale = self.emphasis_font_scale
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise EditorialError("emphasis_font_scale must be a number")
        if not 1.0 <= float(scale) <= 3.0:
            raise EditorialError("emphasis_font_scale must lie in [1, 3]")
        object.__setattr__(self, "emphasis_font_scale", float(scale))
        margin = self.safe_margin_fraction
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise EditorialError("safe_margin_fraction must be a number")
        if not 0.0 <= float(margin) <= 0.2:
            raise EditorialError("safe_margin_fraction must lie in [0, 0.2]")
        object.__setattr__(self, "safe_margin_fraction", float(margin))
        strength = _unit_interval(self.vignette_strength, "vignette_strength")
        if strength > 0.35:
            raise EditorialError(
                "vignette_strength is capped at 0.35: identity must not repaint the assets"
            )
        object.__setattr__(self, "vignette_strength", strength)

    def ass_colour(self, name: str) -> str:
        """``&H00BBGGRR`` — the byte order libass expects."""

        value = {
            "background": self.background,
            "foreground": self.foreground,
            "accent": self.accent,
        }[name]
        red, green, blue = value[1:3], value[3:5], value[5:7]
        return f"&H00{blue}{green}{red}".upper()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "style_id": self.style_id,
            "background": self.background,
            "foreground": self.foreground,
            "accent": self.accent,
            "font_name": self.font_name,
            "emphasis_font_scale": self.emphasis_font_scale,
            "safe_margin_fraction": self.safe_margin_fraction,
            "vignette_strength": self.vignette_strength,
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualStyle":
        _keys(data, required=set(), optional=set(cls().to_dict()))
        merged = cls().to_dict()
        merged.pop("schema_version", None)
        merged.update({k: v for k, v in data.items() if k != "schema_version"})
        return cls(**merged)


DARK_DOCUMENTARY_V1 = VisualStyle()
