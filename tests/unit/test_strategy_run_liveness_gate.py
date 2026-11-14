"""`scripts/check_strategy_run_liveness.py` 的判据测试。

这条闸防的是 2026-11-10 起那四天：3 条 `RUNNING` 的 run 租约一直在续、`error=NULL`、
pod ready、`/health` 200，而 `params.last_bar_date` 停在 11-09 一动不动——因为
runner 每个 tick 都「拿到最后一根 bar，与上次一样就 return」，数据不再前进就永远早退。
没有订单、没有跳过记录、没有 error、没有告警。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "check_strategy_run_liveness.py")


def _load():
    name = "run_liveness_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()
TODAY = date(2026, 11, 14)


def _f(rid="a1", strategy="s", symbol="600000", processed="2026-11-13",
       latest="2026-11-13", error=None):
    return gate.RunFact(rid, strategy, symbol, processed, latest, error)


def _one(fact, **kw):
    (v,) = gate.judge([fact], today=kw.pop("today", TODAY),
                      max_age_days=kw.pop("max_age_days", 4), **kw)
    return v


def test_caught_up_run_is_ok():
    assert _one(_f()).state == "ok"


def test_newer_bars_in_the_store_mean_the_runner_is_stuck():
    """库里已经有 11-13 的 bar，run 停在 11-06 ⇒ runner 没推进；重启 runner 能修。"""
    v = _one(_f(processed="2026-11-06", latest="2026-11-13"))
    assert v.state == "runner_lag" and v.counts
    assert "runner 没在推进" in v.detail


def test_processing_the_latest_but_old_bar_means_the_data_stopped():
    """处理到的就是最新 bar 且已 5 天 ⇒ 数据停了；重启 runner 是错的方向。"""
    v = _one(_f(processed="2026-11-09", latest="2026-11-09"))
    assert v.state == "data_stalled" and v.counts
    assert "数据停了" in v.detail and "重启 runner 没有用" in v.detail


def test_the_two_causes_never_share_a_verdict():
    """这两档必须分开：处置方向相反。混成一句「策略没在交易」会让人重启一个健康的 runner。"""
    lag = _one(_f(processed="2026-11-06", latest="2026-11-13"))
    stalled = _one(_f(processed="2026-11-09", latest="2026-11-09"))
    assert lag.state != stalled.state


def test_no_symbol_is_undetermined_not_a_hit():
    v = _one(_f(symbol=None))
    assert v.state == "undetermined" and not v.counts


def test_missing_last_bar_is_undetermined():
    v = _one(_f(processed=None))
    assert v.state == "undetermined" and not v.counts


def test_store_has_no_bars_is_undetermined():
    v = _one(_f(latest=None))
    assert v.state == "undetermined" and not v.counts


def test_fetch_error_is_undetermined_with_the_error():
    v = _one(_f(error="RuntimeError: store down"))
    assert v.state == "undetermined" and "store down" in v.detail


def test_primary_symbol_prefers_the_single_symbol_shape():
    assert gate._primary_symbol({"symbol": "600000"}, {}) == "600000"
    assert gate._primary_symbol({"universe": ["510300", "000001"]}, {}) == "510300"
    # 权重轨的判定轴是 params.universe 的第一个（与 runner 的 primary_bars 一致）
    assert gate._primary_symbol({}, {"universe": ["159915"]}) == "159915"
    assert gate._primary_symbol({}, {}) is None


def test_default_threshold_is_the_trading_threshold():
    from fd_paper_trading.execution.service import MAX_QUOTE_AGE_SECONDS

    assert gate.trading_max_age_days() == int(MAX_QUOTE_AGE_SECONDS // 86400) == 4


def test_inject_both_causes_go_red():
    """报告路径的红证：在真实取到的 run 上注入，走同一套判据。"""
    facts = [_f("deadbeef", "batch6-c2-sma")]
    notes = gate._inject(facts, lag="dead", stalled=None, today=TODAY)
    assert notes and "已注入" in notes[0]
    assert _one(facts[0]).state == "runner_lag"

    facts2 = [_f("deadbeef", "batch6-c2-sma")]
    gate._inject(facts2, lag=None, stalled="dead", today=TODAY)
    assert _one(facts2[0], max_age_days=4).state == "data_stalled"


def test_inject_on_an_unknown_target_says_so():
    notes = gate._inject([_f("deadbeef")], lag="nosuch", stalled=None, today=TODAY)
    assert any("没有匹配" in n for n in notes)


def test_self_test_passes():
    assert gate._self_test() == 0
