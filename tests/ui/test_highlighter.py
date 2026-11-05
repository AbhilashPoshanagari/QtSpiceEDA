from __future__ import annotations

from PySide6.QtGui import QColor, QTextDocument

from bridge.highlighter import COLORS, NetlistHighlighter


def _colors(document: QTextDocument, block_number: int) -> dict[int, str]:
    """Map character index -> highlight colour name for one line."""
    block = document.findBlockByNumber(block_number)
    by_color = {QColor(v).name(): k for k, v in COLORS.items()}
    result: dict[int, str] = {}
    for fmt in block.layout().formats():
        name = by_color.get(fmt.format.foreground().color().name())
        for i in range(fmt.start, fmt.start + fmt.length):
            result[i] = name
    return result


def _document(text: str) -> tuple[QTextDocument, NetlistHighlighter]:
    document = QTextDocument()
    highlighter = NetlistHighlighter(document)
    document.setPlainText(text)
    highlighter.rehighlight()
    return document, highlighter


def test_highlighting(qapp: object) -> None:
    text = (
        "Title 1k\n"  # 0
        "* a comment 10k\n"  # 1
        "R1 in out 4.7k ; note 5\n"  # 2
        ".tran 1u 1m\n"  # 3
        "+ 2MEG\n"  # 4
        "Q1 c b e q2n2222\n"  # 5
    )
    document, _keep = _document(text)

    assert set(_colors(document, 0).values()) == {"title"}
    assert set(_colors(document, 1).values()) == {"comment"}

    line = _colors(document, 2)
    assert [line[i] for i in (0, 1)] == ["element", "element"]
    assert 3 not in line  # node "in" is plain
    assert {line[i] for i in range(10, 14)} == {"number"}
    assert line[15] == "comment" and line[len("R1 in out 4.7k ; note 5") - 1] == "comment"

    card = _colors(document, 3)
    assert {card[i] for i in range(0, 5)} == {"card"}
    assert card[6] == "number" and card[9] == "number"

    cont = _colors(document, 4)
    assert cont[0] == "continuation"
    assert {cont[i] for i in range(2, 6)} == {"number"}

    bjt = _colors(document, 5)
    assert all(i not in bjt for i in range(9, 16))  # model name is not a number
