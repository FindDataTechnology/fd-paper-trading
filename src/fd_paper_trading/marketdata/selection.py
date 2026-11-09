"""行情源选择：按**注册表**解析，不按源名硬编码。

## 为什么单独成模块

`build_market_data_provider` 里原先是一串 ``if settings.market_data_provider == "astock"``
——源名既是数据身份又是实现选择器。后果是换源等于改代码，而"换源"正是这个 change 要
做的最基本的事。

新形状：注册表说"这个源用什么**协议**访问"（``access.kind``），本模块把协议映射到
provider 类。协议是取数方式，源名是数据身份，两者分开后换源只改声明。

## 包没到怎么办：明确拒绝，不回落

``fd_open_data_mcp.price_sources`` 随 fd-open-data-mcp 发版才可用；本仓按冻结 lockfile
装已发布版本。所以在包还没到位的窗口里，``MARKET_DATA_SOURCE_ID`` 一旦被设置就必须
**明确报错说清缺什么**——绝不静默退回 simulated（那会让回测在假数据上跑出漂亮结果）。

## 旧变量的映射只有一个版本的生命期

``MARKET_DATA_PROVIDER`` / ``ASTOCK_*`` 保留一个版本的兼容映射，并在启动日志里打弃用提示。
一个版本的窗口过后（change task 6.4）就删掉——弃用窗口不该永久存在。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 旧 provider 名 -> 注册表中的 source_id。只是迁移期映射，不是第二事实源：
#: 值必须在注册表里存在，否则报错。
LEGACY_PROVIDER_MAP = {
    "astock": "astock_daily",
    "fd-open-data": None,   # 本体读面，不是联邦视图源；无对应 source_id
    "polygon": None,        # 外部 API，无对应 source_id
    "simulated": None,
}

PROVIDER_SIMULATED = "simulated"
PROVIDER_POLYGON = "polygon"
PROVIDER_FD_OPEN_DATA = "fd-open-data"
PROVIDER_FEDERATED = "federated-view"


class SourceSelectionError(RuntimeError):
    """源选择不可成立。单独成类型，便于启动期把它与配置缺失区分开。"""


@dataclass(frozen=True)
class Selection:
    """一次解析的结果：用哪个 provider、读哪个源、拿什么凭据。"""

    provider: str
    source_id: str | None = None
    db_source: str | None = None
    dsn: str = ""
    calendar_refs: str = ""
    deprecations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pinned_source(self) -> str | None:
        """联邦视图里钉住的 ``source_used``。"""
        return self.db_source


def _load_registry():
    """延迟导入注册表。包版本没到就给出可操作的错误，而不是 ImportError 栈。"""
    try:
        from fd_open_data_mcp.price_sources import (  # type: ignore[import-not-found]
            SourceNotRegistered,
            load_registry,
        )
    except ImportError as exc:  # pragma: no cover - 只在包未发版时发生
        raise SourceSelectionError(
            "MARKET_DATA_SOURCE_ID requires fd-open-data-mcp with the "
            "price_sources registry (change add-price-supply-and-caliber). "
            "The installed fd-open-data-mcp does not provide it yet; publish "
            "that package or unset MARKET_DATA_SOURCE_ID. Refusing to fall "
            "back to another provider."
        ) from exc
    return load_registry, SourceNotRegistered


def _registry_access(source_id: str):
    load_registry, SourceNotRegistered = _load_registry()
    reg = load_registry()
    try:
        decl = reg.get(source_id)
    except SourceNotRegistered as exc:
        raise SourceSelectionError(
            f"MARKET_DATA_SOURCE_ID={source_id!r} is not registered: {exc}. "
            "Register it in fd-open-data-mcp's price_sources/declarations.yaml "
            "or fix the id; refusing to fall back to another provider."
        ) from exc
    return decl


def _dsn_for(settings: Any, dsn_env: str | None) -> str:
    """按声明里的 ``dsn_env`` 取只读 DSN。

    先查 settings 的同名属性（pydantic Settings 已经读过 ``.env`` / 环境），再退到
    ``os.environ``。只读 ``os.environ`` 会绕过 Settings 的 ``.env`` 加载，表现为
    "明明配了却说没配"——所以两条路都走，且以 Settings 为准。
    """
    if not dsn_env:
        return ""
    attr = dsn_env.lower()
    value = getattr(settings, attr, "") or ""
    if value:
        return str(value)
    import os

    return os.environ.get(dsn_env, "") or ""


def _provider_for_access(kind: str) -> str:
    """协议 -> provider 类。这里只有协议名，没有源名（task 3.3）。"""
    if kind == "federated_view":
        return PROVIDER_FEDERATED
    if kind == "fd_open_data_ontology":
        return PROVIDER_FD_OPEN_DATA
    if kind == "polygon":
        return PROVIDER_POLYGON
    if kind == "simulated":
        return PROVIDER_SIMULATED
    raise SourceSelectionError(
        f"unsupported access.kind={kind!r}; the trading engine knows "
        f"{PROVIDER_FEDERATED}/{PROVIDER_FD_OPEN_DATA}/{PROVIDER_POLYGON}/{PROVIDER_SIMULATED}"
    )


def resolve_selection(settings: Any) -> Selection:
    """从 settings 解析出一次选择。

    优先级：``MARKET_DATA_SOURCE_ID``（注册表）> 旧 ``MARKET_DATA_PROVIDER`` > simulated。
    """
    source_id = (getattr(settings, "market_data_source_id", "") or "").strip()
    legacy = (getattr(settings, "market_data_provider", "") or "").strip()
    deprecations: list[str] = []

    if source_id:
        if legacy and legacy not in ("", "simulated"):
            deprecations.append(
                f"both MARKET_DATA_SOURCE_ID={source_id!r} and legacy "
                f"MARKET_DATA_PROVIDER={legacy!r} are set; the registry id wins "
                "and the legacy variable is deprecated"
            )
        decl = _registry_access(source_id)
        provider = _provider_for_access(decl.access.kind)
        dsn = _dsn_for(settings, decl.access.dsn_env)
        if provider == PROVIDER_FEDERATED and not dsn:
            raise SourceSelectionError(
                f"source {source_id!r} uses access.kind=federated_view and needs "
                f"{decl.access.dsn_env} (read-only DSN); refusing to fall through "
                "to another provider"
            )
        deprecations.append(
            "MARKET_DATA_PROVIDER / ASTOCK_* are deprecated in favour of "
            "MARKET_DATA_SOURCE_ID; the compatibility mapping is removed next version"
        )
        for note in deprecations:
            logger.warning("[market-data] %s", note)
        return Selection(
            provider=provider,
            source_id=source_id,
            db_source=decl.db_source,
            dsn=dsn,
            calendar_refs=getattr(settings, "astock_calendar_refs", "") or "",
            deprecations=tuple(deprecations),
        )

    # ---- 迁移期：旧变量 ----
    if legacy and legacy not in ("simulated", ""):
        mapped = LEGACY_PROVIDER_MAP.get(legacy, "unmapped")
        if mapped == "unmapped":
            # 未知取值原先会静默落到 simulated —— 这正是要在本次消灭的形态。
            raise SourceSelectionError(
                f"MARKET_DATA_PROVIDER={legacy!r} is not a known provider "
                f"(known: {', '.join(sorted(LEGACY_PROVIDER_MAP))}) and has no "
                "registry source_id; set MARKET_DATA_SOURCE_ID instead of relying "
                "on a silent fallthrough to simulated"
            )
        if mapped is None:
            deprecations.append(
                f"MARKET_DATA_PROVIDER={legacy!r} is deprecated; set "
                "MARKET_DATA_SOURCE_ID once that source is registered"
            )
            for note in deprecations:
                logger.warning("[market-data] %s", note)
            if legacy == "polygon":
                return Selection(
                    provider=PROVIDER_POLYGON,
                    deprecations=tuple(deprecations),
                )
            if legacy == "fd-open-data":
                return Selection(
                    provider=PROVIDER_FD_OPEN_DATA,
                    deprecations=tuple(deprecations),
                )
        else:
            decl = _registry_access(mapped)
            dsn = _dsn_for(settings, decl.access.dsn_env)
            if _provider_for_access(decl.access.kind) == PROVIDER_FEDERATED and not dsn:
                # 旧代码在这里有一道守卫（MARKET_DATA_PROVIDER=astock 且无 DSN 即启动失败）；
                # 迁移期必须保留同样的行为，否则"选了源但没有凭据"会静默退化成别的 provider。
                raise SourceSelectionError(
                    f"MARKET_DATA_PROVIDER={legacy!r} resolves to {mapped!r}, which uses "
                    f"access.kind=federated_view and needs {decl.access.dsn_env} "
                    "(read-only DSN); refusing to fall through to another provider"
                )
            deprecations.append(
                f"MARKET_DATA_PROVIDER={legacy!r} is deprecated; use "
                f"MARKET_DATA_SOURCE_ID={mapped!r} (resolved from the registry)"
            )
            for note in deprecations:
                logger.warning("[market-data] %s", note)
            return Selection(
                provider=_provider_for_access(decl.access.kind),
                source_id=mapped,
                db_source=decl.db_source,
                dsn=dsn,
                calendar_refs=getattr(settings, "astock_calendar_refs", "") or "",
                deprecations=tuple(deprecations),
            )

    return Selection(provider=PROVIDER_SIMULATED)
