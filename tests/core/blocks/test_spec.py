from __future__ import annotations

import pytest

from core.blocks.spec import (
    CONSERVING,
    SIGNAL,
    BlockOptions,
    BlockSpec,
    BlockSpecError,
    Param,
    PortSpec,
    class_spec,
    function_spec,
    is_plain_constant,
    spec_from_callable,
)


def test_parameters_split_into_inputs_settings_and_time() -> None:
    def f(u, v, t, gain=2.0, *, label="x"):
        return u

    spec = spec_from_callable(f, BlockOptions())
    assert spec.inputs == ["u", "v"]
    assert spec.settings == {"gain": 2.0, "label": "x"}
    assert spec.time_args == ["t"]


def test_varargs_rejected_with_param_name() -> None:
    def f(*us):
        return 0

    with pytest.raises(BlockSpecError) as err:
        spec_from_callable(f, BlockOptions())
    assert err.value.param == "us"


def test_varkw_and_posonly_rejected() -> None:
    def g(**kw):
        return 0

    def h(a, /):
        return a

    with pytest.raises(BlockSpecError, match=r"\*\*kw"):
        spec_from_callable(g, BlockOptions())
    with pytest.raises(BlockSpecError, match="positional-only"):
        spec_from_callable(h, BlockOptions())


def test_non_constant_default_rejected() -> None:
    def f(u, k=object()):  # noqa: B008
        return u

    with pytest.raises(BlockSpecError, match="plain constant") as err:
        spec_from_callable(f, BlockOptions())
    assert err.value.param == "k"


def test_plain_constants() -> None:
    assert is_plain_constant(1.5)
    assert is_plain_constant("++")
    assert is_plain_constant(None)
    assert is_plain_constant([1, 2.0, "a"])
    assert not is_plain_constant({"a": 1})
    assert not is_plain_constant(1j)
    assert not is_plain_constant([[1]])


def test_docstring_is_cleaned() -> None:
    def f(u):
        """First line.

        More text.
        """
        return u

    assert spec_from_callable(f, BlockOptions()).doc == "First line.\n\nMore text."


def test_class_spec_needs_step_and_output() -> None:
    opts = BlockOptions(stateful=True)
    with pytest.raises(BlockSpecError, match="step"):
        class_spec("C", [], None, True, opts)
    with pytest.raises(BlockSpecError, match="output"):
        class_spec("C", [], [Param("u")], False, opts)


def test_class_init_params_need_defaults() -> None:
    opts = BlockOptions(stateful=True)
    with pytest.raises(BlockSpecError, match="needs a default") as err:
        class_spec("C", [Param("k")], [Param("u")], True, opts)
    assert err.value.param == "k"


def test_class_step_params_cannot_have_defaults() -> None:
    opts = BlockOptions(stateful=True)
    with pytest.raises(BlockSpecError, match="put settings in __init__"):
        class_spec("C", [], [Param("u"), Param("k", 1.0)], True, opts)


def test_class_without_own_init_has_no_settings() -> None:
    class C:
        def output(self):
            return 0

        def step(self, u, dt):
            pass

    spec = spec_from_callable(C, BlockOptions(stateful=True))
    assert spec.settings == {}
    assert spec.inputs == ["u"]


def test_function_spec_from_params() -> None:
    spec = function_spec("f", [Param("a"), Param("k", 1)], BlockOptions(shape="rectangle"))
    assert spec.inputs == ["a"]
    assert spec.settings == {"k": 1}
    assert spec.shape == "rectangle"


# ---- F2: port kinds


def test_v01_blocks_have_only_signal_ports() -> None:
    def f(u, gain=2.0):
        return u * gain

    spec = spec_from_callable(f, BlockOptions(outputs=["y"]))
    assert spec.conserving == []
    assert not spec.is_physical
    assert spec.port("u") == PortSpec("u", SIGNAL)
    assert spec.port("y") == PortSpec("y")
    assert spec.port("gain") is None
    assert spec.port("nope") is None


def test_all_builtin_library_blocks_load_unchanged() -> None:
    from core.blocks.library import builtin_specs

    specs = builtin_specs()
    assert specs
    for spec in specs:
        assert spec.conserving == []
        assert all(spec.port(p) == PortSpec(p) for p in spec.inputs + spec.outputs)


def test_conserving_port_needs_known_domain() -> None:
    port = PortSpec("shaft", CONSERVING, "rotational")
    assert port.is_conserving
    with pytest.raises(BlockSpecError, match="needs a domain"):
        PortSpec("shaft", CONSERVING)
    with pytest.raises(BlockSpecError, match="needs a domain") as err:
        PortSpec("pipe", CONSERVING, "hydraulic")
    assert err.value.param == "pipe"


def test_signal_port_cannot_have_domain_and_kind_is_checked() -> None:
    with pytest.raises(BlockSpecError, match="cannot have a domain"):
        PortSpec("u", SIGNAL, "electrical")
    with pytest.raises(BlockSpecError, match="kind must be one of"):
        PortSpec("u", "magnetic")
    with pytest.raises(BlockSpecError, match="identifier"):
        PortSpec("1x", CONSERVING, "thermal")


def test_block_with_conserving_ports() -> None:
    spec = BlockSpec(
        name="motor",
        inputs=["V"],
        outputs=["w_meas"],
        conserving=[
            PortSpec("shaft", CONSERVING, "rotational"),
            PortSpec("case", CONSERVING, "thermal"),
        ],
    )
    assert spec.is_physical
    assert spec.port("shaft") == PortSpec("shaft", CONSERVING, "rotational")
    assert spec.port("V") == PortSpec("V")


def test_conserving_port_names_must_be_unique() -> None:
    a = PortSpec("p", CONSERVING, "electrical")
    with pytest.raises(BlockSpecError, match="used twice"):
        BlockSpec("r", [], [], conserving=[a, PortSpec("p", CONSERVING, "electrical")])
    with pytest.raises(BlockSpecError, match="used twice"):
        BlockSpec("r", ["p"], [], conserving=[a])
    with pytest.raises(BlockSpecError, match="not a conserving port"):
        BlockSpec("r", [], [], conserving=[PortSpec("u")])
