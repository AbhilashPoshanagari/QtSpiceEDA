"""Spike 5: does a German process locale (decimal comma) break ngspice?

Each mode runs in a fresh process, through the app's normal NgspiceAdapter:
  "c"    : locale untouched (Python starts in the C locale)
  "de"   : setlocale(LC_ALL, de_DE) before loading the deck
  "de+c" : setlocale(LC_ALL, de_DE), then setlocale(LC_NUMERIC, "C")   (rule 4)

Checked: numbers in the deck (1.5, 1.5k, .param 2.5k, a B-source 1.5*V(a)) and
numbers in ngspice's printed output ("print" and "let" in the control language).

Windows note: ngspice.dll links its C runtime statically (it imports no msvcrt /
ucrtbase / api-ms-win-crt DLL), so Python's setlocale - which sets the ucrtbase
locale - cannot reach it there. On Linux and macOS ngspice shares the process libc.

Run:  python scratch/phase3/spike5_locale.py
"""

from __future__ import annotations

import locale
import sys
from dataclasses import dataclass, field

DECK = """spike5 locale
V1 a 0 1.5
R1 a b 1.5k
R2 b 0 1.5k
.param rr=2.5k
R3 a c {rr}
R4 c 0 2.5k
B1 d 0 V=1.5*V(a)
Rd d 0 1k
.op
.end
"""

EXPECTED = {"V(a)": 1.5, "V(b)": 0.75, "V(c)": 0.75, "V(d)": 2.25}

GERMAN_NAMES = ("de_DE.UTF-8", "de_DE.utf8", "de_DE", "German_Germany.1252", "de-DE")


@dataclass
class Spike5Result:
    mode: str
    platform: str
    locale_name: str  # the German locale that setlocale accepted ("" if none)
    decimal_point: str  # Python's localeconv() after the mode's setlocale calls
    values: dict[str, float] = field(default_factory=dict)
    values_ok: bool = False
    printed: list[str] = field(default_factory=list)  # ngspice "print" output lines
    printed_comma: bool = False  # a printed number used ',' as decimal point
    error: str = ""


def set_german() -> str:
    for name in GERMAN_NAMES:
        try:
            locale.setlocale(locale.LC_ALL, name)
            return name
        except locale.Error:
            continue
    return ""


def run(mode: str) -> Spike5Result:
    from core.sim.ngspice_adapter import NgspiceAdapter

    res = Spike5Result(mode=mode, platform=sys.platform, locale_name="", decimal_point="")
    if mode in ("de", "de+c"):
        res.locale_name = set_german()
        if not res.locale_name:
            res.error = "no German locale installed"
            return res
    if mode == "de+c":
        locale.setlocale(locale.LC_NUMERIC, "C")
    res.decimal_point = locale.localeconv()["decimal_point"]

    ad = NgspiceAdapter()
    if not ad.available():
        res.error = ad.last_error
        return res
    try:
        ad.load(DECK)
        ad.run()
    except Exception as exc:
        res.error = repr(exc)
        return res
    vec = ad.results().vectors
    res.values = {k: float(vec[k][0]) for k in EXPECTED if k in vec}
    res.values_ok = all(abs(res.values.get(k, 1e9) - v) < 1e-9 for k, v in EXPECTED.items())
    before = len(ad.console)
    ad._command("let q = 1.5 * 1.25")
    ad._command("print q v(b)")
    res.printed = [ln for ln in ad.console[before:] if "=" in ln]
    res.printed_comma = any("," in ln.split("=", 1)[1] for ln in res.printed)
    return res


def main() -> None:
    from fresh import in_fresh_process

    for mode in ("c", "de", "de+c"):
        r = in_fresh_process(run, mode)
        print(
            f"{r.platform} mode={mode:5} locale={r.locale_name or '-':20} "
            f"decimal_point={r.decimal_point!r} values_ok={r.values_ok} "
            f"printed_comma={r.printed_comma} {r.error}"
        )
        print(f"    values  {r.values}")
        print(f"    printed {r.printed}")


if __name__ == "__main__":
    main()
