"""Lay out a screen card: real repository content composed as a full frame.

A *screen card* is the developer-video counterpart of a stock photograph. Where
the dark workflow answers "which picture goes here?" with an asset provider,
this module answers it with material the repository already holds -- a source
line, a terminal transcript, a JSON fragment, a commit list, a comparison, a
number -- and states exactly where each glyph lands on a 1920x1080 canvas.

The module is pure: it turns a :class:`ScreenCard` (what should be on screen)
into a :class:`ScreenLayout` (absolute boxes and text blocks). It never touches
a font file, a renderer or the filesystem, so the same card always lays out the
same way. Rasterising a layout is an adapter's job.

Text measurement is deliberately conservative. A monospace family has a fixed
advance, and for a proportional family the theme carries an upper-bound ratio
for capitalised editorial type. Every laid-out line is checked against the
content width, so a card that would overflow the frame raises instead of
rendering a truncated screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence


SCREEN_KINDS = (
    "code",
    "terminal",
    "json",
    "statement",
    "chain",
    "compare",
    "stat",
    "list",
)
# A card that carries two labelled columns instead of one body.
COLUMN_KINDS = ("compare",)
# Kinds whose body is set in the monospace family: they quote a file, a shell
# session or a document, and alignment is part of the meaning.
MONO_KINDS = ("code", "terminal", "json")
CHAIN_ARROW = "↓"
MAX_BODY_LINES = 24
MAX_COLUMN_LINES = 12
MAX_CHAIN_STEPS = 8
MAX_LINE_CHARACTERS = 200

_CARD_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_COLOUR = re.compile(r"^0x[0-9A-Fa-f]{6}$")
_FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class ScreenCardError(ValueError):
    """A card states something this layer cannot honour."""


class ScreenLayoutError(ScreenCardError):
    """A well-formed card does not fit the frame it was laid out against."""


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ScreenCardError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ScreenCardError(f"{name} must not be empty")
    if len(value) > MAX_LINE_CHARACTERS:
        raise ScreenCardError(f"{name} must be at most {MAX_LINE_CHARACTERS} characters")
    for character in value:
        if character in "\r\n\t":
            raise ScreenCardError(f"{name} must not contain line breaks or tabs")
        if ord(character) < 0x20 or ord(character) == 0x7F:
            raise ScreenCardError(f"{name} must not contain control characters")
    return value


def _lines(value: object, name: str, *, maximum: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ScreenCardError(f"{name} must be a sequence of strings")
    lines = tuple(_text(line, f"{name} line", allow_empty=True) for line in value)
    if len(lines) > maximum:
        raise ScreenCardError(f"{name} accepts at most {maximum} lines")
    return lines


def _colour(value: object, name: str) -> str:
    if not isinstance(value, str) or not _COLOUR.match(value):
        raise ScreenCardError(f"{name} must be a 0xRRGGBB colour literal")
    return value


def _font(value: object, name: str) -> str:
    if not isinstance(value, str) or not _FONT_NAME.match(value):
        raise ScreenCardError(f"{name} must be a plain font family name")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScreenCardError(f"{name} must be a positive integer")
    return value


def _ratio(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScreenCardError(f"{name} must be a number")
    ratio = float(value)
    if not 0.1 <= ratio <= 2.0:
        raise ScreenCardError(f"{name} must be between 0.1 and 2.0")
    return ratio


@dataclass(frozen=True, slots=True)
class ScreenTheme:
    """The channel's screen-card style: one dark ground, one accent, two families."""

    width: int = 1920
    height: int = 1080
    background: str = "0x0E1013"
    panel: str = "0x161A20"
    rule: str = "0x2A313A"
    foreground: str = "0xE8ECEF"
    muted: str = "0x8A949E"
    accent: str = "0xE5A33C"
    mono_font: str = "Consolas"
    display_font: str = "Segoe UI"
    mono_advance_ratio: float = 0.58
    display_advance_ratio: float = 0.64
    margin_x: int = 132
    margin_y: int = 120
    title_size: int = 34
    subtitle_size: int = 28
    body_size: int = 36
    statement_size: int = 104
    stat_size: int = 220
    chain_size: int = 62
    line_gap: int = 20

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            value = _positive_int(getattr(self, name), name)
            if value % 2:
                raise ScreenCardError(f"{name} must be even")
        for name in ("background", "panel", "rule", "foreground", "muted", "accent"):
            _colour(getattr(self, name), name)
        for name in ("mono_font", "display_font"):
            _font(getattr(self, name), name)
        for name in ("mono_advance_ratio", "display_advance_ratio"):
            _ratio(getattr(self, name), name)
        for name in (
            "margin_x", "margin_y", "title_size", "subtitle_size", "body_size",
            "statement_size", "stat_size", "chain_size", "line_gap",
        ):
            _positive_int(getattr(self, name), name)
        if self.margin_x * 2 >= self.width or self.margin_y * 2 >= self.height:
            raise ScreenCardError("margins must leave a content area inside the frame")

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin_x

    def advance(self, font: str) -> float:
        """Upper-bound width of one character in ``font`` at size 1."""

        if font == self.mono_font:
            return self.mono_advance_ratio
        return self.display_advance_ratio

    def measure(self, text: str, font: str, size: int) -> float:
        return len(text) * self.advance(font) * size


