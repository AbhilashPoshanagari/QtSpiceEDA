from __future__ import annotations

import textwrap

import pytest

from core.blocks.decorator import block, spec_of
from core.blocks.registry import BlockRegistry, match_ports, read_source
from core.blocks.spec import BlockSpec
from core.netlist.model import ERROR, WARNING

ADD_SUB = textwrap.dedent(
    """\
    @block(shape="circle", outputs=["sum", "diff"])
    def add_sub(a, b): return a + b, a - b
    """
)


def errors(result) -> list[tuple[int, str]]:
    return [(d.line, d.message) for d in result.diagnostics if d.severity == ERROR]


# ---- reading code


def test_add_sub_spec_from_source() -> None:
    specs, diagnostics = read_source(ADD_SUB)
    assert diagnostics == []
    [spec] = specs
    assert spec.name == "add_sub"
    assert spec.shape == "circle"
    assert spec.inputs == ["a", "b"]
    assert spec.outputs == ["sum", "diff"]


def test_syntax_error_on_right_line() -> None:
    code = "@block\ndef f(u):\n    return u +\n"
    specs, diagnostics = read_source(code)
    assert specs == []
    [d] = diagnostics
    assert d.severity == ERROR
    assert d.line == 3
    assert d.message.startswith("syntax error")


def test_unclosed_bracket_reported_inside_the_code() -> None:
    code = "@block\ndef f(u):\n    return (u\n"
    _, diagnostics = read_source(code)
    [d] = diagnostics
    assert 1 <= d.line <= 3


def test_indentation_error() -> None:
    _, diagnostics = read_source("@block\ndef f(u):\nreturn u\n")
    [d] = diagnostics
    assert d.line == 3
    assert "indentation" in d.message


def test_code_is_not_executed() -> None:
    code = 'raise SystemExit("ran")\nimport os\nos.remove("x")\n' + ADD_SUB
    specs, diagnostics = read_source(code)
    assert [s.name for s in specs] == ["add_sub"]
    assert diagnostics == []


def test_no_block_is_a_warning() -> None:
    _, diagnostics = read_source("def f(u):\n    return u\n")
    [d] = diagnostics
    assert d.severity == WARNING


def test_plain_functions_are_ignored() -> None:
    code = "def helper(x):\n    return x\n\n" + ADD_SUB
    specs, _ = read_source(code)
    assert [s.name for s in specs] == ["add_sub"]


@pytest.mark.parametrize(
    ("code", "line", "fragment"),
    [
        ("@block(shape='hexagon')\ndef f(u): return u\n", 1, "shape must be one of"),
        ("@block('circle')\ndef f(u): return u\n", 1, "keyword arguments only"),
        ("@block(size=3)\ndef f(u): return u\n", 1, "unknown @block argument"),
        ("X = 'y'\n@block(outputs=[X])\ndef f(u): return u\n", 2, "must be a constant"),
        ("@block\ndef f(u,\n      *rest): return u\n", 3, "*rest"),
        ("@block\ndef f(u, k=len('ab')): return u\n", 2, "plain constant"),
        ("@block(stateful=True)\ndef f(u): return u\n", 1, "needs a class"),
        ("@block\nasync def f(u): return u\n", 2, "async"),
        (
            "@block(stateful=True)\nclass C:\n    def output(self): return 0\n",
            2,
            "step",
        ),
        (
            "@block(stateful=True)\nclass C:\n    def __init__(self, k):\n        pass\n"
            "    def output(self): return 0\n    def step(self, u, dt): pass\n",
            3,
            "needs a default",
        ),
        (
            "@block\ndef f(u): return u\n\n@block\ndef f(v): return v\n",
            5,
            "already defined on line 2",
        ),
    ],
)
def test_bad_blocks_report_line(code: str, line: int, fragment: str) -> None:
    specs, diagnostics = read_source(code)
    assert [(d.line, d.severity) for d in diagnostics] == [(line, ERROR)], diagnostics
    assert fragment in diagnostics[0].message


def test_one_bad_block_does_not_hide_good_ones() -> None:
    code = "@block(shape='x')\ndef bad(u): return u\n\n" + ADD_SUB
    specs, diagnostics = read_source(code)
    assert [s.name for s in specs] == ["add_sub"]
    assert len(diagnostics) == 1


# ---- the AST path and the inspect path agree

