from __future__ import annotations

import pytest

from core.blocks.decorator import block, spec_of
from core.blocks.spec import STATEFUL_COLOR, USER_COLOR, BlockSpecError


def test_add_sub_example() -> None:
    @block(shape="circle", outputs=["sum", "diff"])
    def add_sub(a, b):
        return a + b, a - b

    spec = spec_of(add_sub)
    assert spec is not None
    assert spec.name == "add_sub"
    assert spec.shape == "circle"
    assert spec.inputs == ["a", "b"]
    assert spec.outputs == ["sum", "diff"]
    assert spec.settings == {}
    assert spec.stateful is False
    assert spec.color == USER_COLOR
    # The function itself is unchanged.
    assert add_sub(3, 1) == (4, 2)


def test_bare_decorator_uses_defaults() -> None:
    @block
    def double(u):
        """Twice the input."""
        return 2 * u

    spec = spec_of(double)
    assert spec is not None
    assert spec.shape == "square"
    assert spec.outputs == ["y"]
    assert spec.doc == "Twice the input."


def test_stateful_class() -> None:
    @block(stateful=True, outputs=["x"])
    class Accumulator:
        """Running sum."""

        def __init__(self, initial=0.0):
            self.x = initial

        def output(self):
            return self.x

        def step(self, u, dt):
            self.x += u

    spec = spec_of(Accumulator)
    assert spec is not None
    assert spec.stateful is True
    assert spec.inputs == ["u"]
    assert spec.settings == {"initial": 0.0}
    assert spec.outputs == ["x"]
    assert spec.time_args == ["dt"]
    assert spec.color == STATEFUL_COLOR
    assert spec.doc == "Running sum."


def test_color_option() -> None:
    @block(color="#112233")
    def f(u):
        return u

    assert spec_of(f).color == "#112233"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"shape": "triangle"}, "shape must be one of"),
        ({"outputs": "y"}, "outputs must be a list"),
        ({"outputs": ["y", "y"]}, "duplicate output"),
        ({"outputs": ["1y"]}, "not a valid identifier"),
        ({"color": "red"}, "color must look like"),
    ],
)
def test_bad_options_raise(kwargs: dict, fragment: str) -> None:
    with pytest.raises(BlockSpecError, match=fragment):
        block(**kwargs)


def test_stateful_function_is_rejected() -> None:
    with pytest.raises(BlockSpecError, match="needs a class"):

        @block(stateful=True)
        def f(u):
            return u


def test_class_without_stateful_is_rejected() -> None:
    with pytest.raises(BlockSpecError, match="needs stateful=True"):

        @block
        class C:
            def output(self):
                return 0

            def step(self, u, dt):
                pass


def test_spec_of_plain_function_is_none() -> None:
    assert spec_of(len) is None