@dataclass(frozen=True, slots=True)
class ScreenCard:
    """What one full-frame card says, in the repository's own material."""

    card_id: str
    kind: str
    title: str = ""
    subtitle: str = ""
    body: tuple[str, ...] = ()
    highlight: tuple[int, ...] = ()
    column_titles: tuple[str, ...] = ()
    columns: tuple[tuple[str, ...], ...] = ()
    footer: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.card_id, str) or not _CARD_ID.match(self.card_id):
            raise ScreenCardError(
                "card_id must be lower-case letters, digits, '-' or '_'"
            )
        if self.kind not in SCREEN_KINDS:
            raise ScreenCardError("kind must be one of " + ", ".join(SCREEN_KINDS))
        object.__setattr__(self, "title", _text(self.title, "title", allow_empty=True))
        object.__setattr__(
            self, "subtitle", _text(self.subtitle, "subtitle", allow_empty=True)
        )
        object.__setattr__(self, "footer", _text(self.footer, "footer", allow_empty=True))
        object.__setattr__(self, "body", _lines(self.body, "body", maximum=MAX_BODY_LINES))

        if self.kind in COLUMN_KINDS:
            if self.body:
                raise ScreenCardError("a compare card carries columns, not a body")
            if (
                isinstance(self.columns, (str, bytes))
                or not isinstance(self.columns, Sequence)
                or len(self.columns) != 2
            ):
                raise ScreenCardError("a compare card requires exactly two columns")
            object.__setattr__(
                self,
                "columns",
                tuple(
                    _lines(column, "column", maximum=MAX_COLUMN_LINES)
                    for column in self.columns
                ),
            )
            titles = _lines(self.column_titles, "column_titles", maximum=2)
            if len(titles) != 2 or not all(title.strip() for title in titles):
                raise ScreenCardError("a compare card requires two column titles")
            object.__setattr__(self, "column_titles", titles)
            if not any(self.columns):
                raise ScreenCardError("a compare card requires at least one column line")
        else:
            if self.columns or self.column_titles:
                raise ScreenCardError(f"a {self.kind} card must not carry columns")
            object.__setattr__(self, "columns", ())
            object.__setattr__(self, "column_titles", ())

        if self.kind == "stat":
            if not self.title.strip():
                raise ScreenCardError("a stat card requires the figure in its title")
        elif self.kind == "statement":
            if not self.body:
                raise ScreenCardError("a statement card requires at least one line")
        elif self.kind == "chain":
            if not 2 <= len(self.body) <= MAX_CHAIN_STEPS:
                raise ScreenCardError(
                    f"a chain card requires 2 to {MAX_CHAIN_STEPS} steps"
                )
            if any(not step.strip() for step in self.body):
                raise ScreenCardError("a chain step must not be blank")
        elif self.kind in MONO_KINDS or self.kind == "list":
            if not self.body:
                raise ScreenCardError(f"a {self.kind} card requires a body")

        if isinstance(self.highlight, (str, bytes)) or not isinstance(
            self.highlight, Sequence
        ):
            raise ScreenCardError("highlight must be a sequence of body indices")
        indices = []
        for index in self.highlight:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ScreenCardError("highlight indices must be integers")
            if not 0 <= index < len(self.body):
                raise ScreenCardError(
                    f"highlight index {index} is outside the card body"
                )
            indices.append(index)
        object.__setattr__(self, "highlight", tuple(sorted(set(indices))))

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "kind": self.kind,
            "title": self.title,
            "subtitle": self.subtitle,
            "body": list(self.body),
            "highlight": list(self.highlight),
            "column_titles": list(self.column_titles),
            "columns": [list(column) for column in self.columns],
            "footer": self.footer,
        }


