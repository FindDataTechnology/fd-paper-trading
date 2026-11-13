"""AST validator accept/reject matrix (design D6, task 8.5)."""

import pytest

from fd_paper_trading.strategy import validate_source, validate_source_strict
from fd_paper_trading.strategy.codegen import CodeValidationError

VALID = [
    "def on_bar(ctx):\n    ctx.buy(1)",
    "def on_bar(ctx):\n    if ctx.closes()[-1] > 100:\n        ctx.buy(2)\n    else:\n        ctx.sell(1)",
    "def helper(x):\n    return x * 2\n\ndef on_bar(ctx):\n    ctx.buy(helper(1))",
    "def on_bar(ctx):\n    s = ctx.sma(10)\n    if s is not None and s > 100:\n        ctx.buy(1, limit_price=99.5)",
    # 正当的格式化：只有占位符/序号/精度，不带属性或下标
    'def on_bar(ctx):\n    return "{}".format(ctx.cash)',
    'def on_bar(ctx):\n    return "{:.2f}".format(ctx.cash)',
    'def on_bar(ctx):\n    return f"{ctx.cash:.2f}"',
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
    # 2026-11-13：dunder 躲在 str.format 的字段说明符里逃过 AST 的 Attribute 检查。
    # 实测这两条**曾经通过校验**，其中第一条运行时真的返回了 'builtins'。
    ('def on_bar(ctx):\n    return "{0.__class__.__module__}".format(ctx)', "format field"),
    ('def on_bar(ctx):\n    return "{0[__globals__]}".format(ctx)', "format field"),
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


def test_dunder_through_str_format_is_actually_reachable_without_the_check():
    """闸的自证：这段源码在**关掉新判据**时确实能拿到模块名。

    只断言"校验器拒了它"是不够的——如果这条 payload 本身无害，那"拒"就是多余的。
    这里在 SAFE_BUILTINS 下真跑一遍，证明它绕过了「属性不许以下划线开头」这条规则：
    AST 只看到一个字符串常量，`__class__` 是运行时由 str.format 解析的。
    """
    from fd_paper_trading.strategy.codegen import SAFE_BUILTINS

    src = 'def on_bar(ctx):\n    return "{0.__class__.__module__}".format(ctx)\n'
    ns: dict = {"__builtins__": SAFE_BUILTINS}
    exec(compile(src, "<probe>", "exec"), ns)   # noqa: S102 - 自证用途
    assert ns["on_bar"](None) == "builtins", "payload 没拿到属性 ⇒ 这条判据是多余的"


# ---- 第二层：受限 builtins 是"静态判据被绕过时"的唯一防线 --------------------
#
# 2026-11-13 量过一圈：`getattr` **能通过静态校验**（它不以 `_` 开头、也不在
# _FORBIDDEN_CALLS 里），但运行时立刻 `NameError`——因为 SAFE_BUILTINS 里没有它。
# 也就是说 `"".format` 是唯一一条"静态判据是唯一防线"的通道（`str` 是安全内建，
# 第二层不设防），所以它才被单独修掉；`getattr` 一族靠第二层兜住。
#
# 这层防线没有任何测试：谁哪天为了"方便"把 `getattr`/`type`/`vars` 加进
# SAFE_BUILTINS，沙箱当场变成可逃逸的，而所有既有用例照样全绿。这条钉住它。

_FORBIDDEN_IN_SAFE_BUILTINS = (
    "getattr", "setattr", "delattr", "hasattr", "vars", "globals", "locals",
    "eval", "exec", "compile", "open", "input", "breakpoint", "__import__",
    "type", "super", "object", "dir", "help",
)


def test_safe_builtins_expose_no_attribute_access_or_io():
    """受限 builtins 里不许出现能取属性、能 IO、能反射的内建。"""
    from fd_paper_trading.strategy.codegen import SAFE_BUILTINS

    leaked = [n for n in _FORBIDDEN_IN_SAFE_BUILTINS if n in SAFE_BUILTINS]
    assert not leaked, (
        f"SAFE_BUILTINS 里出现了 {leaked} —— 这些能让策略代码绕过静态判据："
        "getattr(x, '__class__') 之类不以 '_' 开头的调用不会被 AST 规则拦住，"
        "第二层是唯一防线。"
    )


def test_getattr_passes_static_validation_so_layer_two_must_hold():
    """自证：`getattr(ctx, "_bars")` 静态校验**会放行**——所以第二层必须挡。

    这条不是"应该被静态拦"，而是记录事实：静态规则管不到运行期拼出来的名字。
    如果哪天静态判据也能拦它，这条会红，那就该把注释和这条一起更新（而不是删掉它）。
    """
    assert validate_source('def on_bar(ctx):\n    return getattr(ctx, "_bars")\n') == []

    from fd_paper_trading.strategy.codegen import SAFE_BUILTINS

    ns: dict = {"__builtins__": SAFE_BUILTINS}
    src = 'def on_bar(ctx):\n    return getattr(ctx, "_bars")\n'
    exec(compile(src, "<probe>", "exec"), ns)   # noqa: S102 - 自证用途
    with pytest.raises(NameError):
        ns["on_bar"](None)
