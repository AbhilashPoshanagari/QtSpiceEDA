"""Connectivity and reference checks on a parsed Circuit. Qt-free.

Checks: a ground node (0 or gnd) exists; every node has at least two connections;
element names are unique per scope; every model and subcircuit used is defined (and
X calls match the subcircuit's port count); at least one analysis card exists.
"""

from __future__ import annotations

from collections import Counter

from core.netlist.model import ERROR, GROUND_NODES, Circuit, Diagnostic, Element, Subcircuit


def validate(circuit: Circuit) -> list[Diagnostic]:
    """Return the problems found in ``circuit``, sorted by line and column."""
    found: list[Diagnostic] = []
    _check_ground(circuit, found)
    _check_scope(circuit.elements, None, found)
    for sub in circuit.subcircuits:
        _check_scope(sub.elements, sub, found)
    _check_references(circuit, found)
    if not circuit.analyses:
        found.append(
            Diagnostic(circuit.end_line, 1, ERROR, "no analysis card (.op, .dc, .ac or .tran)")
        )
    return sorted(found, key=lambda d: (d.line, d.col))


def _check_ground(circuit: Circuit, found: list[Diagnostic]) -> None:
    if not circuit.elements:
        found.append(Diagnostic(1, 1, ERROR, "the netlist has no elements"))
        return
    everything = [*circuit.elements, *(e for s in circuit.subcircuits for e in s.elements)]
    if not any(node in GROUND_NODES for e in everything for node in e.nodes):
        found.append(
            Diagnostic(1, 1, ERROR, "no ground node: connect at least one element to node 0")
        )


def _check_scope(elements: list[Element], sub: Subcircuit | None, found: list[Diagnostic]) -> None:
    where = f" in subcircuit {sub.name}" if sub is not None else ""

    seen: dict[str, Element] = {}
    for element in elements:
        key = element.name.casefold()
        first = seen.get(key)
        if first is None:
            seen[key] = element
        else:
            found.append(
                Diagnostic(
                    element.source_line,
                    element.name_col,
                    ERROR,
                    f"duplicate element name '{element.name}'{where} "
                    f"(first used on line {first.source_line})",
                )
            )

    counts: Counter[str] = Counter(node for e in elements for node in e.nodes)
    ports = sub.ports if sub is not None else []
    counts.update(ports)
    for element in elements:
        for index, node in enumerate(element.nodes):
            if counts[node] < 2 and node not in GROUND_NODES:
                col = element.node_cols[index] if index < len(element.node_cols) else 1
                found.append(
                    Diagnostic(
                        element.source_line,
                        col,
                        ERROR,
                        f"node '{node}'{where} has only one connection ({element.name})",
                    )
                )
    if sub is not None:
        used = {node for e in elements for node in e.nodes}
        for port in ports:
            if port not in used:
                found.append(
                    Diagnostic(
                        sub.source_line,
                        1,
                        ERROR,
                        f"port '{port}' of subcircuit {sub.name} is not connected inside it",
                    )
                )


def _check_references(circuit: Circuit, found: list[Diagnostic]) -> None:
    global_models = {m.name.casefold() for m in circuit.models}
    subckts = {s.name.casefold(): s for s in circuit.subcircuits}
    scopes: list[tuple[list[Element], set[str]]] = [(circuit.elements, global_models)]
    for sub in circuit.subcircuits:
        scopes.append((sub.elements, global_models | {m.name.casefold() for m in sub.models}))

    for elements, models in scopes:
        for element in elements:
            ref = element.reference()
            if ref is None:
                continue
            if element.kind == "X":
                target = subckts.get(ref.casefold())
                if target is None:
                    found.append(
                        Diagnostic(
                            element.source_line,
                            element.value_col,
                            ERROR,
                            f"unknown subcircuit '{ref}' used by {element.name}",
                        )
                    )
                elif len(target.ports) != len(element.nodes):
                    found.append(
                        Diagnostic(
                            element.source_line,
                            element.name_col,
                            ERROR,
                            f"{element.name} has {len(element.nodes)} nodes but subcircuit "
                            f"{target.name} has {len(target.ports)} ports",
                        )
                    )
            elif ref.casefold() not in models:
                found.append(
                    Diagnostic(
                        element.source_line,
                        element.value_col,
                        ERROR,
                        f"unknown model '{ref}' used by {element.name}",
                    )
                )