PARITY_SOURCES = [
    ADD_SUB,
    '@block\ndef gain(u, k=2.0, *, label="g"):\n    """Scale."""\n    return k * u\n',
    "@block(color='#123456', outputs=[])\ndef sink(u, t): pass\n",
    "@block(stateful=True, outputs=['x', 'v'], shape='rectangle')\n"
    "class Mass:\n"
    '    """A mass.\n\n    With two states.\n    """\n'
    "    def __init__(self, m=1.0, x0=[0.0, 0.0]):\n"
    "        self.s = list(x0)\n"
    "    def output(self):\n"
    "        return tuple(self.s)\n"
    "    def step(self, force, dt):\n"
    "        pass\n",
]


@pytest.mark.parametrize("code", PARITY_SOURCES)
def test_ast_spec_matches_inspect_spec(code: str) -> None:
    [from_ast], diagnostics = read_source(code)
    assert diagnostics == []
    namespace: dict = {"block": block}
    exec(code, namespace)  # test process only: the registry itself never does this
    live = spec_of(namespace[from_ast.name])
    assert live == from_ast


# ---- the registry


def test_add_source_registers_blocks() -> None:
    reg = BlockRegistry()
    result = reg.add_source(ADD_SUB, "doc1")
    assert result.ok
    assert reg.get("add_sub") is not None
    assert reg.document_of("add_sub") == "doc1"
    assert reg.source("doc1") == ADD_SUB


def test_add_source_with_errors_changes_nothing() -> None:
    reg = BlockRegistry()
    reg.add_source(ADD_SUB, "doc1")
    result = reg.add_source("@block\ndef add_sub(a:\n", "doc1")
    assert not result.ok
    assert errors(result)[0][0] == 2
    assert reg.get("add_sub").outputs == ["sum", "diff"]  # type: ignore[union-attr]
    assert reg.source("doc1") == ADD_SUB


def test_edit_rematches_ports_and_reports_removed() -> None:
    reg = BlockRegistry()
    reg.add_source(ADD_SUB, "doc1")
    edited = '@block(shape="circle", outputs=["sum"])\ndef add_sub(a, c): return a + c\n'
    result = reg.add_source(edited, "doc1")
    assert result.ok
    match = result.port_changes["add_sub"]
    assert match.kept_inputs == ["a"]
    assert match.added_inputs == ["c"]
    assert match.removed_inputs == ["b"]
    assert match.removed_outputs == ["diff"]
    assert result.removed_ports == ["add_sub: input b", "add_sub: output diff"]
    assert reg.get("add_sub").inputs == ["a", "c"]  # type: ignore[union-attr]


def test_renamed_block_replaces_old_one_in_same_document() -> None:
    reg = BlockRegistry()
    reg.add_source(ADD_SUB, "doc1")
    result = reg.add_source(ADD_SUB.replace("add_sub", "plus_minus"), "doc1")
    assert result.removed_blocks == ["add_sub"]
    assert reg.get("add_sub") is None
    assert reg.get("plus_minus") is not None


def test_name_clash_with_other_document() -> None:
    reg = BlockRegistry()
    reg.add_source(ADD_SUB, "doc1")
    result = reg.add_source("\n" + ADD_SUB, "doc2")
    assert errors(result) == [(3, "another function already defines block 'add_sub'")]
    assert reg.document_of("add_sub") == "doc1"


def test_name_clash_with_builtin() -> None:
    builtin = BlockSpec("Gain", ["u"], ["y"])
    reg = BlockRegistry([builtin])
    result = reg.check_source("@block\ndef Gain(u): return u\n", "doc1")
    assert errors(result) == [(2, "a built-in block is already named 'Gain'")]
    assert reg.is_builtin("Gain")


def test_specs_order_builtins_first() -> None:
    reg = BlockRegistry([BlockSpec("Gain", ["u"], ["y"])])
    reg.add_source(ADD_SUB, "doc1")
    assert [s.name for s in reg.specs()] == ["Gain", "add_sub"]


def test_remove_document() -> None:
    reg = BlockRegistry()
    reg.add_source(ADD_SUB, "doc1")
    assert reg.remove_document("doc1") == ["add_sub"]
    assert reg.get("add_sub") is None
    assert reg.documents() == []


def test_check_source_does_not_change_registry() -> None:
    reg = BlockRegistry()
    result = reg.check_source(ADD_SUB, "doc1")
    assert result.ok
    assert reg.get("add_sub") is None


def test_match_ports() -> None:
    old = BlockSpec("f", ["a", "b"], ["y"])
    new = BlockSpec("f", ["b", "c"], ["y", "z"])
    m = match_ports(old, new)
    assert m.kept_inputs == ["b"]
    assert m.added_inputs == ["c"]
    assert m.removed_inputs == ["a"]
    assert m.added_outputs == ["z"]
    assert m.removed == ["input a"]
