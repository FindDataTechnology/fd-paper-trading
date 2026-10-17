"""Strategy CRUD, template catalog, and backtest endpoints (/api/v1/*).

Backtests run as a background task on the app event loop: the run row is
created QUEUED with the strategy version pinned, then transitions
RUNNING -> COMPLETED | FAILED. Group limits (max_backtest_days,
allow_custom_code, allowed_symbols) are enforced here; the defaults in
``DEFAULT_GROUP_CONFIG`` apply when a key is missing.
"""

import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user, get_session
from ...persistence.models import (
    ACTIVE_RUN_STATUSES,
    Strategy,
    StrategyEquityPoint,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
    User,
    group_config,
)
from ...persistence.repositories import (
    AccountRepository,
    StrategyRepository,
    StrategyRunRepository,
)
from ...strategy import (
    CodeSpec,
    ComputePlaneUnavailable,
    CostModel,
    EvaluationError,
    SpecValidationError,
    TemplateSpec,
    WeightSpec,
    build_evaluator,
    build_weight_evaluator,
    expand_grid,
    extended_stats,
    grid_size,
    replay,
    replay_weights,
    run_walk_forward,
    run_walk_forward_weight,
    spec_summary,
    validate_spec,
)
from ...strategy.benchmarks import (
    DEFAULT_BENCHMARKS,
    BenchmarkResult,
    benchmark_stats,
    fetch_benchmark,
    relative_metrics,
)
from ...strategy.compute_plane import validate_no_lookahead as _validate_no_lookahead
from ...strategy.templates import list_templates, weight_track_metadata
from ..errors import APIError
from ..schemas import (
    BacktestRequest,
    PaperRunRequest,
    StrategyCreate,
    StrategyOut,
    StrategyRunDetailOut,
    StrategyRunOut,
    StrategyCatalogOut,
    StrategyTemplateOut,
    StrategyUpdate,
)

router = APIRouter(tags=["strategies"])

#: 扫描组合数上限（design D6：笛卡尔积的风险是量级性的）
DEFAULT_PARAM_GRID_LIMIT = 50


def _validate_spec_for_user(raw: object, user: User):
    """Validate a spec dict and enforce the user's group gates."""
    try:
        spec = validate_spec(raw)
    except SpecValidationError as exc:
        raise APIError(
            "invalid_spec",
            "Invalid strategy spec",
            422,
            fields=[{"loc": ["spec"], "message": e} for e in exc.errors],
        ) from exc
    # A weight spec with ``source`` runs user Python in the same sandbox as the
    # code track, so it passes the same gate: gating only ``CodeSpec`` would let
    # an author run arbitrary code by relabelling the kind, and the sandbox
    # requirement is about the code, not about which kind carries it.
    runs_custom_code = isinstance(spec, CodeSpec) or (
        isinstance(spec, WeightSpec) and spec.source is not None
    )
    if runs_custom_code and not group_config(user, "allow_custom_code"):
        raise APIError(
            "custom_code_not_allowed",
            "Custom-code strategies are not enabled for your group",
            403,
        )
    allowed = group_config(user, "allowed_symbols")
    if allowed is not None:
        # A weight spec trades a list of symbols; every one of them must be
        # allowed, and the error names all the offenders rather than the first.
        symbols = spec.universe if isinstance(spec, WeightSpec) else [spec.symbol]
        outside = [s for s in symbols if s not in allowed]
        if outside:
            raise APIError(
                "symbol_not_allowed",
                f"Symbol {', '.join(outside)} is not in your group's allowed symbols",
                403,
            )
    return spec


async def _get_owned_strategy(
    strategy_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession,
) -> Strategy:
    strategy = await StrategyRepository(session).get_for_user(strategy_id, user_id)
    if strategy is None:
        raise APIError("not_found", "Strategy not found", 404)
    return strategy


def _strategy_out(strategy: Strategy) -> dict:
    out = StrategyOut.model_validate(strategy).model_dump()
    try:
        out["spec_summary"] = spec_summary(validate_spec(strategy.spec))
    except SpecValidationError:  # pragma: no cover - stored specs are validated
        out["spec_summary"] = "invalid spec"
    return out


