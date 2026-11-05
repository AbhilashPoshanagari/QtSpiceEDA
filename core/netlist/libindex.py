"""Library index: which models and subcircuits are in which library files. Qt-free.

``LibraryIndex.scan(folders)`` walks the folders (recursively) and lists every
``.model`` (name, type, file, line) and ``.subckt`` (name, pins, file, line), with the
``.lib`` section each one is in. It is a fast line scanner, not the full netlist
parser, so it can index hundreds of vendor files quickly.

Encrypted model files (a ``* Begin:`` comment, hex-only comment lines, ``* End``) are
listed with ``encrypted=True`` and not indexed; QtSpice never tries to decrypt them.

Like the parser, ``scan`` never raises: unreadable files and folders become
Diagnostics.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.netlist.model import ERROR, WARNING, Diagnostic

LIBRARY_SUFFIXES = frozenset({".lib", ".sub", ".mod", ".inc", ".cir", ".sp", ".spi"})

_BEGIN_RE = re.compile(r"^[ \t]*\*[ \t]*begin:", re.I | re.M)
_END_RE = re.compile(r"\*\s*end\b", re.I)
_HEX_RE = re.compile(r"\*\s*[0-9A-Fa-f]+\s*")
_DOLLAR_RE = re.compile(r"(?:^|(?<=\s))\$")


def encrypted_block_line(text: str) -> int | None:
    """1-based line of the ``* Begin:`` that opens an encrypted block, or None.

    A block is ``* Begin:``, one or more comment lines holding only hex digits, then
    ``* End``. A plain comment that merely starts with "Begin:" does not count.
    """
    for match in _BEGIN_RE.finditer(text):
        start = text.count("\n", 0, match.start())
        body = text[match.start() :].splitlines()[1:]
        hex_lines = 0
        for raw in body:
            line = raw.strip()
            if not line:
                continue
            if _END_RE.fullmatch(line):
                if hex_lines:
                    return start + 1
                break
            if not _HEX_RE.fullmatch(line):
                break
            hex_lines += 1
    return None


def read_library_text(path: Path) -> str:
    """Read a library file: UTF-8, else Windows-1252 (common for LTspice files)."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@dataclass(frozen=True)
class ModelEntry:
    name: str
    type: str
    file: Path
    line: int
    section: str | None = None


@dataclass(frozen=True)
class SubcktEntry:
    name: str
    pins: tuple[str, ...]
    file: Path
    line: int
    section: str | None = None


@dataclass
class LibFile:
    """One scanned file. Diagnostics carry lines within this file."""

    path: Path
    encrypted: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class LibraryIndex:
    models: list[ModelEntry] = field(default_factory=list)
    subcircuits: list[SubcktEntry] = field(default_factory=list)
    files: list[LibFile] = field(default_factory=list)
    # Problems with the folders themselves (line 0: not about a file line).
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @classmethod
    def scan(
        cls,
        folders: list[str | Path] | tuple[str | Path, ...],
        suffixes: frozenset[str] = LIBRARY_SUFFIXES,
    ) -> LibraryIndex:
        """Index every library file under ``folders``. Never raises."""
        index = cls()
        seen: set[Path] = set()
        for folder in folders:
            root = Path(folder)
            if not root.is_dir():
                index.diagnostics.append(Diagnostic(0, 0, ERROR, f"folder not found: {root}"))
                continue
            for path in _walk(root, suffixes, index.diagnostics):
                try:
                    key = path.resolve()
                except OSError:
                    key = path
                if key in seen:
                    continue
                seen.add(key)
                index._scan_file(path)
        return index

    @property
    def encrypted_files(self) -> list[Path]:
        return [f.path for f in self.files if f.encrypted]

    def find_model(self, name: str) -> list[ModelEntry]:
        key = name.casefold()
        return [m for m in self.models if m.name.casefold() == key]

    def find_subckt(self, name: str) -> list[SubcktEntry]:
        key = name.casefold()
        return [s for s in self.subcircuits if s.name.casefold() == key]

    # ---- one file

    def _scan_file(self, path: Path) -> None:
        entry = LibFile(path)
        self.files.append(entry)
        try:
            text = read_library_text(path)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            entry.diagnostics.append(Diagnostic(1, 1, ERROR, f"cannot read file: {reason}"))
            return
        if (begin := encrypted_block_line(text)) is not None:
            entry.encrypted = True
            entry.diagnostics.append(
                Diagnostic(begin, 1, WARNING, "encrypted model file: not indexed (never decrypted)")
            )
            return
        section: tuple[str, int] | None = None
        for number, words in _logical_lines(text):
            keyword = words[0].casefold()
            if keyword == ".model":
                if len(words) < 3:
                    entry.diagnostics.append(
                        Diagnostic(number, 1, WARNING, ".model needs a name and a type")
                    )
                    continue
                kind = words[2].split("(", 1)[0] or (words[3] if len(words) > 3 else "")
                self.models.append(ModelEntry(words[1], kind, path, number, section and section[0]))
            elif keyword == ".subckt":
                if len(words) < 2:
                    entry.diagnostics.append(Diagnostic(number, 1, WARNING, ".subckt needs a name"))
                    continue
                pins: list[str] = []
                for word in words[2:]:
                    if word.startswith("="):  # "name = value": name was not a pin
                        del pins[-1:]
                        break
                    if "=" in word or word.casefold() == "params:":
                        break
                    pins.append(word)
                self.subcircuits.append(
                    SubcktEntry(words[1], tuple(pins), path, number, section and section[0])
                )
            elif keyword == ".lib" and len(words) == 2:
                section = (words[1].strip("'\""), number)
            elif keyword == ".endl":
                section = None
        if section is not None:
            entry.diagnostics.append(
                Diagnostic(section[1], 1, WARNING, f"missing .endl for .lib {section[0]}")
            )


def _walk(root: Path, suffixes: frozenset[str], diagnostics: list[Diagnostic]) -> list[Path]:
    found: list[Path] = []

    def on_error(exc: OSError) -> None:
        diagnostics.append(Diagnostic(0, 0, WARNING, f"cannot read folder {exc.filename}"))

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames.sort()
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in suffixes:
                found.append(Path(dirpath, name))
    return found


def _logical_lines(text: str) -> list[tuple[int, list[str]]]:
    """(line number, words) per dot-card line, comments cut.

    Only lines that start with a dot are kept: that is all the index needs. ``+``
    continuations are joined for ``.subckt`` only (its pins may continue); other cards
    need just their first words, so their continuations are skipped for speed.
    """
    out: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.lstrip()
        if not line or line[0] == "*":
            continue
        if line[0] == "+":
            if current is not None:
                current.extend(_strip_comment(line[1:]).split())
            continue
        current = None
        if line[0] == ".":
            words = _strip_comment(line).split()
            if words:
                out.append((number, words))
                if words[0].casefold() == ".subckt":
                    current = words
    return out


def _strip_comment(line: str) -> str:
    if ";" in line:
        line = line[: line.index(";")]
    if "$" in line:
        match = _DOLLAR_RE.search(line)
        if match is not None:
            line = line[: match.start()]
    return line
