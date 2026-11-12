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
