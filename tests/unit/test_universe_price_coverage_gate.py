"""`scripts/check_universe_price_coverage.py` 的判据测试。

这条闸防的是 2026-11-14 那类静默缺口：库里数据在、策略 universe 引用了它、
而交易面拿不到报价，全链每一处单独看都绿。判据必须有正反两面——只有"能红"
的闸在数据正常时会误报，只有"能绿"的闸永远不会响。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "check_universe_price_coverage.py")


def _load():
    # dataclass 在 exec_module 时要能从 sys.modules 找到本模块，否则报
    # "'NoneType' object has no attribute '__dict__'"。
    name = "universe_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()
TODAY = date(2026, 11, 14)


def test_symbols_come_from_the_two_explicit_spec_slots():
    """只认 spec['symbol'] 与 spec['universe']，不做"扫全 JSON 找像代码的串"。"""
    assert gate.symbols_of_spec({"symbol": "600036"}) == {"600036"}
    assert gate.symbols_of_spec({"universe": ["510300", "000001"]}) == {
        "510300", "000001",
    }
    # 模板名/因子名不在那两处 ⇒ 不能被抓进来（否则闸变噪声）
    assert gate.symbols_of_spec({"template": "sma", "params": {"window": 20}}) == set()
    assert gate.symbols_of_spec("not-a-dict") == set()


def test_fresh_quote_is_green():
    facts = [gate.SymbolFact("000001", quote=True, last_bar="2026-11-13")]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "ok" and not v.counts


def test_default_threshold_is_the_trading_threshold():
    """闸问的是「现在能不能下单」，所以缺省阈值必须等于交易侧 `stale_quote` 那个数。

    两处各写一个数字迟早漂移，而漂移的方向恰好是「闸报绿、下单门拒单」——
    2026-11-14 就是这个形态：默认 8 天时 600000（最后一根 11-09）报 ok，
    而 `_require_fresh_quote` 已经按 4 天拒它。
    """
    from fd_paper_trading.execution.service import MAX_QUOTE_AGE_SECONDS

    assert gate.trading_max_age_days() == int(MAX_QUOTE_AGE_SECONDS // 86400) == 4


def test_a_five_day_old_bar_is_stale_under_the_default():
    """11-14 实况的具体标的一只：5 天前的那根 bar 必须计数，且 detail 指向下单门。"""
    facts = [gate.SymbolFact("600000", quote=True, last_bar="2026-11-09")]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=gate.trading_max_age_days())
    assert v.state == "stale" and v.counts
    assert "stale_quote" in v.detail


def test_holiday_safe_threshold_still_hides_that_gap():
    """保留 8 天档是为了判「通道级断供」；它确实看不见 5 天——这是取舍，不是 bug。
    把这条钉住，免得以后有人以为 8 天档也能当验收判据。"""
    facts = [gate.SymbolFact("600000", quote=True, last_bar="2026-11-09")]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "ok"


def test_old_bar_is_stale_and_counts():
    facts = [gate.SymbolFact("600036", quote=True, last_bar="2026-10-01")]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "stale" and v.counts


def test_no_quote_is_unpriceable_and_counts():
    facts = [gate.SymbolFact("510300", quote=False)]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "unpriceable" and v.counts


def test_declared_gap_is_printed_but_not_counted():
    facts = [gate.SymbolFact("007355", quote=False)]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "declared_gap" and not v.counts
    # 解除条件必须写在 detail 里——没有解除条件的豁免就是永久静音
    assert "解除条件" in v.detail


def test_a_declared_gap_without_a_declaration_still_counts():
    """把标的从表里删掉，它必须立刻回到命中——否则"入表"就是洗白开关。"""
    facts = [gate.SymbolFact("007355", quote=False)]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8, declared={})
    assert v.state == "unpriceable" and v.counts


def test_non_astock_shape_is_out_of_scope_and_not_counted():
    """AAPL 之类不归这条通道；算成缺口会让人去查一条本就不该管的链。"""
    facts = [gate.SymbolFact("AAPL", quote=False)]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "out_of_scope" and not v.counts


def test_fetch_error_is_its_own_failure_not_a_symbol_gap():
    """取数失败（库/源挂了）要能读成"通道没通"，不是"这个标的没价"。"""
    facts = [gate.SymbolFact("600519", error="RuntimeError: store down")]
    (v,) = gate.judge(facts, today=TODAY, max_age_days=8)
    assert v.state == "unpriceable" and v.counts
    assert "取数失败" in v.detail


def test_self_test_branches_all_pass():
    assert gate._self_test() == 0


@pytest.mark.parametrize("sym,ok", [
    ("600036", True), ("000001", True), ("510300", True),
    ("510300.SH", True), ("AAPL", False), ("BTC-USD", False),
])
def test_astock_symbol_shape(sym, ok):
    assert gate.is_astock_symbol(sym) is ok
