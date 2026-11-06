from __future__ import annotations

import json

import pytest

from core.netlist.model import ERROR, WARNING
from core.waveform.layout import (
    MAX_PANES,
    Pane,
    PlotLayout,
    YAxis,
    layouts_from_dict,
    layouts_to_dict,
)


def _layout() -> PlotLayout:
    layout = PlotLayout.default("tran")
    layout.panes = [Pane(["V(in)", "V(out)"]), Pane(["I(R1)"])]
    return layout


def test_default_layouts() -> None:
    tran = PlotLayout.default("tran")
    assert len(tran.panes) == 1 and tran.panes[0].traces == [] and not tran.x.log
    ac = PlotLayout.default("ac")
    assert ac.x.log and ac.is_ac  # AC opens as a Bode plot


def test_add_delete_move_panes() -> None:
    layout = _layout()
    assert layout.add_pane(0, above=True) == 0
    assert [p.traces for p in layout.panes] == [[], ["V(in)", "V(out)"], ["I(R1)"]]
    assert layout.active == 0
    assert layout.add_pane(2, above=False) == 3
    assert layout.delete_pane(3) and layout.delete_pane(0)
    assert [p.traces for p in layout.panes] == [["V(in)", "V(out)"], ["I(R1)"]]
    assert layout.move_pane(1, 0)
    assert [p.traces for p in layout.panes] == [["I(R1)"], ["V(in)", "V(out)"]]
    assert layout.delete_pane(0) and not layout.delete_pane(0)  # the last pane stays
    assert not layout.delete_pane(5)


def test_pane_limit() -> None:
    layout = PlotLayout.default("tran")
    for _ in range(MAX_PANES - 1):
        assert layout.add_pane(0, above=False) >= 0
    assert layout.add_pane(0, above=False) == -1


def test_trace_moves_and_copies() -> None:
    layout = _layout()
    assert layout.drop_trace(0, "V(out)", 1, copy_=False)  # move
    assert [p.traces for p in layout.panes] == [["V(in)"], ["I(R1)", "V(out)"]]
    assert layout.drop_trace(1, "I(R1)", 0, copy_=True)  # Ctrl = copy
    assert [p.traces for p in layout.panes] == [["V(in)", "I(R1)"], ["I(R1)", "V(out)"]]
    assert not layout.drop_trace(0, "V(in)", 0, copy_=False)  # own pane: nothing
    # Moving onto a pane that already has it only removes it from the source.
    assert layout.drop_trace(0, "I(R1)", 1, copy_=False)
    assert [p.traces for p in layout.panes] == [["V(in)"], ["I(R1)", "V(out)"]]
    assert layout.drop_trace(-1, "V(x)", 0, copy_=False)  # from the trace list
    assert layout.panes[0].traces == ["V(in)", "V(x)"]
    assert not layout.add_trace(0, "V(in)")  # once per pane
    assert layout.all_traces() == ["V(in)", "V(x)", "I(R1)", "V(out)"]


def test_show_only_and_remove() -> None:
    layout = _layout()
    assert layout.show_only(0, "V(out)")
    assert layout.panes[0].traces == ["V(out)"]
    assert not layout.show_only(0, "V(out)")
    assert layout.remove_trace(1, "I(R1)") and not layout.remove_trace(1, "I(R1)")
    layout.panes[1].traces = ["V(out)"]
    assert layout.remove_everywhere("V(out)")
    assert layout.all_traces() == []


def test_zoom_history() -> None:
    layout = _layout()
    layout.push_history()
    layout.x.auto, layout.x.lo, layout.x.hi = False, 1e-3, 2e-3
    layout.panes[0].y = YAxis(auto=False, lo=0.1, hi=0.2)
    assert layout.zoom_back()
    assert layout.x.auto and layout.panes[0].y.auto
    assert not layout.zoom_back()
    layout.x.auto = False
    layout.auto_range()
    assert layout.x.auto


def test_round_trip() -> None:
    layout = _layout()
    layout.x.log = True
    layout.panes[1].title = "current"
    layout.panes[1].y = YAxis(auto=False, lo=1e-9, hi=1e-2, log=True, show_abs=True)
    layout.push_history()  # not saved
    data = json.loads(json.dumps(layout.to_dict()))
    back, found = PlotLayout.from_dict(data, "tran")
    assert found == []
    assert back.to_dict() == layout.to_dict()
    assert back.history == []


def test_bad_input_gives_diagnostics_not_exceptions() -> None:
    layout, found = PlotLayout.from_dict("nonsense", "ac")
    assert [d.severity for d in found] == [ERROR]
    assert layout.x.log and len(layout.panes) == 1

    layout, found = PlotLayout.from_dict(
        {
            "x": {"auto": "yes", "lo": 5, "hi": 1},
            "panes": [{"traces": ["V(a)", 3, "V(a)"], "y": {"lo": "x"}, "title": 7}, 4],
        },
        "tran",
    )
    assert len(layout.panes) == 2
    assert layout.panes[0].traces == ["V(a)"] and layout.panes[0].title == ""
    assert layout.x.auto
    assert found and all(d.severity == WARNING for d in found)

    layout, found = PlotLayout.from_dict({"panes": []}, "dc")
    assert len(layout.panes) == 1 and found


@pytest.mark.parametrize("data", [[], None, 3])
def test_layouts_from_dict_bad(data: object) -> None:
    layouts, found = layouts_from_dict(data)
    assert layouts == {} and found


def test_layouts_dict() -> None:
    layouts = {"ac": PlotLayout.default("ac"), "tran": _layout()}
    data = layouts_to_dict(layouts)
    assert list(data) == ["tran", "ac"]
    back, found = layouts_from_dict({**data, "noise": {}})
    assert set(back) == {"tran", "ac"} and len(found) == 1  # unknown analysis warned


def test_rename_trace_keeps_its_place() -> None:
    layout = PlotLayout.default("tran")
    layout.panes[0].traces = ["V(a)", "V(b)", "V(c)"]
    assert layout.rename_trace(0, "V(b)", "2*V(b)")
    assert layout.panes[0].traces == ["V(a)", "2*V(b)", "V(c)"]
    assert layout.rename_trace(0, "2*V(b)", "V(c)")  # already shown: the old one goes
    assert layout.panes[0].traces == ["V(a)", "V(c)"]
    assert not layout.rename_trace(0, "V(zz)", "x")
    assert not layout.rename_trace(0, "V(a)", "V(a)")
    assert not layout.rename_trace(3, "V(a)", "x")
    assert not layout.rename_trace(0, "V(a)", "")
