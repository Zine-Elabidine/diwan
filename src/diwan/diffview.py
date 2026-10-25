"""The diff of a `write` or `edit`, drawn the way the chat shows it: a numbered gutter, then
the code with syntax colours, removed lines tinted red and added lines green, the words that
changed tinted stronger. All colours come from the current theme, so the diff follows a theme
change. Long lines wrap inside the code column, keeping the gutter clear."""

from __future__ import annotations

from pathlib import PurePath

from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import Comment, Keyword, Name, Number, String
from pygments.util import ClassNotFound
from rich.text import Text as RText
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from .diff import Change, Row, rows
from .theme import mix


def lexer_for(path: str) -> Lexer:
    try:
        return get_lexer_for_filename(PurePath(path).name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return TextLexer()


class _Themed(Widget):
    """Redraws when the app's theme changes (the colours are computed in render)."""

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._theme_changed, init=False)

    def _theme_changed(self, _old: str, _new: str) -> None:
        self.refresh(layout=True)


class Gutter(_Themed):
    DEFAULT_CSS = "Gutter { width: 7; height: auto; }"

    def __init__(self, row: Row):
        super().__init__()
        self.row = row

    def render(self) -> RText:
        th = self.app.current_theme
        n = self.row.new if self.row.new is not None else self.row.old
        t = RText(f"{n:>4} " if n is not None else "     ", style=mix(th.foreground, th.background, 0.5))
        hot = {"add": th.success, "del": th.error}.get(self.row.kind)
        t.append({"add": "+", "del": "−"}.get(self.row.kind, " "), style=f"bold {hot}" if hot else "")
        return t


class Code(_Themed):
    DEFAULT_CSS = "Code { width: 1fr; height: auto; }"

    def __init__(self, row: Row, lexer: Lexer):
        super().__init__()
        self.row, self.lexer = row, lexer

    def render(self) -> RText:
        th = self.app.current_theme
        fg, bg = th.foreground or "#ffffff", th.background or "#000000"
        if self.row.kind == "gap":
            return RText(self.row.text or "⋯", style=mix(fg, bg, 0.5))
        hot = {"add": th.success, "del": th.error}.get(self.row.kind)
        word_bg = mix(bg, hot, 0.55) if hot else None
        line = self.row.text
        changed: list[bool] = []
        for piece, ch in self.row.spans:
            changed += [ch] * len(piece)
        t, pos = RText(), 0
        for kind, token in self.lexer.get_tokens(line):
            value = token[: max(0, len(line) - pos)]   # lexers may add a final newline
            color, italic = _color(kind, th, fg, bg)
            for k, c in enumerate(value):
                on = f" on {word_bg}" if word_bg and pos + k < len(changed) and changed[pos + k] else ""
                t.append(c, style=f"{color}{' italic' if italic else ''}{on}")
            pos += len(value)
        return t


def _color(kind, th, fg: str, bg: str) -> tuple[str, bool]:
    if kind in Comment:
        return mix(fg, bg, 0.45), True
    if kind in Keyword:
        return th.primary, False
    if kind in Name.Builtin or kind in Name.Function or kind in Name.Class:
        return th.secondary or th.primary, False
    if kind in String:
        return th.warning or fg, False
    if kind in Number:
        return th.accent or fg, False
    return fg, False


class DiffLine(Horizontal):
    DEFAULT_CSS = """
    DiffLine { height: auto; }
    DiffLine.add { background: $success 24%; }
    DiffLine.del { background: $error 24%; }
    """

    def __init__(self, row: Row, lexer: Lexer):
        super().__init__(classes=row.kind)
        self.row, self.lexer = row, lexer

    def compose(self) -> ComposeResult:
        yield Gutter(self.row)
        yield Code(self.row, self.lexer)


class DiffView(Vertical):
    """A whole diff: added and removed counts (and "new file"), then the rows."""

    DEFAULT_CSS = """
    DiffView { height: auto; padding: 0 0 0 2; }
    DiffView > .counts { height: 1; color: $text-muted; }
    """

    def __init__(self, ch: Change, **kw):
        super().__init__(**kw)
        self.change = ch
        self.rows, self.adds, self.dels = rows(ch.before, ch.after)

    def compose(self) -> ComposeResult:
        note = "  new file" if self.change.created else ""
        yield Static(f"⎿ [$success]+{self.adds}[/] [$error]−{self.dels}[/]{note}", classes="counts")
        lexer = lexer_for(self.change.path)
        for row in self.rows:
            yield DiffLine(row, lexer)
