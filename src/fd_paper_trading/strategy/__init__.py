"""Strategy core: spec models, indicators, evaluation contract, templates.

The live runner (``PaperRunSupervisor``) is imported lazily: it pulls in the
execution + persistence (SQLAlchemy) stack, which the custom-code sandbox
subprocess must not pay for on every per-bar spawn. Import it via
``from fd_paper_trading.strategy.runner import PaperRunSupervisor`` (the app does this)
or access ``fd_paper_trading.strategy.PaperRunSupervisor`` (resolved on first use).
"""

from .compute_plane import ComputePlaneUnavailable
from .costs import CostModel, CostTotals
from .extended_stats import extended_stats
from .walk_forward import expand_grid, grid_size, run_walk_forward, sort_matrix
from .backtester import (
    BacktestResult,
    BarEvaluator,
    EvaluationError,
    build_evaluator,
    compute_stats,
    replay,
)
from .ctx import Signal, StrategyContext
from .codegen import CodeValidationError, validate_source, validate_source_strict
from .spec import (
    CodeSpec,
    Rebalance,
    SpecValidationError,
    StrategySpec,
    TemplateSpec,
    WeightSpec,
    spec_summary,
    validate_spec,
)
from .templates import (
    CATALOG,
    TemplateValidationError,
    list_templates,
    make_evaluator,
    validate_template_params,
)
from .weight_backtester import (
    RebalanceSnapshot,
    WeightBacktestResult,
    build_weight_evaluator,
    replay_weights,
)


def __getattr__(name: str):
    if name == "PaperRunSupervisor":
        from .runner import PaperRunSupervisor

        globals()[name] = PaperRunSupervisor  # cache
        return PaperRunSupervisor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + ["PaperRunSupervisor"])


__all__ = [
    "Signal",
    "StrategyContext",
    "PaperRunSupervisor",
    "BacktestResult",
    "BarEvaluator",
    "EvaluationError",
    "build_evaluator",
    "compute_stats",
    "replay",
    "CodeSpec",
    "Rebalance",
    "SpecValidationError",
    "StrategySpec",
    "TemplateSpec",
    "WeightSpec",
    "spec_summary",
    "validate_spec",
    "RebalanceSnapshot",
    "WeightBacktestResult",
    "build_weight_evaluator",
    "replay_weights",
    "CostModel",
    "CostTotals",
    "ComputePlaneUnavailable",
    "extended_stats",
    "run_walk_forward",
    "expand_grid",
    "grid_size",
    "sort_matrix",
    "CATALOG",
    "TemplateValidationError",
    "list_templates",
    "make_evaluator",
    "validate_template_params",
    "CodeValidationError",
    "validate_source",
    "validate_source_strict",
]