def card_from_mapping(payload: Mapping[str, object]) -> ScreenCard:
    """Build a card from a decoded JSON object, rejecting unknown keys."""

    if not isinstance(payload, Mapping):
        raise ScreenCardError("a screen card must be a JSON object")
    known = {
        "card_id", "kind", "title", "subtitle", "body", "highlight",
        "column_titles", "columns", "footer",
    }
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ScreenCardError("unknown screen card keys: " + ", ".join(unknown))
    if "card_id" not in payload or "kind" not in payload:
        raise ScreenCardError("a screen card requires card_id and kind")
    columns = payload.get("columns", ())
    if not isinstance(columns, (str, bytes)) and isinstance(columns, Sequence):
        columns = tuple(
            tuple(column) if not isinstance(column, (str, bytes)) and isinstance(column, Sequence)
            else column
            for column in columns
        )
    return ScreenCard(
        card_id=payload["card_id"],  # type: ignore[arg-type]
        kind=payload["kind"],  # type: ignore[arg-type]
        title=payload.get("title", ""),  # type: ignore[arg-type]
        subtitle=payload.get("subtitle", ""),  # type: ignore[arg-type]
        body=tuple(payload.get("body", ())),  # type: ignore[arg-type]
        highlight=tuple(payload.get("highlight", ())),  # type: ignore[arg-type]
        column_titles=tuple(payload.get("column_titles", ())),  # type: ignore[arg-type]
        columns=columns,  # type: ignore[arg-type]
        footer=payload.get("footer", ""),  # type: ignore[arg-type]
    )


@dataclass(frozen=True, slots=True)
class ScreenBoxBlock:
    """A filled rectangle in frame coordinates."""

    x: int
    y: int
    width: int
    height: int
    colour: str

    def to_dict(self) -> dict[str, object]:
        return {
            "x": self.x, "y": self.y, "width": self.width,
            "height": self.height, "colour": self.colour,
        }


@dataclass(frozen=True, slots=True)
class ScreenTextBlock:
    """One run of text, already positioned. ``lines`` share a left edge."""

    lines: tuple[str, ...]
    font: str
    size: int
    colour: str
    x: int
    y: int
    line_spacing: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": list(self.lines), "font": self.font, "size": self.size,
            "colour": self.colour, "x": self.x, "y": self.y,
            "line_spacing": self.line_spacing,
        }


@dataclass(frozen=True, slots=True)
class ScreenLayout:
    """A card resolved to absolute geometry against one theme."""

    card_id: str
    kind: str
    width: int
    height: int
    background: str
    boxes: tuple[ScreenBoxBlock, ...] = field(default_factory=tuple)
    texts: tuple[ScreenTextBlock, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "background": self.background,
            "boxes": [box.to_dict() for box in self.boxes],
            "texts": [text.to_dict() for text in self.texts],
        }


def _line_height(size: int, theme: ScreenTheme) -> int:
    return size + theme.line_gap


def _check_width(
    lines: Sequence[str], font: str, size: int, x: int, theme: ScreenTheme, card_id: str
) -> None:
    limit = theme.width - theme.margin_x
    for line in lines:
        if x + theme.measure(line, font, size) > limit:
            raise ScreenLayoutError(
                f"card {card_id}: line does not fit the frame at size {size}: {line!r}"
            )


