"""Syntax highlighting for SPICE netlists in a QTextDocument."""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

# Same number syntax as core.netlist.parser.parse_number, but only where a number
# starts a word (so "R1" and "q2n2222" are not highlighted).
_NUMBER_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?(?:meg|[fpnumkgt])?[a-z]*\b", re.I
)
_CARD_RE = re.compile(r"\s*(\.\w+)")
_ELEMENT_RE = re.compile(r"\s*([A-Za-z]\S*)")

COLORS: dict[str, str] = {
    "title": "#6a3d9a",
    "comment": "#6a737d",
    "card": "#005cc5",
    "element": "#b31d28",
    "number": "#1a7f37",
    "continuation": "#e36209",
}


def _format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    fmt.setFontItalic(italic)
    return fmt


class NetlistHighlighter(QSyntaxHighlighter):
    """Highlights the title line, element names, dot-cards, comments and numbers."""

    def __init__(self, document: QTextDocument | None = None) -> None:
        super().__init__(document)
        self._formats = {
            "title": _format(COLORS["title"], bold=True),
            "comment": _format(COLORS["comment"], italic=True),
            "card": _format(COLORS["card"], bold=True),
            "element": _format(COLORS["element"], bold=True),
            "number": _format(COLORS["number"]),
            "continuation": _format(COLORS["continuation"], bold=True),
        }

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if self.currentBlock().blockNumber() == 0:
            self.setFormat(0, len(text), self._formats["title"])
            return
        stripped = text.lstrip()
        if stripped.startswith("*"):
            self.setFormat(0, len(text), self._formats["comment"])
            return

        code = text
        semicolon = text.find(";")
        if semicolon >= 0:
            code = text[:semicolon]
            self.setFormat(semicolon, len(text) - semicolon, self._formats["comment"])

        start = 0
        if stripped.startswith("."):
            match = _CARD_RE.match(code)
            if match:
                self.setFormat(match.start(1), len(match.group(1)), self._formats["card"])
                start = match.end(1)
        elif stripped.startswith("+"):
            plus = code.index("+")
            self.setFormat(plus, 1, self._formats["continuation"])
            start = plus + 1
        else:
            match = _ELEMENT_RE.match(code)
            if match:
                self.setFormat(match.start(1), len(match.group(1)), self._formats["element"])
                start = match.end(1)

        for match in _NUMBER_RE.finditer(code, start):
            self.setFormat(match.start(), match.end() - match.start(), self._formats["number"])
