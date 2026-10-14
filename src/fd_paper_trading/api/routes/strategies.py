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
    EvaluationError,
    SpecValidationError,
    build_evaluator,
    replay,
    spec_summary,
    validate_spec,
)
from ...strategy.templates import list_templates
from ..errors import APIError
from ..schemas import (
    BacktestRequest,
    PaperRunRequest,
    StrategyCreate,
    StrategyOut,
    StrategyRunDetailOut,
    StrategyRunOut,
    StrategyTemplateOut,
    StrategyUpdate,
)

router = APIRouter(tags=["strategies"])


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
    if isinstance(spec, CodeSpec) and not group_config(user, "allow_custom_code"):
        raise APIError(
            "custom_code_not_allowed",
            "Custom-code strategies are not enabled for your group",
            403,
        )
    allowed = group_config(user, "allowed_symbols")
    if allowed is not None and spec.symbol not in allowed:
        raise APIError(
            "symbol_not_allowed",
            f"Symbol {spec.symbol} is not in your group's allowed symbols",
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
    return list_templates()


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------


def _bar_date(bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


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
            evaluator = build_evaluator(spec)
            start = date.fromisoformat(run.params["start"])
            end = date.fromisoformat(run.params["end"])
            days = (end - start).days + 1
            bars = await app.state.market_data_provider.get_bars(
                spec.symbol, "1d", days
            )
            bars = [b for b in bars if start <= _bar_date(b) <= end]
            if not bars:
                raise EvaluationError(
                    f"no bar data for symbol {spec.symbol} in {start}..{end}"
                )
            result = await replay(
                bars, evaluator, Decimal(run.params["initial_cash"])
            )
            await StrategyRunRepository(session).add_equity_points(
                [
                    StrategyEquityPoint(run_id=run.id, date=d, equity=e)
                    for d, e in result.equity_points
                ]
            )
            run.stats = result.stats
            run.status = StrategyRunStatus.COMPLETED
        except EvaluationError as exc:
            run.status = StrategyRunStatus.FAILED
            run.error = str(exc)
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
    run = await StrategyRunRepository(session).create(
        StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode=StrategyRunMode.BACKTEST,
            status=StrategyRunStatus.QUEUED,
            params={
                "start": payload.start.isoformat(),
                "end": payload.end.isoformat(),
                "initial_cash": str(payload.initial_cash),
            },
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
    run = await StrategyRunRepository(session).create(
        StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode=StrategyRunMode.PAPER,
            account_id=account.id,
            status=StrategyRunStatus.STARTING,
            params={"symbol": strategy.spec.get("symbol")},
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
