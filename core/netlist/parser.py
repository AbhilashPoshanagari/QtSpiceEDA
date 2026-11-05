"""SPICE netlist parser. Qt-free.

``parse(text, base_dir)`` never raises on bad input: every problem becomes a
:class:`Diagnostic` in the returned :class:`ParseResult`.

Supported: the title line, ``*`` comments, ``;`` and ``$`` inline comments, ``+``
continuation lines, elements R C L V I D Q M E F G H X, and the cards ``.model``,
``.subckt``/``.ends``, ``.include``, ``.lib``, ``.param`` (also inside ``.subckt``),
``.op``, ``.dc``, ``.ac``, ``.tran`` and ``.end``.

Libraries: ``.lib file`` is the include form (as in LTspice). ``.lib file section``
reads only the ``.lib section`` ... ``.endl`` block of that file. Inside an included
file a one-word ``.lib name`` followed by ``.endl`` is a section header; such sections
are skipped (with a warning) when the file is included without naming a section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.libindex import encrypted_block_line
from core.netlist.model import (
    ERROR,
    WARNING,
    Analysis,
    Circuit,
    Diagnostic,
    Element,
    Model,
    ParseResult,
    Subcircuit,
)

# ---------------------------------------------------------------- numbers

_NUMBER_RE = re.compile(
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[fpnumkgt])?([a-z]*)", re.I
)
_SCALE = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "mil": 25.4e-6,
    "g": 1e9,
    "t": 1e12,
}


def parse_number(text: str) -> float | None:
    """Parse a SPICE number such as ``4.7k``, ``10uF``, ``1MEG`` or ``-2.5e-3``.

    Suffixes (case-insensitive): f p n u m k meg g t mil, where ``m`` is milli, ``meg``
    mega and ``mil`` a thousandth of an inch (25.4u). As in ngspice, letters after the
    number and suffix are ignored (``10uF``, ``1kohm``). Returns None when the text does
    not start with a valid number.
    """
    match = _NUMBER_RE.fullmatch(text.strip())
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value * _SCALE.get((match.group(2) or "").lower(), 1.0)


# ---------------------------------------------------------------- tokens and lines

# A brace expression or quoted string is one token; "=" and parentheses are their own
# tokens; commas and whitespace separate tokens.
_TOKEN_RE = re.compile(r"\{[^}]*\}?|'[^']*'?|[=()]|[^\s=(),{}']+")

ELEMENT_KINDS = "RCLVIDQMEFGHX"
# Valid SPICE element letters that v0.1 does not handle: warned about and skipped.
_UNSUPPORTED_KINDS = "ABJKNOPSTUWYZ"
_SOURCE_WORDS = frozenset(
    {"dc", "ac", "sin", "pulse", "pwl", "exp", "sffm", "am", "distof1", "distof2", "(", ")"}
)
_MAX_INCLUDE_DEPTH = 16
# As in ngspice, "$" starts an inline comment at the start of a line or after a space.
_DOLLAR_COMMENT_RE = re.compile(r"(?:^|(?<=\s))\$")


@dataclass(frozen=True)
class _Token:
    text: str
    line: int
    col: int


@dataclass(frozen=True)
class _Site:
    """Where an included line is reported: the top-level ``.include`` line."""

    line: int
    col: int
    file: str


# An included file and the library section read from it (None = the whole file).
_Key = tuple[Path, str | None]


@dataclass
class _Line:
    """One logical line: a card or element with its continuation lines joined."""

    tokens: list[_Token]
    site: _Site | None = None

    @property
    def keyword(self) -> str:
        return self.tokens[0].text.casefold()


@dataclass
class _Scope:
    elements: list[Element]
    models: list[Model]


@dataclass
class _State:
    circuit: Circuit = field(default_factory=Circuit)
    subckt: Subcircuit | None = None
    in_control: bool = False
    ended: bool = False


def comment_start(text: str) -> int:
    """Index where an inline comment starts in ``text``, or -1 if there is none.

    ``;`` starts a comment anywhere; ``$`` at the start of a line or after a space.
    """
    starts = [i for i in (text.find(";"),) if i >= 0]
    dollar = _DOLLAR_COMMENT_RE.search(text)
    if dollar is not None:
        starts.append(dollar.start())
    return min(starts, default=-1)


def _tokenize(text: str, line: int, start: int = 0) -> list[_Token]:
    return [_Token(m.group(), line, m.start() + 1) for m in _TOKEN_RE.finditer(text, start)]


def _join(texts: list[str]) -> str:
    """Join tokens with single spaces, attaching parentheses: ``SIN(0 1 1k)``."""
    out = ""
    for text in texts:
        if not out:
            out = text
        elif text in ("(", ")") or out.endswith("("):
            out += text
        else:
            out += " " + text
    return out


def _section_end(lines: list[_Line], start: int) -> int | None:
    """Index of the ``.endl`` closing the one-word ``.lib name`` at ``start``, or None."""
    if len(lines[start].tokens) != 2:
        return None
    for j in range(start + 1, len(lines)):
        keyword = lines[j].keyword
        if keyword == ".endl":
            return j
        if keyword == ".lib" and len(lines[j].tokens) == 2:
            return None
    return None


# ---------------------------------------------------------------- parser


class _Parser:
    def __init__(self, base_dir: Path | None) -> None:
        self.base_dir = base_dir
        self.diagnostics: list[Diagnostic] = []
        self.model_names: set[str] = set()
        self.param_names: set[str] = set()
        self.state = _State()

    # ---- diagnostics

    def report(self, line: _Line, token: _Token, severity: str, message: str) -> None:
        if line.site is None:
            self.diagnostics.append(Diagnostic(token.line, token.col, severity, message))
        else:
            site = line.site
            text = f"{site.file} line {token.line}: {message}"
            self.diagnostics.append(Diagnostic(site.line, site.col, severity, text))

    def error(self, line: _Line, token: _Token, message: str) -> None:
        self.report(line, token, ERROR, message)

    def warning(self, line: _Line, token: _Token, message: str) -> None:
        self.report(line, token, WARNING, message)

    @staticmethod
    def pos(line: _Line, token: _Token) -> tuple[int, int]:
        """(line, col) recorded on model objects: the include site for included lines."""
        if line.site is not None:
            return line.site.line, line.site.col
        return token.line, token.col

    # ---- stage 1: physical lines -> logical lines, with includes expanded

    def split(self, physical: list[str], first: int, site: _Site | None) -> list[_Line]:
        lines: list[_Line] = []
        for offset, raw in enumerate(physical):
            number = first + offset
            cut = comment_start(raw)
            text = raw if cut < 0 else raw[:cut]
            stripped = text.lstrip()
            if not stripped or stripped.startswith("*"):
                continue
            if stripped.startswith("+"):
                start = len(text) - len(stripped) + 1
                tokens = _tokenize(text, number, start)
                if lines:
                    lines[-1].tokens.extend(tokens)
                else:
                    orphan = _Line([_Token("+", number, start)], site)
                    self.error(orphan, orphan.tokens[0], "continuation line with nothing before it")
                continue
            tokens = _tokenize(text, number)
            if tokens:
                lines.append(_Line(tokens, site))
        return lines

    def read(
        self,
        physical: list[str],
        first: int,
        site: _Site | None,
        base_dir: Path | None,
        stack: tuple[_Key, ...],
    ) -> list[_Line]:
        return self.expand(self.split(physical, first, site), base_dir, stack)

    def expand(
        self, lines: list[_Line], base_dir: Path | None, stack: tuple[_Key, ...]
    ) -> list[_Line]:
        """Expand ``.include`` and ``.lib`` lines; drop library sections not asked for."""
        out: list[_Line] = []
        skipped: list[str] = []
        first_skipped: _Line | None = None
        i = 0
        while i < len(lines):
            line = lines[i]
            keyword = line.keyword
            if keyword in (".include", ".inc"):
                out.extend(self.include(line, base_dir, stack))
            elif keyword == ".lib":
                end = _section_end(lines, i) if line.site is not None else None
                if end is not None:
                    skipped.append(line.tokens[1].text)
                    first_skipped = first_skipped or line
                    i = end + 1
                    continue
                if len(line.tokens) > 3:
                    self.warning(line, line.tokens[3], f"unexpected '{line.tokens[3].text}'")
                section = line.tokens[2] if len(line.tokens) > 2 else None
                out.extend(self.include(line, base_dir, stack, section))
            elif keyword == ".endl":
                self.error(line, line.tokens[0], ".endl without .lib section")
            elif line.site is not None and keyword == ".end":
                pass  # an .end inside an included file does not end the netlist
            else:
                out.append(line)
            i += 1
        if first_skipped is not None and first_skipped.site is not None:
            file = first_skipped.site.file
            self.warning(
                first_skipped,
                first_skipped.tokens[0],
                f"{file} has library sections ({', '.join(skipped)}); choose one with "
                f".lib {file} <section>. The sections were not read",
            )
        return out

    def include(
        self,
        line: _Line,
        base_dir: Path | None,
        stack: tuple[_Key, ...],
        section: _Token | None = None,
    ) -> list[_Line]:
        card = line.tokens[0]
        if len(line.tokens) < 2:
            self.error(line, card, f"{card.text.lower()} needs a file name")
            return []
        name = line.tokens[1].text.strip("'\"")
        path = Path(name)
        if not path.is_absolute():
            if base_dir is None:
                self.error(
                    line,
                    line.tokens[1],
                    f"cannot find '{name}': relative .include needs the netlist to be "
                    "opened from a file",
                )
                return []
            path = base_dir / path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        wanted = section.text.strip("'\"") if section is not None else None
        key = (resolved, wanted.casefold() if wanted is not None else None)
        if key in stack:
            self.error(line, line.tokens[1], f"'{name}' includes itself")
            return []
        if len(stack) >= _MAX_INCLUDE_DEPTH:
            self.error(line, line.tokens[1], "too many nested .include files")
            return []
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            self.error(line, line.tokens[1], f"cannot read include file '{name}': {reason}")
            return []
        if encrypted_block_line(text) is not None:
            self.error(
                line,
                line.tokens[1],
                f"'{name}' is an encrypted model file: not supported (QtSpice never decrypts it)",
            )
            return []
        if line.site is None:
            site = _Site(card.line, card.col, path.name)
        else:
            site = _Site(line.site.line, line.site.col, path.name)
        if section is None:
            return self.read(text.splitlines(), 1, site, resolved.parent, (*stack, key))
        lines = self.split(text.splitlines(), 1, site)
        block = self.section(line, section, path.name, lines)
        if block is None:
            return []
        return self.expand(block, resolved.parent, (*stack, key))

    def section(
        self, line: _Line, section: _Token, file: str, lines: list[_Line]
    ) -> list[_Line] | None:
        """The lines of library section ``section`` in ``lines``, or None (reported)."""
        wanted = section.text.strip("'\"")
        names: list[str] = []
        for i, header in enumerate(lines):
            if header.keyword != ".lib" or len(header.tokens) != 2:
                continue
            names.append(header.tokens[1].text)
            if header.tokens[1].text.casefold() != wanted.casefold():
                continue
            end = _section_end(lines, i)
            if end is None:
                self.error(header, header.tokens[0], f"missing .endl for .lib {wanted}")
                return None
            closer = lines[end]
            if len(closer.tokens) > 1 and closer.tokens[1].text.casefold() != wanted.casefold():
                self.warning(
                    closer, closer.tokens[1], f".endl {closer.tokens[1].text} closes {wanted}"
                )
            return lines[i + 1 : end]
        have = f" (it has {', '.join(names)})" if names else " (it has no sections)"
        self.error(line, section, f"no library section '{wanted}' in {file}{have}")
        return None

    # ---- stage 2: collect names used to resolve ambiguous lines

    def prescan(self, lines: list[_Line]) -> None:
        for line in lines:
            if line.keyword == ".model" and len(line.tokens) > 1:
                self.model_names.add(line.tokens[1].text.casefold())
            elif line.keyword == ".param":
                tokens = line.tokens
                for i in range(1, len(tokens) - 1):
                    if tokens[i + 1].text == "=":
                        self.param_names.add(tokens[i].text.casefold())

    # ---- stage 3: cards and elements

    def run(self, text: str) -> Circuit:
        physical = text.splitlines()
        circuit = self.state.circuit
        circuit.title = physical[0].strip() if physical else ""
        circuit.end_line = max(1, len(physical))
        lines = self.read(physical[1:], 2, None, self.base_dir, ())
        self.prescan(lines)
        for line in lines:
            if self.state.ended:
                break
            try:
                self.dispatch(line)
            except Exception as exc:  # parse() must never raise
                self.error(line, line.tokens[0], f"internal parser error: {exc!r}")
        self.finish()
        return circuit

    def finish(self) -> None:
        sub = self.state.subckt
        if sub is not None:
            self.diagnostics.append(
                Diagnostic(sub.source_line, 1, ERROR, f"missing .ends for .subckt {sub.name}")
            )
            self.state.circuit.subcircuits.append(sub)
            self.state.subckt = None

    def scope(self) -> _Scope:
        sub = self.state.subckt
        if sub is not None:
            return _Scope(sub.elements, sub.models)
        return _Scope(self.state.circuit.elements, self.state.circuit.models)

    def dispatch(self, line: _Line) -> None:
        first = line.tokens[0]
        if self.state.in_control:
            if line.keyword == ".endc":
                self.state.in_control = False
            return
        if first.text.startswith("."):
            self.card(line)
        else:
            self.element(line)

    # ---- helpers

    def split_params(
        self, line: _Line, tokens: list[_Token]
    ) -> tuple[list[_Token], dict[str, str]]:
        """Separate ``key=value`` pairs from positional tokens."""
        positional: list[_Token] = []
        params: dict[str, str] = {}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.text == "=":
                self.error(line, token, "'=' without a parameter name")
                i += 1
            elif i + 1 < len(tokens) and tokens[i + 1].text == "=":
                if i + 2 < len(tokens) and tokens[i + 2].text not in ("=", "(", ")"):
                    params[token.text.casefold()] = tokens[i + 2].text
                else:
                    self.error(line, token, f"parameter '{token.text}' has no value")
                i += 3
            else:
                positional.append(token)
                i += 1
        return positional, params

    def is_value(self, text: str) -> bool:
        if text.startswith("{"):
            return text.endswith("}") and len(text) > 2
        if text.startswith("'"):
            return text.endswith("'") and len(text) > 2
        return parse_number(text) is not None or text.casefold() in self.param_names

    def check_value(self, line: _Line, token: _Token) -> None:
        text = token.text
        if self.is_value(text):
            return
        if text.startswith("{"):
            self.error(line, token, f"unclosed '{{' in '{text}'")
        elif text.startswith("'"):
            self.error(line, token, f"unclosed quote in {text}")
        else:
            self.error(line, token, f"bad number '{text}'")

    # ---- elements

    def element(self, line: _Line) -> None:
        name_token = line.tokens[0]
        name = name_token.text
        kind = name[0].upper()
        if kind not in ELEMENT_KINDS:
            if kind in _UNSUPPORTED_KINDS:
                self.warning(
                    line, name_token, f"{kind} elements are not supported in v0.1; line ignored"
                )
            else:
                self.error(line, name_token, f"unknown element type '{name[0]}' in '{name}'")
            return

        positional, params = self.split_params(line, line.tokens[1:])
        positional = [t for t in positional if t.text.casefold() != "params:"]

        if kind == "X":
            node_count = len(positional) - 1
            if node_count < 1:
                self.error(line, name_token, f"{name} needs at least one node and a subcircuit")
                return
        else:
            node_count = {"E": 4, "G": 4, "M": 4, "Q": 3}.get(kind, 2)
            if (
                kind == "Q"
                and len(positional) >= 5
                and positional[3].text.casefold() not in self.model_names
            ):
                node_count = 4  # Q c b e substrate model
            if len(positional) < node_count:
                self.error(line, name_token, f"{name} needs {node_count} nodes")
                return

        node_tokens = positional[:node_count]
        rest = positional[node_count:]
        for token in node_tokens:
            if token.text in ("(", ")") or token.text.startswith(("{", "'")):
                self.error(line, token, f"'{token.text}' is not a valid node name")
                return

        if not self.check_rest(line, name_token, kind, rest, params):
            return

        source_line, name_col = self.pos(line, name_token)
        value_token = rest[0] if rest else name_token
        element = Element(
            name=name,
            kind=kind,
            nodes=[t.text.lower() for t in node_tokens],
            value=_join([t.text for t in rest]),
            params=params,
            source_line=source_line,
            name_col=name_col,
            node_cols=[self.pos(line, t)[1] for t in node_tokens],
            value_col=self.pos(line, value_token)[1],
        )
        self.scope().elements.append(element)

    def check_rest(
        self,
        line: _Line,
        name_token: _Token,
        kind: str,
        rest: list[_Token],
        params: dict[str, str],
    ) -> bool:
        """Check what follows the nodes. Returns False if the element must be dropped."""
        name = name_token.text
        if kind in "RCL":
            if not rest:
                self.error(line, name_token, f"{name} has no value")
                return False
            self.check_value(line, rest[0])
            for token in rest[1:]:
                self.warning(line, token, f"unexpected '{token.text}' after the value")
        elif kind in "VI":
            for token in rest:
                if token.text.casefold() not in _SOURCE_WORDS:
                    self.check_value(line, token)
        elif kind in "DQMX":
            what = "subcircuit" if kind == "X" else "model"
            if not rest:
                self.error(line, name_token, f"{name} needs a {what} name")
                return False
            if kind != "X":
                for token in rest[1:]:
                    self.check_value(line, token)
        elif kind in "EG":
            if not rest and not params:
                self.error(line, name_token, f"{name} has no gain")
                return False
            if len(rest) == 1:
                self.check_value(line, rest[0])
        elif kind in "FH":
            if len(rest) < 2:
                self.error(line, name_token, f"{name} needs a controlling source and a gain")
                return False
            if len(rest) == 2:
                self.check_value(line, rest[1])
        return True

    # ---- dot-cards

    def card(self, line: _Line) -> None:
        keyword = line.keyword
        token = line.tokens[0]
        handler = {
            ".model": self.model_card,
            ".subckt": self.subckt_card,
            ".ends": self.ends_card,
            ".param": self.param_card,
            ".op": self.analysis_card,
            ".dc": self.analysis_card,
            ".ac": self.analysis_card,
            ".tran": self.analysis_card,
            ".end": self.end_card,
        }.get(keyword)
        if handler is not None:
            handler(line)
        elif keyword == ".control":
            self.warning(line, token, ".control blocks are not supported in v0.1; ignored")
            self.state.in_control = True
        elif keyword == ".endc":
            self.error(line, token, ".endc without .control")
        else:
            self.warning(line, token, f"{token.text} is not supported in v0.1; line ignored")

    def model_card(self, line: _Line) -> None:
        card = line.tokens[0]
        if len(line.tokens) < 3:
            self.error(line, card, ".model needs a name and a type")
            return
        name, type_ = line.tokens[1], line.tokens[2]
        positional, params = self.split_params(line, line.tokens[3:])
        for token in positional:
            if token.text not in ("(", ")"):
                self.warning(line, token, f"unexpected '{token.text}' in .model (want name=value)")
        self.scope().models.append(
            Model(name.text, type_.text, params, source_line=self.pos(line, card)[0])
        )

    def subckt_card(self, line: _Line) -> None:
        card = line.tokens[0]
        if self.state.subckt is not None:
            self.error(line, card, "nested .subckt is not supported; close the previous one first")
            return
        if len(line.tokens) < 2:
            self.error(line, card, ".subckt needs a name")
            return
        positional, params = self.split_params(line, line.tokens[2:])
        ports = [t.text.lower() for t in positional if t.text.casefold() != "params:"]
        self.state.subckt = Subcircuit(
            name=line.tokens[1].text,
            ports=ports,
            params=params,
            source_line=self.pos(line, card)[0],
        )

    def ends_card(self, line: _Line) -> None:
        card = line.tokens[0]
        sub = self.state.subckt
        if sub is None:
            self.error(line, card, ".ends without .subckt")
            return
        if len(line.tokens) > 1 and line.tokens[1].text.casefold() != sub.name.casefold():
            self.warning(line, line.tokens[1], f".ends {line.tokens[1].text} closes {sub.name}")
        self.state.circuit.subcircuits.append(sub)
        self.state.subckt = None

    def param_card(self, line: _Line) -> None:
        card = line.tokens[0]
        positional, params = self.split_params(line, line.tokens[1:])
        for token in positional:
            self.error(line, token, f"expected name=value, got '{token.text}'")
        if not params and not positional:
            self.error(line, card, ".param needs name=value")
        sub = self.state.subckt
        if sub is not None:
            sub.local_params.update(params)
        else:
            self.state.circuit.params.update(params)

    def analysis_card(self, line: _Line) -> None:
        card = line.tokens[0]
        kind = line.keyword[1:]
        if self.state.subckt is not None:
            self.error(line, card, f".{kind} is not allowed inside .subckt")
            return
        args = line.tokens[1:]
        if not self.check_analysis(line, card, kind, args):
            return
        self.state.circuit.analyses.append(
            Analysis(kind, [t.text for t in args], source_line=self.pos(line, card)[0])
        )

    def check_analysis(self, line: _Line, card: _Token, kind: str, args: list[_Token]) -> bool:
        def numbers(tokens: list[_Token]) -> None:
            for token in tokens:
                self.check_value(line, token)

        if kind == "op":
            for token in args:
                self.warning(line, token, f".op takes no arguments; '{token.text}' ignored")
            del args[:]
        elif kind == "dc":
            if len(args) not in (4, 8):
                self.error(
                    line, card, ".dc needs: source start stop step [source2 start2 stop2 step2]"
                )
                return False
            numbers(args[1:4] + args[5:8])
        elif kind == "ac":
            if len(args) != 4:
                self.error(line, card, ".ac needs: dec|oct|lin points fstart fstop")
                return False
            if args[0].text.casefold() not in ("dec", "oct", "lin"):
                self.error(line, args[0], f"'{args[0].text}' is not dec, oct or lin")
            numbers(args[1:])
        elif kind == "tran":
            values = args[:-1] if args and args[-1].text.casefold() == "uic" else args
            if not 2 <= len(values) <= 4:
                self.error(line, card, ".tran needs: tstep tstop [tstart [tmax]] [uic]")
                return False
            numbers(values)
        return True

    def end_card(self, line: _Line) -> None:
        self.state.circuit.end_line = self.pos(line, line.tokens[0])[0]
        self.finish()
        self.state.ended = True


def parse(text: str, base_dir: str | Path | None = None) -> ParseResult:
    """Parse a SPICE netlist. The first line is the title. Never raises.

    ``base_dir`` is the directory relative ``.include`` paths are resolved against;
    with None, only absolute include paths work.
    """
    base = Path(base_dir) if base_dir is not None else None
    parser = _Parser(base)
    try:
        circuit = parser.run(text if isinstance(text, str) else str(text))
    except Exception as exc:  # parse() must never raise
        parser.diagnostics.append(Diagnostic(1, 1, ERROR, f"internal parser error: {exc!r}"))
        circuit = parser.state.circuit
    diagnostics = sorted(parser.diagnostics, key=lambda d: (d.line, d.col))
    return ParseResult(circuit, diagnostics)
