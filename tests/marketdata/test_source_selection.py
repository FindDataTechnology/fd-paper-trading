"""源选择（add-price-supply-and-caliber tasks 3.1-3.3）。

核心断言：**选择按协议、不按源名**；**未登记即拒绝**；**包没到就说清缺什么**。
三件事都是"不许静默"的具体形态——静默回落 simulated 会让回测在假数据上跑出漂亮结果。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fd_paper_trading.marketdata.selection import (
    PROVIDER_FD_OPEN_DATA,
    PROVIDER_FEDERATED,
    PROVIDER_POLYGON,
    PROVIDER_SIMULATED,
    SourceSelectionError,
    resolve_selection,
)


@pytest.fixture(autouse=True)
def _clean_legacy_env(monkeypatch):
    """清掉开发机/CI 上可能残留的旧变量。

    本轮实测：本机 shell 里就有 MARKET_DATA_PROVIDER，会把"注册表路径"的用例
    全污染成"旧变量致失败"——看着像代码错，其实是环境脏。测试必须自己擦干净，
    别指望外部环境干净（这同理适用于任何读 os.environ 的守卫）。
    """
    for name in ("MARKET_DATA_PROVIDER", "ASTOCK_SOURCE", "ASTOCK_CALENDAR_REFS"):
        monkeypatch.delenv(name, raising=False)


def _settings(**kw):
    base = dict(
        market_data_source_id="",
        astock_dsn="",
        market_data_api_key="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_unset_selects_simulated():
    sel = resolve_selection(_settings())
    assert sel.provider == PROVIDER_SIMULATED
    assert sel.deprecations == ()


# ── 旧变量：task 6.4 起出现即启动失败（映射层已删）─────────────────────


@pytest.mark.parametrize("name", ["MARKET_DATA_PROVIDER", "ASTOCK_SOURCE", "ASTOCK_CALENDAR_REFS"])
def test_legacy_env_var_refuses_startup(name, monkeypatch):
    """旧变量还在配置里 = 错误配置，直接失败并点名——不悄悄按旧方式跑。

    为什么必须失败而不是忽略：忽略会让"我改过了"与"其实没生效"在观测上完全一样，
    而那正是这个 change 要消灭的形态。
    """
    monkeypatch.setenv(name, "astock")
    with pytest.raises(SourceSelectionError, match=name):
        resolve_selection(_settings(market_data_source_id="astock_daily"))


def test_legacy_env_refusal_names_the_removal_and_the_fix(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "astock")
    with pytest.raises(SourceSelectionError) as ei:
        resolve_selection(_settings())
    msg = str(ei.value)
    assert "task 6.4" in msg and "MARKET_DATA_SOURCE_ID" in msg


def test_astock_dsn_is_not_a_legacy_variable(monkeypatch):
    """ASTOCK_DSN 不在此列：它是 federated_view 声明的凭据槽，照旧注入。"""
    monkeypatch.setenv("ASTOCK_DSN", "postgresql://ro@x/y")
    sel = resolve_selection(_settings())
    assert sel.provider == PROVIDER_SIMULATED  # 没有 SOURCE_ID → simulated


# ── 注册表 id：唯一选择器 ─────────────────────────────────────────────


def test_registry_id_without_dsn_is_refused():
    """federated_view 协议要 DSN；缺了不许回落。"""
    with pytest.raises(SourceSelectionError, match="ASTOCK_DSN"):
        resolve_selection(_settings(market_data_source_id="astock_daily"))


def test_unregistered_registry_id_is_refused_and_names_it():
    with pytest.raises(SourceSelectionError, match="ASTOCK_DSN|not registered"):
        resolve_selection(
            _settings(market_data_source_id="astock_dailyy",
                      astock_dsn="postgresql://ro@x/y")
        )


def test_registry_id_selects_by_access_kind_not_source_name():
    """同一个 id 换协议就换实现——这不依赖任何源名字面量。"""
    sel = resolve_selection(
        _settings(
            market_data_source_id="index-seed",
            astock_dsn="postgresql://ro@x/y",
        )
    )
    assert sel.provider == PROVIDER_FEDERATED
    assert sel.source_id == "index-seed"


def test_calendar_refs_come_from_the_declaration_not_env(monkeypatch):
    """参考篮只来自声明（watermark_probe.reference_basket），不再读环境变量。"""
    monkeypatch.setenv("ASTOCK_DSN", "postgresql://ro@x/y")
    sel = resolve_selection(_settings(market_data_source_id="astock_daily"))
    assert sel.calendar_refs  # 声明里带了篮
    assert "600036" in sel.calendar_refs
