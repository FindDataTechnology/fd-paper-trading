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


def _settings(**kw):
    base = dict(
        market_data_source_id="",
        market_data_provider="simulated",
        astock_dsn="",
        astock_source="astock_daily",
        astock_calendar_refs="600000,600036",
        market_data_api_key="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_unset_selects_simulated():
    sel = resolve_selection(_settings())
    assert sel.provider == PROVIDER_SIMULATED
    assert sel.deprecations == ()


def test_unknown_legacy_value_is_refused_not_silently_simulated():
    """原先未知取值会静默落到 simulated；现在必须报错点名。"""
    with pytest.raises(SourceSelectionError, match="not a known provider"):
        resolve_selection(_settings(market_data_provider="astock_v2"))


def test_legacy_astock_maps_through_registry_and_warns():
    """旧变量仍可用一个版本，但必须从注册表解析、并打弃用提示。"""
    sel = resolve_selection(
        _settings(market_data_provider="astock", astock_dsn="postgresql://ro@x/y")
    )
    assert sel.provider == PROVIDER_FEDERATED
    assert sel.source_id == "astock_daily"
    assert sel.pinned_source == "astock_daily"
    assert any("deprecated" in d for d in sel.deprecations)


def test_legacy_astock_without_dsn_is_refused():
    with pytest.raises(SourceSelectionError, match="ASTOCK_DSN"):
        resolve_selection(_settings(market_data_provider="astock"))


def test_registry_id_wins_over_legacy():
    sel = resolve_selection(
        _settings(
            market_data_source_id="astock_daily",
            market_data_provider="astock",
            astock_dsn="postgresql://ro@x/y",
        )
    )
    assert sel.source_id == "astock_daily"
    assert any("wins" in d for d in sel.deprecations)


def test_registry_id_without_dsn_is_refused():
    """federated_view 协议要 DSN；缺了不许回落。"""
    with pytest.raises(SourceSelectionError, match="ASTOCK_DSN"):
        resolve_selection(_settings(market_data_source_id="astock_daily"))


def test_unregistered_registry_id_is_refused_and_names_it():
    with pytest.raises(SourceSelectionError, match="not registered"):
        resolve_selection(_settings(market_data_source_id="astock_dailyy"))


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


def test_legacy_polygon_and_open_data_still_selectable():
    assert resolve_selection(
        _settings(market_data_provider="polygon")
    ).provider == PROVIDER_POLYGON
    assert resolve_selection(
        _settings(market_data_provider="fd-open-data")
    ).provider == PROVIDER_FD_OPEN_DATA


def test_deprecation_warning_is_emitted(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="fd_paper_trading.marketdata.selection"):
        resolve_selection(
            _settings(market_data_provider="astock", astock_dsn="postgresql://ro@x/y")
        )
    assert any("deprecated" in r.message for r in caplog.records)
