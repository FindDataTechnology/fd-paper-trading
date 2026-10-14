"""AST validator accept/reject matrix (design D6, task 8.5)."""

import pytest

from fd_trade.strategy import validate_source, validate_source_strict
from fd_trade.strategy.codegen import CodeValidationError

VALID = [
    "def on_bar(ctx):\n    ctx.buy(1)",
    "def on_bar(ctx):\n    if ctx.closes()[-1] > 100:\n        ctx.buy(2)\n    else:\n        ctx.sell(1)",
    "def helper(x):\n    return x * 2\n\ndef on_bar(ctx):\n    ctx.buy(helper(1))",
    "def on_bar(ctx):\n    s = ctx.sma(10)\n    if s is not None and s > 100:\n        ctx.buy(1, limit_price=99.5)",
]

INVALID = [
    ("x = 1", "on_bar"),
    ("import os\ndef on_bar(ctx):\n    pass", "import"),
    ("from os import path\ndef on_bar(ctx):\n    pass", "from-import"),
    ("def on_bar(ctx):\n    eval('1')", "eval"),
    ("def on_bar(ctx):\n    exec('1')", "exec"),
    ("def on_bar(ctx):\n    open('x')", "open"),
    ("def on_bar(ctx):\n    compile('x', 'f', 'exec')", "compile"),
    ("def on_bar(ctx):\n    globals()", "globals"),
    ("def on_bar(ctx):\n    __import__('os')", "__import__"),
    ("def on_bar(ctx):\n    input()", "input"),
    ("def on_bar(ctx):\n    breakpoint()", "breakpoint"),
    ("def _hidden(ctx):\n    pass\ndef on_bar(ctx):\n    pass", "_hidden"),
    ("def on_bar(ctx):\n    a = __name__", "__name__"),
    ("def on_bar(ctx):\n    ctx._bars", "_bars"),
    ("def on_bar(ctx):\n    vars()", "vars"),
    ("def on_bar(ctx):\n    locals()", "locals"),
    ("def on_bar(ctx:\n    pass", "syntax error"),
]


@pytest.mark.parametrize("src", VALID)
def test_valid_sources_accepted(src):
    assert validate_source(src) == []


@pytest.mark.parametrize("src,needle", INVALID)
def test_invalid_sources_rejected(src, needle):
    errors = validate_source(src)
    assert errors, f"expected violations for: {src!r}"
    assert any(needle in e for e in errors), f"{needle!r} not in {errors}"


def test_strict_raises_with_errors():
    with pytest.raises(CodeValidationError) as exc_info:
        validate_source_strict("import os\ndef on_bar(ctx):\n    pass")
    assert "import" in exc_info.value.errors[0]


def test_strict_passes_on_valid():
    validate_source_strict("def on_bar(ctx):\n    ctx.buy(1)")
