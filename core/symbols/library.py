"""Symbol library: where ``.asy`` files are found. Qt-free.

Search order: QtSpice's built-in symbols, then the user's folders in the order given,
then the optional LTspice folder (its ``sym`` subfolder when it has one). The first
folder that has a symbol wins. Every folder is read-only: the library never writes.

A symbol's key is its path below the search folder, without ``.asy`` and with ``/``
separators, e.g. ``res`` or ``Opamps/opamp2``. ``lookup`` accepts that key with ``/``
or ``\\`` (as LTspice writes it), any case; a bare name with no folder that is not a
key also matches the first symbol of that name in any subfolder.

The folder listing and parsed symbols are cached; ``set_paths`` with different paths,
or ``refresh``, clears the cache. Never raises: problems are Diagnostics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.symbols.asy import AsyParser, Symbol

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"

BUILTIN = "builtin"
USER = "user"
LTSPICE = "ltspice"


@dataclass(frozen=True)
class SymbolEntry:
    key: str
    path: Path
    source: str  # BUILTIN, USER or LTSPICE
    read_only: bool = True

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


def normalize_key(name: str) -> str:
    key = name.strip().replace("\\", "/").strip("/")
    if key.casefold().endswith(".asy"):
        key = key[:-4]
    return key


class SymbolLibrary:
    def __init__(
        self,
        user_dirs: list[str | Path] | tuple[str | Path, ...] = (),
        ltspice_dir: str | Path | None = None,
        builtin_dir: str | Path | None = BUILTIN_DIR,
    ) -> None:
        self._builtin = Path(builtin_dir) if builtin_dir is not None else None
        self._user: tuple[Path, ...] = ()
        self._ltspice: Path | None = None
        self._entries: list[SymbolEntry] | None = None
        self._by_key: dict[str, SymbolEntry] = {}
        self._by_name: dict[str, SymbolEntry] = {}
        self._parsed: dict[Path, tuple[Symbol, list[Diagnostic]]] = {}
        self._diagnostics: list[Diagnostic] = []
        self.set_paths(user_dirs, ltspice_dir)

    # ---- paths

    @property
    def user_dirs(self) -> tuple[Path, ...]:
        return self._user

    @property
    def ltspice_dir(self) -> Path | None:
        return self._ltspice

    def search_paths(self) -> list[tuple[Path, str]]:
        """(folder, source) in search order, as they will be scanned."""
        paths: list[tuple[Path, str]] = []
        if self._builtin is not None:
            paths.append((self._builtin, BUILTIN))
        paths += [(p, USER) for p in self._user]
        if self._ltspice is not None:
            sym = self._ltspice / "sym"
            paths.append((sym if sym.is_dir() else self._ltspice, LTSPICE))
        return paths

    def set_paths(
        self,
        user_dirs: list[str | Path] | tuple[str | Path, ...],
        ltspice_dir: str | Path | None = None,
    ) -> bool:
        """Change the user and LTspice folders. True (and cache cleared) if they changed."""
        user = tuple(Path(p) for p in user_dirs if str(p).strip())
        ltspice = (
            Path(ltspice_dir) if ltspice_dir is not None and str(ltspice_dir).strip() else None
        )
        if user == self._user and ltspice == self._ltspice and self._entries is not None:
            return False
        self._user, self._ltspice = user, ltspice
        self.refresh()
        return True

    def refresh(self) -> None:
        """Forget the folder listing and parsed symbols; they are re-read on next use."""
        self._entries = None
        self._by_key.clear()
        self._by_name.clear()
        self._parsed.clear()
        self._diagnostics = []

    # ---- listing

    @property
    def diagnostics(self) -> list[Diagnostic]:
        """Problems with the search folders (missing, unreadable)."""
        self._scan()
        return list(self._diagnostics)

    def entries(self) -> list[SymbolEntry]:
        """Every visible symbol (first in search order wins), sorted by key."""
        self._scan()
        return sorted(self._by_key.values(), key=lambda e: e.key.casefold())

    def names(self) -> list[str]:
        return [e.key for e in self.entries()]

    def find(self, name: str) -> SymbolEntry | None:
        self._scan()
        key = normalize_key(name).casefold()
        entry = self._by_key.get(key)
        if entry is None and "/" not in key:
            entry = self._by_name.get(key)
        return entry

    def lookup(self, name: str) -> tuple[Symbol | None, list[Diagnostic]]:
        """The parsed symbol called ``name`` (cached; do not modify it)."""
        entry = self.find(name)
        if entry is None:
            return None, [Diagnostic(0, 0, ERROR, f"symbol not found: {name}")]
        cached = self._parsed.get(entry.path)
        if cached is None:
            cached = AsyParser.parse(entry.path)
            self._parsed[entry.path] = cached
        return cached[0], list(cached[1])

    def _scan(self) -> None:
        if self._entries is not None:
            return
        entries: list[SymbolEntry] = []
        for folder, source in self.search_paths():
            if not folder.is_dir():
                self._diagnostics.append(
                    Diagnostic(0, 0, WARNING, f"symbol folder not found: {folder}")
                )
                continue
            for path in self._walk(folder):
                rel = path.relative_to(folder).with_suffix("").as_posix()
                entry = SymbolEntry(rel, path, source)
                entries.append(entry)
                self._by_key.setdefault(rel.casefold(), entry)
                self._by_name.setdefault(entry.name.casefold(), entry)
        self._entries = entries

    def _walk(self, root: Path) -> list[Path]:
        found: list[Path] = []

        def on_error(exc: OSError) -> None:
            self._diagnostics.append(Diagnostic(0, 0, WARNING, f"cannot read {exc.filename}"))

        for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
            dirnames.sort(key=str.casefold)
            for name in sorted(filenames, key=str.casefold):
                if name.casefold().endswith(".asy"):
                    found.append(Path(dirpath, name))
        return found