def _weight_spec_or_none(strategy: Strategy) -> WeightSpec | None:
    """The strategy's weight spec, or None when it is not one (or is invalid).

    Stored specs pass ``validate_spec`` at create/update, so an unparseable one
    here is a row predating that gate: returning None leaves the run to fail in
    the background task with the validator's own message rather than turning the
    start request into a 500. Single-symbol specs return None without being
    re-validated, keeping that path exactly as it was.
    """
    raw = strategy.spec
    if not isinstance(raw, dict) or raw.get("kind") != "weight":
        return None
    try:
        return validate_spec(raw)
    except SpecValidationError:  # pragma: no cover - create/update gates this
        return None


# ---------------------------------------------------------------------------
# Strategy CRUD
# ---------------------------------------------------------------------------


@router.post("/strategies", response_model=StrategyOut, status_code=201)
async def create_strategy(
    payload: StrategyCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    spec = _validate_spec_for_user(payload.spec, user)
    strategy = await StrategyRepository(session).create(
        Strategy(
            user_id=user.id,
            name=payload.name,
            description=payload.description,
            spec=spec.model_dump(),
        )
    )
    await session.commit()
    return _strategy_out(strategy)


@router.get("/strategies", response_model=list[StrategyOut])
async def list_strategies(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    strategies = await StrategyRepository(session).list_for_user(user.id)
    return [_strategy_out(s) for s in strategies]


@router.get("/strategies/{strategy_id}", response_model=StrategyOut)
async def get_strategy(
    strategy_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _strategy_out(await _get_owned_strategy(strategy_id, user.id, session))


@router.patch("/strategies/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    strategy = await _get_owned_strategy(strategy_id, user.id, session)
    if payload.spec is not None:
        spec = _validate_spec_for_user(payload.spec, user)
        normalized = spec.model_dump()
        if normalized != strategy.spec:
            strategy.spec = normalized
            strategy.version += 1  # spec edits bump the pinned version
    if payload.name is not None:
        strategy.name = payload.name
    if payload.description is not None:
        strategy.description = payload.description
    await session.commit()
    return _strategy_out(strategy)


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    strategy = await _get_owned_strategy(strategy_id, user.id, session)
    runs = await StrategyRunRepository(session).list_for_user(
        user.id, strategy_id=strategy.id
    )
    if any(run.status in ACTIVE_RUN_STATUSES for run in runs):
        raise APIError(
            "strategy_active",
            "Strategy has an active run; stop it before deleting",
            409,
        )
    await StrategyRepository(session).delete(strategy)
    await session.commit()


@router.get("/strategy-templates", response_model=list[StrategyTemplateOut])
async def get_strategy_templates() -> list[dict]:
    """The template list. Kept as a bare array for existing consumers (the MCP
    tool and the web editor both expect one); the full authoring surface,
    including the weight track, is at ``/strategy-catalog``."""
    return list_templates()


@router.get("/strategy-catalog", response_model=StrategyCatalogOut)
async def get_strategy_catalog() -> dict:
    """Everything a client needs to author any of the three spec kinds.

    The template list alone cannot describe the weight track: it is a third
    ``kind``, not a template entry, and it carries a universe, a rebalance
    cadence, and two source forms. Exposing that here is what keeps UIs and LLM
    tools from hardcoding the shape (spec: "Weight track surface is queryable").
    """
    return {
        "templates": list_templates(),
        "spec_kinds": ["template", "code", "weight"],
        "weight_track": weight_track_metadata(),
    }


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------


def _bar_date(bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


def _format_interval(interval: dict) -> str:
    return f"{interval['start']}..{interval['end']}"


async def _check_coverage(app, symbol: str, start: date, end: date) -> dict | None:
    """One symbol's coverage facts, or None when the provider cannot report them.

    The gate is best-effort by design (design D1): only a provider that knows
    what it is missing can be held to it, and a synthetic series must not be
    asked to vouch for its own completeness. ``None`` here means "unchecked",
    which the run record makes visible rather than letting it pass silently.
    """
    provider = app.state.market_data_provider
    checker = getattr(provider, "get_bars_with_coverage", None)
    if checker is None:
        return None
    return await checker(symbol, start, end)


def _gate_coverage(symbol: str, coverage: dict) -> None:
    """Reject a window whose data is missing without being proven absent.

    The DAAS layer splits the two kinds of absence, and the split is the whole
    contract: ``missing`` intervals are ones neither held nor probed-empty, so a
    statistic computed across them would silently span fewer days than the user
    asked for. ``market_closed`` intervals were probed and hold nothing — the
    series is complete for every day it trades, and rejecting on them would
    fail every window containing a holiday (a normal A-share year has ~7).
    """
    missing = coverage.get("missing") or []
    if not missing:
        return
    shown = ", ".join(_format_interval(i) for i in missing[:5])
    more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
    raise EvaluationError(
        f"incomplete price data for {symbol}: {len(missing)} missing interval(s) "
        f"[{shown}{more}] — the window is only partially covered; fill the gaps "
        f"(read_series fill_gaps=true or prewarm-prices) and retry"
    )


def _coverage_params(symbol: str, coverage: dict | None) -> dict:
    """The run-record slice of a coverage check (design D4)."""
    if coverage is None:
        return {"coverage_checked": False}
    return {
        "coverage_checked": True,
        "fingerprint": coverage.get("fingerprint") or "",
        "missing_count": len(coverage.get("missing") or []),
        "market_closed_count": len(coverage.get("market_closed") or []),
    }


async def _replay_single_symbol(app, run, spec, start: date, end: date, costs=None):
    """The single-symbol path: one series through ``on_bar(ctx)``."""
    evaluator = build_evaluator(spec)
    days = (end - start).days + 1
    coverage = await _check_coverage(app, spec.symbol, start, end)
    if coverage is not None:
        _gate_coverage(spec.symbol, coverage)
        run.params = {**(run.params or {}), **_coverage_params(spec.symbol, coverage)}
    bars = await app.state.market_data_provider.get_bars(spec.symbol, "1d", days)
    bars = [b for b in bars if start <= _bar_date(b) <= end]
    if not bars:
        raise EvaluationError(
            f"no bar data for symbol {spec.symbol} in {start}..{end}"
        )
    # 装载后断言（design D3 的第一处）：整体数据不得超出窗口末端。
    _assert_no_lookahead([_bar_date(b).isoformat() for b in bars],
                         end.isoformat(), where=f"symbol {spec.symbol}")
    return await replay(bars, evaluator, Decimal(run.params["initial_cash"]), costs)


async def _replay_universe(app, spec, start: date, end: date, initial_cash: Decimal, costs=None):
    """The weight path: date-aligned bars for the universe through the engine.

    One provider call per universe symbol, each filtered to the window, then the
    engine replays the aligned dates. Unlike the single-symbol replay this one is
    awaited directly rather than handed to a worker thread: the engine is
    synchronous by contract, and the run row is written immediately after, in the
    same task step. That keeps the whole record inside one database transaction
    instead of straddling a long suspension, which is what makes a partly written
    weight record impossible rather than merely unlikely.
    """
    days = (end - start).days + 1
    bars_by_symbol = {}
    coverage_by_symbol: dict[str, dict | None] = {}
    for symbol in spec.universe:
        coverage = await _check_coverage(app, symbol, start, end)
        coverage_by_symbol[symbol] = coverage
        if coverage is not None:
            _gate_coverage(symbol, coverage)
        bars = await app.state.market_data_provider.get_bars(symbol, "1d", days)
        bars_by_symbol[symbol] = [b for b in bars if start <= _bar_date(b) <= end]
    if not any(bars_by_symbol.values()):
        raise EvaluationError(
            f"no bar data for universe {spec.universe} in {start}..{end}"
        )
    for symbol, bars in bars_by_symbol.items():
        if bars:
            _assert_no_lookahead([_bar_date(b).isoformat() for b in bars],
                                 end.isoformat(), where=f"symbol {symbol}")
    if costs is not None and costs.enabled:
        # 静默忽略一个显式开启的成本模型，等于给出一份"零成本"的结果而用户以为
        # 含费——那正是本仓纪律禁止的沉默谎言。weight 轨的成本支持属
        # strategy-weight-mode 变更范围，此处如实拒绝。
        raise EvaluationError(
            "the cost model is not yet supported on the weight (multi-symbol) track; "
            "disable cost_model or run a single-symbol strategy"
        )
    # 尽力而为的覆盖度记录：没有能力的 provider 记 coverage_checked=false，
    # 有能力的按标的记录指纹与缺口语义（design D4/D5）。休市段数照记不拒绝。
    coverage_record: dict = {}
    for symbol, coverage in coverage_by_symbol.items():
        coverage_record[symbol] = _coverage_params(symbol, coverage)
    return replay_weights(
        spec,
        bars_by_symbol,
        initial_cash,
        evaluate_code=build_weight_evaluator(spec),
    ), coverage_record


def _record_weight_result(run: StrategyRun, result) -> None:
    """Copy a weight replay's record onto the run row (the caller commits once).

    The universe is copied, not read back from the strategy: editing the spec
    later must not rewrite what a recorded run traded (the same reason
    ``strategy_version`` is pinned).
    """
    run.universe = result.universe
    run.weight_snapshots = [s.to_dict() for s in result.snapshots]
    run.data_gaps = result.data_gaps
    run.stats = result.stats


def _assert_no_lookahead(dates: list[str], as_of: str, *, where: str) -> None:
    """装载后 / 每窗前的无未来函数断言（design D3）。

    失败抛 `EvaluationError`，使整轮回测 FAILED——不是跳过该窗。跳过会让用户
    拿到"少了几窗"的结果而不知，而 spec 的既有纪律是"FAILED 不得伪装
    COMPLETED"。
    """
    try:
        _validate_no_lookahead(dates, as_of=as_of, name=where)
    except ComputePlaneUnavailable as exc:
        raise EvaluationError(
            f"no-lookahead assertion could not run: compute plane unavailable ({exc})"
        ) from exc


async def _attach_extended_stats(app, run, equity, *, turnover_weights=None) -> None:
    """把计算面来的新统计并入 run.stats（既有字段一个不动）。"""
    if not equity:
        return
    new = extended_stats(equity, turnover_weights=turnover_weights)
    stats = dict(run.stats or {})
    stats.update({k: v for k, v in new.items() if k != "notes"})
    if new.get("notes"):
        stats["extended_stats_notes"] = new["notes"]
    run.stats = stats


async def _attach_benchmarks(app, run, equity, start: date, end: date, requested) -> None:
    """取每个基准、算相对指标，逐条落记录（缺数据的也落，带原因）。"""
    symbols = list(requested) if requested else list(DEFAULT_BENCHMARKS)
    run.benchmark_config = symbols
    results: list[dict] = []
    for symbol in symbols:
        bars, reason = await fetch_benchmark(app.state.market_data_provider, symbol, start, end)
        if bars is None:
            results.append(BenchmarkResult(symbol=symbol, available=False, reason=reason).to_dict())
            continue
        points = [(_bar_date(b), b.close) for b in bars]
        results.append(BenchmarkResult(
            symbol=symbol, available=True, points=points,
            stats=benchmark_stats(points),
            relative=relative_metrics(equity, points),
        ).to_dict())
    run.benchmark_results = results


async def _run_walkforward(app, run, spec, start, end, initial_cash, cost_model, wf):
    """walk-forward 模式：切窗（问计算面）→ 逐窗重放 → 拼接 → 整体统计。

    返回拼接后的权益曲线（调用方负责落库）——不用模型上的临时属性传递，
    那样会让"谁写了这条曲线"变成靠属性名约定。
    """
    days = (end - start).days + 1
    # Weight 轨没有单一 symbol：walk-forward 在多标的轴上照样成立——切窗按
    # 主标轴（universe 首标的）切，各窗测试段用各标的自己的 bars 重放。
    if isinstance(spec, WeightSpec):
        axis_symbol = spec.universe[0]
    else:
        axis_symbol = spec.symbol
    bars = await app.state.market_data_provider.get_bars(axis_symbol, "1d", days)
    bars = [b for b in bars if start <= _bar_date(b) <= end]
    if not bars:
        raise EvaluationError(f"no bar data for symbol {axis_symbol} in {start}..{end}")

    _assert_no_lookahead([_bar_date(b).isoformat() for b in bars],
                         end.isoformat(), where=f"symbol {axis_symbol}")

    if isinstance(spec, WeightSpec):
        # Weight 轨：universe 逐标的拉齐窗口后走 weight 引擎逐窗重放。
        days = (end - start).days + 1
        bars_by_symbol = {}
        for symbol in spec.universe:
            sb = await app.state.market_data_provider.get_bars(symbol, "1d", days)
            bars_by_symbol[symbol] = [b for b in sb if start <= _bar_date(b) <= end]
        wf_result = await run_walk_forward_weight(
            spec, bars_by_symbol, initial_cash,
            train_window=wf["train_window"], test_window=wf["test_window"],
            step=wf["step"], costs=cost_model,
        )
        run.walkforward_windows = [w.to_dict() for w in wf_result.windows]
        run.stats = wf_result.combined_stats
        run.cost_config = cost_model.to_dict() if cost_model.enabled else None
        run.cost_summary = None
        await _attach_extended_stats(app, run, wf_result.equity_points)
        await _attach_benchmarks(app, run, wf_result.equity_points, start, end,
                                 run.params.get("benchmarks"))
        return wf_result.equity_points

    result = await run_walk_forward(
        spec, bars, initial_cash,
        train_window=wf["train_window"], test_window=wf["test_window"],
        step=wf["step"], costs=cost_model,
    )
    run.walkforward_windows = [w.to_dict() for w in result.windows]
    run.stats = result.combined_stats
    run.cost_config = cost_model.to_dict() if cost_model.enabled else None
    run.cost_summary = None  # 逐窗成本各自体现在各窗权益里；跨窗汇总口径待定
    await _attach_extended_stats(app, run, result.equity_points)
    await _attach_benchmarks(app, run, result.equity_points, start, end,
                             run.params.get("benchmarks"))
    return result.equity_points


async def _run_param_scan(app, run, spec, start, end, initial_cash, cost_model, grid, sort_by) -> None:
    """参数网格扫描：笛卡尔积 → 逐一回测 → 结果矩阵 + 样本内标注（design D7）。"""
    combos = expand_grid(grid)
    limit = DEFAULT_PARAM_GRID_LIMIT
    if len(combos) > limit:
        raise EvaluationError(
            f"parameter grid of {len(combos)} combination(s) exceeds the limit of {limit}"
        )
    if not combos:
        raise EvaluationError("parameter grid is empty")

    if not isinstance(spec, TemplateSpec):
        raise EvaluationError(
            "parameter scan is supported on template strategies only "
            f"(got {type(spec).__name__}); a code or weight strategy has no "
            "declared parameter space to sweep"
        )

    matrix: list[dict] = []
    for combo in combos:
        merged = {**spec.params, **combo}
        scanned = validate_spec({
            "kind": spec.kind, "template": spec.template,
            "symbol": spec.symbol, "params": merged,
        })
        result = await _replay_single_symbol(app, run, scanned, start, end, cost_model)
        matrix.append({"params": combo, "stats": result.stats})

    from ...strategy.walk_forward import sort_matrix
    run.param_grid = grid
    run.scan_results = sort_matrix(matrix, sort_by)
    # 机读的样本内标注：最佳组最容易被误读为推荐，故标注是数据而非文档。
    run.in_sample = True
    run.stats = {"scan": {
        "combinations": len(combos),
        "sorted_by": sort_by,
        "in_sample": True,
        "disclaimer": (
            "in-sample result: these parameters were scored on the same window "
            "used to choose them; this is not a recommendation"
        ),
    }}


async def _run_backtest(app, run_id: uuid.UUID) -> None:
    """Background lifecycle: QUEUED -> RUNNING -> COMPLETED | FAILED."""
    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        if run is None:
            return
        run.status = StrategyRunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        await session.commit()
        try:
            strategy = await session.get(Strategy, run.strategy_id)
            spec = validate_spec(strategy.spec)
            start = date.fromisoformat(run.params["start"])
            end = date.fromisoformat(run.params["end"])
            initial_cash = Decimal(run.params["initial_cash"])
            cost_model = CostModel.from_mapping(run.params.get("cost_model"))
            wf = run.params.get("walkforward")
            grid = run.params.get("param_grid")

            if grid:
                await _run_param_scan(app, run, spec, start, end, initial_cash,
                                      cost_model, grid, run.params.get("sort_by", "sharpe"))
            elif wf:
                wf_equity = await _run_walkforward(
                    app, run, spec, start, end, initial_cash, cost_model, wf
                )
                session.add_all(
                    StrategyEquityPoint(run_id=run.id, date=d, equity=e)
                    for d, e in wf_equity
                )
            elif isinstance(spec, WeightSpec):
                result, coverage_record = await _replay_universe(
                    app, spec, start, end, initial_cash, cost_model
                )
                _record_weight_result(run, result)
                # 覆盖度记录落在 params：指纹 + 每标的缺失/休市段数 + 是否检查。
                run.params = {**(run.params or {}), "data_coverage": coverage_record}
                session.add_all(
                    StrategyEquityPoint(run_id=run.id, date=d, equity=e)
                    for d, e in result.equity_points
                )
                await _attach_extended_stats(app, run, result.equity_points)
            else:
                result = await _replay_single_symbol(
                    app, run, spec, start, end, cost_model
                )
                await StrategyRunRepository(session).add_equity_points(
                    [
                        StrategyEquityPoint(run_id=run.id, date=d, equity=e)
                        for d, e in result.equity_points
                    ]
                )
                run.stats = result.stats
                await _attach_extended_stats(app, run, result.equity_points)
            if run.benchmark_config is None and run.params.get("benchmarks") is not None:
                pass  # 已由各模式内部处理
            run.status = StrategyRunStatus.COMPLETED
        except EvaluationError as exc:
            run.status = StrategyRunStatus.FAILED
            run.error = str(exc)
        except ComputePlaneUnavailable as exc:
            # 计算面不可用 → 明确失败并指名（design D2：不降级为内部实现）。
            run.status = StrategyRunStatus.FAILED
            run.error = f"compute plane unavailable: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            run.status = StrategyRunStatus.FAILED
            run.error = f"backtest failed: {exc}"
        run.stopped_at = datetime.now(timezone.utc)
        await session.commit()


@router.post(
    "/strategies/{strategy_id}/backtests",
    response_model=StrategyRunOut,
    status_code=202,
)
async def start_backtest(
    strategy_id: uuid.UUID,
    payload: BacktestRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StrategyRun:
    strategy = await _get_owned_strategy(strategy_id, user.id, session)
    max_days = group_config(user, "max_backtest_days")
    window_days = (payload.end - payload.start).days + 1
    if window_days > max_days:
        raise APIError(
            "window_too_large",
            f"Backtest window of {window_days} days exceeds your group's "
            f"limit of {max_days} days",
            400,
        )
    weight_spec = _weight_spec_or_none(strategy)

    # 扫描的组合数上限在**请求时**检查（design D6）：让它跑起来再在后台失败，
    # 用户拿到的是一个 FAILED 而不是一条"你超了 50 组"的即时拒绝。
    if payload.param_grid:
        n = grid_size(payload.param_grid)
        if n > DEFAULT_PARAM_GRID_LIMIT:
            raise APIError(
                "grid_too_large",
                f"parameter grid of {n} combination(s) exceeds the limit of "
                f"{DEFAULT_PARAM_GRID_LIMIT}",
                400,
            )

    params = {
        "start": payload.start.isoformat(),
        "end": payload.end.isoformat(),
        "initial_cash": str(payload.initial_cash),
    }
    if payload.cost_model is not None:
        # 即使 enabled=false 也存快照：运行记录要能回答"这次用了什么成本配置"，
        # 而"用户显式关掉了成本"与"这个请求根本没有成本字段"是两个不同的事实。
        params["cost_model"] = payload.cost_model.model_dump(mode="json")
    if payload.benchmarks is not None:
        params["benchmarks"] = list(payload.benchmarks)
    if payload.walk_forward_enabled:
        params["walkforward"] = {
            "train_window": payload.train_window,
            "test_window": payload.test_window,
            "step": payload.step,
        }
    if payload.param_grid:
        params["param_grid"] = payload.param_grid
        params["sort_by"] = payload.sort_by
    universe = None
    if weight_spec is not None:
        # The window gate above is date-based, so it already applies to weight
        # runs unchanged. The universe is pinned here, at start, exactly as the
        # window is: a later spec edit must not rewrite what this run traded.
        universe = list(weight_spec.universe)
        params["universe"] = universe
    run = await StrategyRunRepository(session).create(
        StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode=StrategyRunMode.BACKTEST,
            status=StrategyRunStatus.QUEUED,
            params=params,
            universe=universe,
        )
    )
    await session.commit()
    asyncio.create_task(_run_backtest(request.app, run.id))
    return run


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

_VALID_RUN_MODES = (StrategyRunMode.BACKTEST, StrategyRunMode.PAPER)
_VALID_RUN_STATUSES = (
    StrategyRunStatus.QUEUED,
    StrategyRunStatus.RUNNING,
    StrategyRunStatus.COMPLETED,
    StrategyRunStatus.FAILED,
    StrategyRunStatus.STARTING,
    StrategyRunStatus.STOPPED,
    StrategyRunStatus.ERROR,
)


@router.get("/strategy-runs", response_model=list[StrategyRunOut])
async def list_strategy_runs(
    strategy_id: uuid.UUID | None = None,
    mode: str | None = None,
    status: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[StrategyRun]:
    if mode is not None and mode not in _VALID_RUN_MODES:
        raise APIError("invalid_filter", f"mode must be one of {_VALID_RUN_MODES}", 400)
    if status is not None and status not in _VALID_RUN_STATUSES:
        raise APIError(
            "invalid_filter", f"status must be one of {_VALID_RUN_STATUSES}", 400
        )
    return await StrategyRunRepository(session).list_for_user(
        user.id, strategy_id=strategy_id, mode=mode, status=status
    )


@router.get("/strategy-runs/{run_id}", response_model=StrategyRunDetailOut)
async def get_strategy_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StrategyRunDetailOut:
    run = await StrategyRunRepository(session).get_for_user(run_id, user.id)
    if run is None:
        raise APIError("not_found", "Strategy run not found", 404)
    out = StrategyRunDetailOut.model_validate(run)
    out.equity_points = sorted(out.equity_points, key=lambda p: p.date)
    return out


# ---------------------------------------------------------------------------
# Live paper runs
# ---------------------------------------------------------------------------


@router.post(
    "/strategies/{strategy_id}/runs",
    response_model=StrategyRunOut,
    status_code=202,
)
async def start_paper_run(
    strategy_id: uuid.UUID,
    payload: PaperRunRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StrategyRun:
    strategy = await _get_owned_strategy(strategy_id, user.id, session)
    account = await AccountRepository(session).get_for_user(payload.account_id, user.id)
    if account is None:
        raise APIError("not_found", "Account not found", 404)
    max_active = group_config(user, "max_active_strategies")
    active = await StrategyRunRepository(session).count_active_paper_for_user(user.id)
    if active >= max_active:
        raise APIError(
            "active_limit_reached",
            f"You already have {active} active strategy run(s); your group's "
            f"limit is {max_active}",
            409,
        )
    weight_spec = _weight_spec_or_none(strategy)
    universe = None
    if weight_spec is not None:
        # Starting is where the universe size is decided for the run's whole
        # life, so the group limit is checked here and never re-derived later.
        limit = group_config(user, "max_universe_size")
        if len(weight_spec.universe) > limit:
            raise APIError(
                "universe_too_large",
                f"Universe of {len(weight_spec.universe)} symbol(s) exceeds your "
                f"group's limit of {limit} symbol(s)",
                400,
            )
        universe = list(weight_spec.universe)
        params = {"universe": universe}
    else:
        params = {"symbol": strategy.spec.get("symbol")}
    run = await StrategyRunRepository(session).create(
        StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode=StrategyRunMode.PAPER,
            account_id=account.id,
            status=StrategyRunStatus.STARTING,
            params=params,
            universe=universe,
        )
    )
    await session.commit()
    await request.app.state.strategy_runner.start(run.id)
    return run


@router.post("/strategy-runs/{run_id}/stop", response_model=StrategyRunOut)
async def stop_paper_run(
    run_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StrategyRun:
    run = await StrategyRunRepository(session).get_for_user(run_id, user.id)
    if run is None:
        raise APIError("not_found", "Strategy run not found", 404)
    if run.mode != StrategyRunMode.PAPER:
        raise APIError("invalid_mode", "Only paper runs can be stopped", 400)
    if run.status not in ACTIVE_RUN_STATUSES:
        return run  # idempotent: already terminal
    await request.app.state.strategy_runner.stop(run.id)
    # The runner mutated the row in its own session; refresh ours.
    await session.refresh(run)
    return run
