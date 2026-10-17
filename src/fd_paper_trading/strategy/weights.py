"""Weight rules run through the ``TargetWeights`` contract, never restated here.

The engine must not keep a second copy of "non-negative, Σ ≤ 1, symbols within
the universe" (design D5): two copies of the same rule inevitably drift, and the
one that drifts is always the one nobody re-reads. So this module holds no weight
arithmetic of its own — it builds a minimal contract instance and hands it to the
contract layer, then maps the contract's per-field errors into the engine's
message style. Change the rule upstream and the engine changes with it.

Where the contract lives: ``fd_paper_trading._vendor.fd_quant`` — a byte-identical
mirror, because ``fd-quant`` has no distribution channel today (not on an index,
not even a git repository) while this repo installs from a frozen lockfile and
builds an image that copies only this repo. The mirror is pinned by sha256 in
``_vendor/VENDOR.json`` and gated by ``scripts/check_contract_drift.py``. See
``_vendor/README.md``; the switch to a real package dependency is an explicit
deliverable in ``openspec/changes/strategy-weight-mode/tasks.md``.

The envelope question: the contract validates whole instances (``as_of``,
``universe``, ``weights``, ``cash_floor``, ``producer``), while the engine mostly
has a fragment of one — a single period's mapping. ``envelope_for_weights``
supplies the missing envelope fields with engine-owned placeholders so the
fragment reaches the real rule check. The envelope's own fields are deliberately
trivial (they can only fail in ways the engine checks separately), so a rejection
always points at the weights.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .._vendor.fd_quant import (
    SUPPORTED_SCHEMA_VERSION,
    WEIGHT_SUM_TOLERANCE,
    ContractError,  # noqa: F401 - re-exported for callers that catch it
    TargetWeights,
    validate_target_weights,
)

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "WEIGHT_SUM_TOLERANCE",
    "TargetWeights",
    "contract_errors",
    "envelope_for_weights",
    "validate_contract_instance",
    "validate_universe",
    "validate_weights",
    "weights_from_contract",
]

#: Placeholder producer for engine-synthesized envelopes. The engine is the
#: producer of record for weights it validates on its own behalf (a strategy
#: author's mapping is not a pack's published contract instance); a real
#: instance arriving from a pack keeps its own producer untouched.
ENGINE_PRODUCER = {"pack": "fd-paper-trading", "skill": "strategy-weight-mode"}

#: Stand-in date for fragments that carry no date. A fixed valid date keeps the
#: synthesized envelope deterministic (a "today" default would make messages and
#: the contract result change across days for no reason).
_PLACEHOLDER_AS_OF = "1970-01-01"


def envelope_for_weights(
    universe: Sequence[str],
    weights: Mapping[str, Any],
    *,
    as_of: str | None = None,
    cash_floor: float = 0.0,
) -> dict:
    """Wrap a bare ``{symbol: weight}`` mapping in a valid-shaped contract instance.

    ``cash_floor`` defaults to 0 so the contract's ``Σweights ≤ 1`` bound is the
    binding constraint, which is what the engine means by "weights within 1".
    """
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "as_of": as_of or _PLACEHOLDER_AS_OF,
        "universe": list(universe),
        "weights": dict(weights),
        "cash_floor": float(cash_floor),
        "producer": dict(ENGINE_PRODUCER),
    }


def contract_errors(instance: Any) -> list[dict]:
    """Contract-layer errors for an instance (empty list when it validates)."""
    return validate_target_weights(instance)["errors"]


def _messages(errors: list[dict]) -> list[str]:
    """Render contract errors the way the engine renders its own violations."""
    out = []
    for err in errors:
        path = err.get("path", "$")
        reason = err.get("reason", "invalid")
        out.append(f"{path}: {reason}" if path else reason)
    return out


def validate_weights(
    universe: Sequence[str],
    weights: Mapping[str, Any],
    *,
    as_of: str | None = None,
    cash_floor: float = 0.0,
) -> list[str]:
    """Human-readable violations for one period's weights; empty = valid.

    Rules applied are the contract's: non-negative, ``Σweights ≤ 1`` within the
    contract's tolerance, and every symbol inside ``universe``. Never normalizes
    or clips — an invalid mapping is reported, not rescaled.
    """
    return _messages(
        contract_errors(
            envelope_for_weights(universe, weights, as_of=as_of, cash_floor=cash_floor)
        )
    )


def validate_universe(universe: Any) -> list[str]:
    """Human-readable violations for a universe (non-empty, no duplicates, strings).

    Reuses the contract by synthesizing an empty-weights instance, so universe
    rules also have exactly one home.
    """
    return _messages(
        contract_errors(envelope_for_weights(list(universe or []), {}))
    )


def validate_contract_instance(instance: Any) -> list[str]:
    """Validate a full contract instance as published (producer and all).

    Used for the static-weights path, where an instance may arrive from a pack
    and must be judged as a contract, not reduced to a bare mapping first.
    """
    return _messages(contract_errors(instance))


def weights_from_contract(instance: Mapping[str, Any]) -> tuple[str, dict[str, float], float]:
    """Adapt a validated contract instance to the engine's period shape (task 1.3).

    Returns ``(as_of, weights, cash_floor)``: the period key, the target mapping,
    and the target cash floor. The instance must already have passed
    ``validate_contract_instance`` or ``TargetWeights.from_dict``; a malformed one
    raises ``ContractError`` from the contract layer rather than being patched up
    here.
    """
    tw = TargetWeights.from_dict(instance)
    return tw.as_of, dict(tw.weights), float(tw.cash_floor)
