#!/usr/bin/env python3
"""在生产里验一次 `run.params` 的水位/出处留痕（change1 4.3 / 6.5 的记录侧判据）。

## 为什么需要它

provider 层的判据已经在产验过（pod 内 `source_watermark()` → `as_of=2026-11-09`,
`method=provider_max_date`）。但**记录侧**是另一件事：`_mark_running` 要把水位与
引用写进 `run.params`，而这只有在"跑过一轮"之后才有数据。生产的策略运行受
`started_at` 约束、且对外 API 需要鉴权（pod 内实测 401），所以拿不到一次新跑。

本脚本用**同一份生产代码路径**补这一次证据：真 DB、真 provider、真 `_mark_running`，
只把 `app` 这层壳换成最小替身。跑完删掉自己造的那行 run，不留垃圾。

用法（pod 内）：
    /app/.venv/bin/python /tmp/verify_run_params.py
退出码：0 = 两个判据都成立；1 = 不成立（打印实际 params）；2 = 造不出前置（无策略/无账户）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from types import SimpleNamespace

from sqlalchemy import select, text

from fd_paper_trading.marketdata.astock import AstockMarketDataProvider
from fd_paper_trading.persistence.base import create_async_engine
from fd_paper_trading.persistence.models import (
    Strategy,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
)
from fd_paper_trading.strategy.runner import PaperRunSupervisor


async def main() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # ── 借一个已有策略与账户（不自造业务对象）──────────────────────────
    async with session_factory() as s:
        row = (await s.execute(
            select(StrategyRun.strategy_id, StrategyRun.account_id,
                   StrategyRun.params)
            .where(StrategyRun.account_id.isnot(None))
            .order_by(StrategyRun.started_at.desc().nullslast()).limit(1)
        )).first()
        if row is None:
            print("✗ 找不到任何带账户的历史 run，造不出前置", file=sys.stderr)
            return 2
        strategy_id, account_id, old_params = row
        universe = (old_params or {}).get("universe")
        if not universe:
            strat = await s.get(Strategy, strategy_id)
            spec = (strat.spec or {}) if strat else {}
            sym = spec.get("symbol")
            universe = [sym] if sym else None
        if not universe:
            print("✗ 借来的策略没有 universe/symbol", file=sys.stderr)
            return 2

        run = StrategyRun(
            strategy_id=strategy_id, strategy_version=1,
            mode=StrategyRunMode.PAPER, account_id=account_id,
            status=StrategyRunStatus.STARTING,
            params={"universe": list(universe)},
            universe=list(universe),
        )
        s.add(run)
        await s.commit()
        run_id = run.id

    provider = AstockMarketDataProvider(
        dsn=os.environ["ASTOCK_DSN"], source="astock_daily",
        registry_source_id="astock_daily")

    app = SimpleNamespace(state=SimpleNamespace(
        db=SimpleNamespace(session_factory=session_factory),
        market_data_provider=provider,
        settings=SimpleNamespace(strategy_poll_seconds=30.0,
                                 strategy_lookback_bars=250),
    ))

    ok = 0
    try:
        await PaperRunSupervisor(app)._mark_running(run_id)

        async with session_factory() as s:
            params = (await s.execute(
                text("select params from strategy_runs where id = :i"), {"i": str(run_id)}
            )).scalar()

        p = params or {}
        print("params.price_* =", json.dumps(
            {k: v for k, v in p.items() if k.startswith("price_")},
            ensure_ascii=False))

        # 判据一（6.5）：水位留痕，且来路是 provider 自证
        if p.get("price_as_of_max") and p.get("price_watermark_status") == "ok":
            print(f"✓ 6.5 记录侧：price_as_of_max={p['price_as_of_max']} "
                  f"status={p['price_watermark_status']}")
            ok += 1
        else:
            print("✗ 6.5 记录侧：run.params 缺 price_as_of_max/price_watermark_status")

        # 判据二（4.3）：序列出处四字段
        ref = p.get("price_series_ref") or {}
        need = ("source_id", "adjustment", "as_of_max", "fingerprint")
        if all(ref.get(k) for k in need):
            print(f"✓ 4.3 记录侧：price_series_ref 四字段齐 "
                  f"({ref['source_id']}/{ref['adjustment']}/{ref['as_of_max']})")
            ok += 1
        else:
            print(f"✗ 4.3 记录侧：price_series_ref 不全 → "
                  f"有={sorted(k for k in need if ref.get(k))} 缺={sorted(k for k in need if not ref.get(k))} "
                  f"| unavailable={(p.get('price_series_ref_unavailable'))}")
    finally:
        # 不留垃圾：这条 run 只是取证用的
        async with session_factory() as s:
            await s.execute(text("delete from strategy_runs where id = :i"),
                            {"i": str(run_id)})
            await s.commit()
        await engine.dispose()

    return 0 if ok == 2 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