def _fit_size(
    lines: Sequence[str], font: str, size: int, available: int, theme: ScreenTheme
) -> int:
    """Largest size at or below ``size`` where every line fits ``available``."""

    widest = max((len(line) for line in lines), default=0)
    if widest == 0:
        return size
    ceiling = int(available / (widest * theme.advance(font)))
    return max(min(size, ceiling), 12)


def _header(
    card: ScreenCard, theme: ScreenTheme
) -> tuple[list[ScreenBoxBlock], list[ScreenTextBlock], int]:
    """Title chip and subtitle. Returns the y where the body may start."""

    boxes: list[ScreenBoxBlock] = []
    texts: list[ScreenTextBlock] = []
    y = theme.margin_y
    if card.title.strip() and card.kind not in ("stat", "statement"):
        font = theme.mono_font if card.kind in MONO_KINDS else theme.display_font
        size = _fit_size([card.title], font, theme.title_size, theme.content_width, theme)
        _check_width([card.title], font, size, theme.margin_x, theme, card.card_id)
        texts.append(
            ScreenTextBlock((card.title,), font, size, theme.accent, theme.margin_x, y)
        )
        y += _line_height(size, theme)
    if card.subtitle.strip():
        size = _fit_size(
            [card.subtitle], theme.display_font, theme.subtitle_size,
            theme.content_width, theme,
        )
        _check_width(
            [card.subtitle], theme.display_font, size, theme.margin_x, theme, card.card_id
        )
        texts.append(
            ScreenTextBlock(
                (card.subtitle,), theme.display_font, size, theme.muted, theme.margin_x, y
            )
        )
        y += _line_height(size, theme)
    if texts:
        y += theme.line_gap
        boxes.append(ScreenBoxBlock(theme.margin_x, y, theme.content_width, 2, theme.rule))
        y += theme.line_gap * 2
    return boxes, texts, y


def _footer(card: ScreenCard, theme: ScreenTheme) -> list[ScreenTextBlock]:
    if not card.footer.strip():
        return []
    size = _fit_size(
        [card.footer], theme.display_font, theme.subtitle_size, theme.content_width, theme
    )
    _check_width(
        [card.footer], theme.display_font, size, theme.margin_x, theme, card.card_id
    )
    return [
        ScreenTextBlock(
            (card.footer,), theme.display_font, size, theme.muted,
            theme.margin_x, theme.height - theme.margin_y,
        )
    ]


def _mono_body(
    card: ScreenCard, theme: ScreenTheme, top: int
) -> tuple[list[ScreenBoxBlock], list[ScreenTextBlock]]:
    """Code, terminal and JSON: one monospace block, highlighted lines banded."""

    boxes: list[ScreenBoxBlock] = []
    texts: list[ScreenTextBlock] = []
    gutter = 14
    text_x = theme.margin_x + gutter + 12
    available = theme.width - theme.margin_x - text_x
    size = _fit_size(card.body, theme.mono_font, theme.body_size, available, theme)
    step = _line_height(size, theme)
    _check_width(card.body, theme.mono_font, size, text_x, theme, card.card_id)
    if top + step * len(card.body) > theme.height - theme.margin_y:
        raise ScreenLayoutError(
            f"card {card.card_id}: {len(card.body)} body lines do not fit the frame"
        )
    for index in card.highlight:
        boxes.append(
            ScreenBoxBlock(
                theme.margin_x, top + index * step - theme.line_gap // 2,
                theme.content_width, step, theme.panel,
            )
        )
        boxes.append(
            ScreenBoxBlock(
                theme.margin_x, top + index * step - theme.line_gap // 2,
                gutter // 2 + 2, step, theme.accent,
            )
        )
    highlighted = set(card.highlight)
    for index, line in enumerate(card.body):
        # With nothing singled out the whole quote reads at full contrast; once a
        # line is highlighted the rest steps back so the eye lands on it.
        colour = (
            theme.foreground
            if not highlighted or index in highlighted
            else theme.muted
        )
        texts.append(
            ScreenTextBlock((line,), theme.mono_font, size, colour, text_x, top + index * step)
        )
    return boxes, texts


