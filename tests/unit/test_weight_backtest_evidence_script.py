"""The evidence script's config surface: `FACTOR_WEIGHT_SCHEME` / `FACTOR_SCORES`.

Why test a script's argument parsing: this is the acceptance-evidence entry point
for `factor-real-cross-section` item 3, and the failure mode it must not have is
the quiet one — an unrecognized scheme falling through to equal weight and then
being filed under "score-weighted evidence". So a bad config must raise, and a
malformed score entry must raise rather than silently shrink the universe.

The two helpers are imported by path because the file is a script, not a module.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "weight_backtest_topN.py"


def _load():
    spec = importlib.util.spec_from_file_location("weight_backtest_topN", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load()


def test_scheme_defaults_to_equal(script) -> None:
    assert script.resolve_scheme(None) == "equal"
    assert script.resolve_scheme("") == "equal"
    assert script.resolve_scheme("  Equal  ") == "equal"


def test_score_scheme_is_accepted_case_insensitively(script) -> None:
    assert script.resolve_scheme("SCORE") == "score"


def test_unknown_scheme_raises_instead_of_falling_back(script) -> None:
    """静默回退到等权 = 把等权证据冒充打分证据。必须炸。"""
    with pytest.raises(ValueError, match="FACTOR_WEIGHT_SCHEME"):
        script.resolve_scheme("scores")


def test_scores_parse_into_a_mapping(script) -> None:
    parsed = script.parse_scores("000908:0.91, 002553:0.77 ,001221:0.5")
    assert parsed == {"000908": 0.91, "002553": 0.77, "001221": 0.5}


def test_entry_without_a_score_raises(script) -> None:
    """少一个名字就是少一个标的——不能悄悄跳过。"""
    with pytest.raises(ValueError, match="code:score"):
        script.parse_scores("000908")


def test_non_numeric_score_raises(script) -> None:
    with pytest.raises(ValueError, match="不是数"):
        script.parse_scores("000908:abc")


def test_empty_scores_is_an_empty_mapping(script) -> None:
    assert script.parse_scores("") == {}
    assert script.parse_scores(None) == {}


# ---- 2026-11-13：真跑撞出的"分数是超集"形态 ----------------------------------
#
# 真库里 top-30 篮子 + 引擎只取有 bar 的前 10 只，把整份 FACTOR_SCORES 传给
# `score_proportional_weights` 会抛 `weights outside universe`——分配器是对的
# （不裁等于给不在册标的配权），错在调用点。`build_weights` 把裁剪与缺分数检查
# 收在一处；这三条钉住的就是那个形态，免得下次又把"超集"当成"配置错"。


def test_score_weights_are_restricted_to_the_universe(script) -> None:
    universe = [f"6000{i:02d}" for i in range(10)]
    scores = {c: float(i + 1) for i, c in enumerate(universe)}
    scores["999999"] = 100.0          # 不在 universe 里的分数（top-30 篮子的常态）
    weights = script.build_weights("score", scores, universe)
    assert set(weights) == set(universe), "超集分数不能给不在册标的配权"
    assert "999999" not in weights
    assert sum(weights.values()) == pytest.approx(0.9)


def test_score_weights_name_the_missing_names(script) -> None:
    universe = ["600000", "600001"]
    with pytest.raises(ValueError) as ei:
        script.build_weights("score", {"600000": 1.0}, universe)
    assert "600001" in str(ei.value), "缺哪个标的要能读出来"


def test_equal_weights_ignore_scores_entirely(script) -> None:
    universe = ["600000", "600001", "600002", "600003", "600004"]
    weights = script.build_weights("equal", {"600009": 5.0}, universe)
    assert set(weights) == set(universe)
    assert set(weights.values()) == {0.18}
