#!/usr/bin/env python3
"""同一策略、同一窗口、同一参数跑两遍回测，导出记录供 fingerprint 判据比对（change1 6.2）。

## 这条补的是什么

6.2 的两半：
- **同输入 → 统计逐位一致**（本次能验）；
- 水位前移 → 指纹变 → 标不可比（要数据真的动，本次验不了，已在单测里覆盖）。

在此之前"逐位一致"只在单测里证过——那只能说明比较函数写得对，不能说明**生产回测
本身可复现**。这里用真 DB、真 provider、真 `_run_backtest` 跑两遍，把那半补上。

## 为什么必须同参数

本轮实测踩到两次：同 universe 同窗口但**策略不同**（成交 6 vs 0）、同策略但**成本模型
不同**（final_equity 差 10.60）。两者都会让"统计不等"看起来像缺陷，其实不是。
所以这里显式固定窗口/初始资金/成本模型，并在输出里带上这些参数供比对工具核对。

用法（pod 内）：
    /app/.venv/bin/python /tmp/rerun_backtest_twice.py > /tmp/twice.json
输出：`{"before": {...}, "after": {...}}`，可直接喂
`scripts/compare_backtest_fingerprints.py --db`。

清理：脚本跑完会删掉自己造的两条 run（只留输出 JSON 作证据）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from fd_paper_trading.api.routes.strategies import _run_backtest
from fd_paper_trading.marketdata.astock import AstockMarketDataProvider
from fd_paper_trading.persistence.base import create_async_engine
from fd_paper_trading.persistence.models import (
    Strategy,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
)


def _app(session_factory, provider):
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(
        db=SimpleNamespace(session_factory=session_factory),
        market_data_provider=provider,
        settings=SimpleNamespace(strategy_poll_seconds=30.0, strategy_lookback_bars=250),
    ))


async def _make_run(session_factory, source_run, params) -> uuid.UUID:
    """照抄一条历史 run 的参数（窗口按需改写），只换 id —— 这就是"同参数"。"""
    async with session_factory() as s:
        run = StrategyRun(
            strategy_id=source_run.strategy_id,
            strategy_version=source_run.strategy_version,
            mode=StrategyRunMode.BACKTEST,
            account_id=source_run.account_id,
            status=StrategyRunStatus.QUEUED,
            params=dict(params),
        )
        s.add(run)
        await s.commit()
        return run.id


async def _record(session_factory, run_id):
    async with session_factory() as s:
        r = await s.get(StrategyRun, run_id)
        return {
            "id": str(r.id),
            "strategy_id": str(r.strategy_id),
            "strategy_version": r.strategy_version,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "params": r.params,
            "stats": r.stats,
            "error": r.error,
        }


async def main() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as s:
        rows = (await s.execute(
            select(StrategyRun)
            .where(StrategyRun.mode == StrategyRunMode.BACKTEST,
                   StrategyRun.status == StrategyRunStatus.COMPLETED)
            .order_by(StrategyRun.started_at.desc().nullslast()).limit(500)
        )).scalars().all()
        # 优先挑"单标的且该标的有数据"的：实测权重轨的 universe 里 510300/510050/159915
        # 在该源查不到行，回测会以 incomplete price data 失败——那验的是覆盖度，
        # 不是可复现性。单标的轨（signal）绕开这个缺口。
        # 回测 run 的 params 里**没有** symbol/universe（它们在策略 spec 里），
        # 所以这里要回查 spec 才能判断轨道。
        spec_by_sid = {
            row.id: (row.spec or {})
            for row in (await s.execute(text(
                "select id, spec from strategies where spec->>'kind' <> 'weight'"))).all()
        }

        def _single_symbol(r):
            spec = spec_by_sid.get(r.strategy_id) or {}
            sym = spec.get("symbol")
            return bool(sym) and str(sym).startswith("6")
        src = next((r for r in rows if _single_symbol(r)), None) or (rows[0] if rows else None)
        if src is None:
            print("✗ 找不到可借参数的历史回测", file=sys.stderr)
            return 2
        # 记下借来的参数，输出里带上——比对工具要靠它判"是不是同一实验"
        borrowed = {"strategy_id": str(src.strategy_id), "version": src.strategy_version,
                    "window": [src.params.get("start"), src.params.get("end")],
                    "cash": src.params.get("initial_cash"),
                    "cost_model": src.params.get("cost_model")}
        # 原窗口的覆盖不完整（实测 510300/510050/159915/000001 在该窗口 0 行，
        # 回测以"incomplete price data"失败）。这里改到**当前确有数据**的窗口，
        # 让两次跑都能完成——要验的是"同输入可复现"，不是那个窗口的覆盖度。
        import datetime as _dt
        end = _dt.date.today()
        start = end - _dt.timedelta(days=40)
        # 单标的轨没有 universe，别去动它
        base_params = dict(src.params or {})
        base_params["start"] = start.isoformat()
        base_params["end"] = end.isoformat()
        # universe 收敛到**确有数据**的标的：实测 510300/510050/159915 在该源里查不到行，
        # 会让回测以 "incomplete price data" 失败——那验的是覆盖度，不是可复现性。
        universe = base_params.get("universe")
        if isinstance(universe, list):
            keep = [s2 for s2 in universe if s2.startswith("6")]
            if keep:
                base_params["universe"] = keep
        borrowed["rewritten_window"] = [base_params["start"], base_params["end"]]

    provider = AstockMarketDataProvider(
        dsn=os.environ["ASTOCK_DSN"], source="astock_daily",
        registry_source_id="astock_daily")
    app = _app(sf, provider)

    ids = []
    try:
        for _ in range(2):
            rid = await _make_run(sf, src, base_params)
            ids.append(rid)
            await _run_backtest(app, rid)

        before, after = [await _record(sf, i) for i in ids]
        print(json.dumps({"borrowed_params": borrowed, "before": before, "after": after},
                         ensure_ascii=False), file=sys.stdout)
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            async with sf() as s:
                for i in ids:
                    obj = await s.get(StrategyRun, i)
                    if obj is not None:
                        await s.delete(obj)
                await s.commit()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