def _list_body(card: ScreenCard, theme: ScreenTheme, top: int) -> list[ScreenTextBlock]:
    size = _fit_size(card.body, theme.display_font, theme.body_size, theme.content_width, theme)
    step = _line_height(size, theme) + theme.line_gap // 2
    _check_width(card.body, theme.display_font, size, theme.margin_x, theme, card.card_id)
    if top + step * len(card.body) > theme.height - theme.margin_y:
        raise ScreenLayoutError(
            f"card {card.card_id}: {len(card.body)} list lines do not fit the frame"
        )
    highlighted = set(card.highlight)
    return [
        ScreenTextBlock(
            (line,), theme.display_font, size,
            theme.accent if index in highlighted else theme.foreground,
            theme.margin_x, top + index * step,
        )
        for index, line in enumerate(card.body)
    ]


def _statement_body(card: ScreenCard, theme: ScreenTheme) -> list[ScreenTextBlock]:
    size = _fit_size(
        card.body, theme.display_font, theme.statement_size, theme.content_width, theme
    )
    step = _line_height(size, theme)
    _check_width(card.body, theme.display_font, size, theme.margin_x, theme, card.card_id)
    block_height = step * len(card.body)
    if block_height > theme.height - 2 * theme.margin_y:
        raise ScreenLayoutError(f"card {card.card_id}: statement does not fit the frame")
    top = (theme.height - block_height) // 2
    highlighted = set(card.highlight)
    return [
        ScreenTextBlock(
            (line,), theme.display_font, size,
            theme.accent if index in highlighted else theme.foreground,
            theme.margin_x, top + index * step,
        )
        for index, line in enumerate(card.body)
    ]


