"""Build a clean SPICE deck from a Circuit. Qt-free.

The deck is self-contained: included files were already expanded by the parser, so
their contents are written inline. ``parse(build(c))`` gives a Circuit equal to ``c``.
"""

from __future__ import annotations

from core.netlist.model import Analysis, Circuit, Element, Model, Subcircuit


def build(circuit: Circuit, analysis: Analysis | str | None = None) -> str:
    """Return the deck for ``circuit``, title line first and ``.end`` last.

    When ``analysis`` is given (an Analysis or a card such as ``".tran 1u 1m"``) it
    replaces all of the circuit's analysis cards.
    """
    lines = [circuit.title]
    lines += [f".param {key}={value}" for key, value in circuit.params.items()]
    lines += [_model(m) for m in circuit.models]
    for sub in circuit.subcircuits:
        lines += _subckt(sub)
    lines += [_element(e) for e in circuit.elements]
    if analysis is None:
        lines += [_analysis(a) for a in circuit.analyses]
    elif isinstance(analysis, str):
        lines.append(analysis.strip())
    else:
        lines.append(_analysis(analysis))
    lines.append(".end")
    return "\n".join(lines) + "\n"


def _params(params: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in params.items())


def _element(element: Element) -> str:
    parts = [element.name, *element.nodes]
    if element.value:
        parts.append(element.value)
    if element.params:
        if element.kind == "X":
            parts.append("params:")
        parts.append(_params(element.params))
    return " ".join(parts)


def _model(model: Model) -> str:
    card = f".model {model.name} {model.type}"
    return f"{card} ({_params(model.params)})" if model.params else card


def _subckt(sub: Subcircuit) -> list[str]:
    head = " ".join([".subckt", sub.name, *sub.ports])
    if sub.params:
        head += f" params: {_params(sub.params)}"
    body = [_model(m) for m in sub.models] + [_element(e) for e in sub.elements]
    return [head, *body, f".ends {sub.name}"]


def _analysis(analysis: Analysis) -> str:
    return " ".join([f".{analysis.kind}", *analysis.args])
