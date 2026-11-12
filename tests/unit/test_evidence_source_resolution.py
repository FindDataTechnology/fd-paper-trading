"""证据取源器：证据必须来自真源，解析成 simulated 就必须拒绝。

这条规则是在 2026-11-12 补的：`AstockMarketDataProvider` 的 `source` 改成必填后，
两个证据脚本仍 `AstockMarketDataProvider(dsn=dsn)`，**拿到 DSN 也会先抛 ValueError**。
更坏的一种情形是本机 `MARKET_DATA_SOURCE_ID` 为空——解析器会安静地给出 `simulated`，
于是"跑通了"可能只是模拟源跑通了。证据脚本一律走 `open_provider()`，这里把两种拒绝钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from _evidence_source import EvidenceSourceError, open_provider  # noqa: E402

FAKE_DSN_VAR = "ASTOCK_DSN"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from fd_paper_trading.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_refuses_simulated_when_source_id_is_empty(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_SOURCE_ID", raising=False)
    with pytest.raises(EvidenceSourceError) as ei:
        open_provider()
    msg = str(ei.value)
    assert "simulated" in msg, "必须点名解析结果，否则读者会以为只是缺凭据"
    assert "MARKET_DATA_SOURCE_ID" in msg, "错误要给出可执行的下一步"


def test_names_the_credential_slot_when_dsn_missing(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE_ID", "astock_daily")
    monkeypatch.delenv(FAKE_DSN_VAR, raising=False)
    monkeypatch.delenv("FD_OPEN_DATA_DSN", raising=False)
    with pytest.raises(EvidenceSourceError) as ei:
        open_provider()
    assert FAKE_DSN_VAR in str(ei.value), "要说清缺的是哪个槽，不是一个泛泛的'缺 DSN'"


def test_builds_provider_with_pinned_source(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE_ID", "astock_daily")
    monkeypatch.setenv(FAKE_DSN_VAR, "postgresql://u:p@127.0.0.1:1/none")
    provider = open_provider()
    assert provider._source == "astock_daily", "联邦视图钉住的 source_used 必须是注册表里的 db_source"
    assert provider._registry_source_id == "astock_daily"


def test_other_dsn_names_do_not_stand_in_for_the_declared_slot(monkeypatch):
    """别名不算数：只有注册表声明的那一个名字能提供 DSN。

    这不是洁癖——`FD_OPEN_DATA_DSN` / `ASTOCK_DATABASE_URL` 这类旁路
    曾让"该配哪个变量"变成猜谜（2026-11-12 三个名字各试一次才排除）。
    手上是别的名字，转一行即可，不必在代码里留旁路。
    """
    monkeypatch.setenv("MARKET_DATA_SOURCE_ID", "astock_daily")
    monkeypatch.delenv(FAKE_DSN_VAR, raising=False)
    monkeypatch.setenv("FD_OPEN_DATA_DSN", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("ASTOCK_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    with pytest.raises(EvidenceSourceError) as ei:
        open_provider()
    assert FAKE_DSN_VAR in str(ei.value)