def _chain_body(card: ScreenCard, theme: ScreenTheme, top: int) -> list[ScreenTextBlock]:
    # A chain is as tall as it is long. Shrink the step type until the whole
    # progression sits between the header and the bottom margin, so a two-step
    # and a seven-step chain are both legible without hand-tuning the theme.
    floor = theme.height - theme.margin_y // 2
    size = _fit_size(
        card.body, theme.display_font, theme.chain_size, theme.content_width, theme
    )
    while size >= 18:
        arrow_size = max(size * 3 // 5, 16)
        step = _line_height(size, theme) + _line_height(arrow_size, theme)
        block_height = step * len(card.body) - _line_height(arrow_size, theme)
        available_top = max(top, (theme.height - block_height) // 2)
        if available_top + block_height <= floor:
            break
        size -= 2
    else:
        raise ScreenLayoutError(f"card {card.card_id}: chain does not fit the frame")
    _check_width(card.body, theme.display_font, size, theme.margin_x, theme, card.card_id)
    highlighted = set(card.highlight)
    texts: list[ScreenTextBlock] = []
    for index, step_text in enumerate(card.body):
        y = available_top + index * step
        texts.append(
            ScreenTextBlock(
                (step_text,), theme.display_font, size,
                theme.accent if index in highlighted else theme.foreground,
                theme.margin_x, y,
            )
        )
        if index < len(card.body) - 1:
            texts.append(
                ScreenTextBlock(
                    (CHAIN_ARROW,), theme.display_font, arrow_size, theme.muted,
                    theme.margin_x, y + _line_height(size, theme),
                )
            )
    return texts


def _compare_body(
    card: ScreenCard, theme: ScreenTheme, top: int
) -> tuple[list[ScreenBoxBlock], list[ScreenTextBlock]]:
    gap = 96
    column_width = (theme.content_width - gap) // 2
    lefts = (theme.margin_x, theme.margin_x + column_width + gap)
    boxes: list[ScreenBoxBlock] = [
        ScreenBoxBlock(
            theme.margin_x + column_width + gap // 2, top,
            2, theme.height - top - theme.margin_y, theme.rule,
        )
    ]
    texts: list[ScreenTextBlock] = []
    all_lines = [line for column in card.columns for line in column]
    body_size = _fit_size(all_lines, theme.display_font, theme.body_size, column_width, theme)
    title_size = _fit_size(
        list(card.column_titles), theme.display_font, theme.subtitle_size,
        column_width, theme,
    )
    step = _line_height(body_size, theme) + theme.line_gap // 2
    tallest = max(len(column) for column in card.columns)
    body_top = top + _line_height(title_size, theme) + theme.line_gap
    if body_top + step * tallest > theme.height - theme.margin_y:
        raise ScreenLayoutError(f"card {card.card_id}: comparison does not fit the frame")
    for column_index, (title, column) in enumerate(zip(card.column_titles, card.columns)):
        x = lefts[column_index]
        _check_width([title], theme.display_font, title_size, x, theme, card.card_id)
        _check_width(column, theme.display_font, body_size, x, theme, card.card_id)
        texts.append(
            ScreenTextBlock(
                (title,), theme.display_font, title_size,
                theme.accent if column_index else theme.muted, x, top,
            )
        )
        for line_index, line in enumerate(column):
            texts.append(
                ScreenTextBlock(
                    (line,), theme.display_font, body_size, theme.foreground,
                    x, body_top + line_index * step,
                )
            )
    return boxes, texts


def _stat_body(card: ScreenCard, theme: ScreenTheme) -> list[ScreenTextBlock]:
    figure_size = _fit_size(
        [card.title], theme.display_font, theme.stat_size, theme.content_width, theme
    )
    label_size = theme.body_size
    support = list(card.body)
    _check_width([card.title], theme.display_font, figure_size, theme.margin_x, theme, card.card_id)
    _check_width(support, theme.display_font, label_size, theme.margin_x, theme, card.card_id)
    step = _line_height(label_size, theme)
    block_height = _line_height(figure_size, theme) + step * len(support)
    if block_height > theme.height - 2 * theme.margin_y:
        raise ScreenLayoutError(f"card {card.card_id}: stat does not fit the frame")
    top = (theme.height - block_height) // 2
    texts = [
        ScreenTextBlock((card.title,), theme.display_font, figure_size, theme.accent, theme.margin_x, top)
    ]
    y = top + _line_height(figure_size, theme)
    for line in support:
        texts.append(
            ScreenTextBlock((line,), theme.display_font, label_size, theme.foreground, theme.margin_x, y)
        )
        y += step
    return texts


def layout_card(card: ScreenCard, theme: ScreenTheme | None = None) -> ScreenLayout:
    """Resolve ``card`` to absolute geometry, or refuse if it overflows the frame."""

    if not isinstance(card, ScreenCard):
        raise ScreenCardError("card must be a ScreenCard")
    style = theme if theme is not None else ScreenTheme()
    if not isinstance(style, ScreenTheme):
        raise ScreenCardError("theme must be a ScreenTheme")

    boxes, texts, top = _header(card, style)
    if card.kind in MONO_KINDS:
        body_boxes, body_texts = _mono_body(card, style, top)
        boxes.extend(body_boxes)
        texts.extend(body_texts)
    elif card.kind == "list":
        texts.extend(_list_body(card, style, top))
    elif card.kind == "statement":
        texts.extend(_statement_body(card, style))
    elif card.kind == "chain":
        texts.extend(_chain_body(card, style, top))
    elif card.kind == "compare":
        body_boxes, body_texts = _compare_body(card, style, top)
        boxes.extend(body_boxes)
        texts.extend(body_texts)
    elif card.kind == "stat":
        texts.extend(_stat_body(card, style))
    else:  # pragma: no cover - SCREEN_KINDS and the branches above stay in step
        raise ScreenCardError(f"no layout for kind {card.kind}")
    texts.extend(_footer(card, style))

    return ScreenLayout(
        card_id=card.card_id,
        kind=card.kind,
        width=style.width,
        height=style.height,
        background=style.background,
        boxes=tuple(boxes),
        texts=tuple(texts),
    )


__all__ = [
    "ScreenBoxBlock",
    "CHAIN_ARROW",
    "COLUMN_KINDS",
    "MONO_KINDS",
    "SCREEN_KINDS",
    "ScreenCard",
    "ScreenCardError",
    "ScreenLayout",
    "ScreenLayoutError",
    "ScreenTheme",
    "ScreenTextBlock",
    "card_from_mapping",
    "layout_card",
]
