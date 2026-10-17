"""Task 1.1/1.3: weight validation runs through the contract, and the engine
agrees with it by construction rather than by coincidence.

The point of these tests is the *agreement*, not the individual rule: they assert
that the engine's verdict on a mapping equals the contract's verdict on the same
mapping, which is what fails if someone later re-implements a rule locally.
"""

from __future__ import annotations

import pytest

from fd_paper_trading._vendor.fd_quant import (
    ContractError,
    TargetWeights,
    validate_target_weights,
)
from fd_paper_trading.strategy.weights import (
    WEIGHT_SUM_TOLERANCE,
    envelope_for_weights,
    validate_contract_instance,
    validate_universe,
    validate_weights,
    weights_from_contract,
)

UNIVERSE = ["600519", "000001"]


# ---------------------------------------------------------------------------
# 1.1 — valid samples: engine accepts exactly when the contract does
# ---------------------------------------------------------------------------

VALID_SAMPLES = {
    "full-sum-minus-cash": ({"600519": 0.5, "000001": 0.3}, 0.2),
    "exactly-cash-only": ({}, 1.0),
    "single-name-full": ({"600519": 1.0}, 0.0),
}


@pytest.mark.parametrize("name", sorted(VALID_SAMPLES))
def test_valid_weights_accepted_and_contract_agrees(name: str) -> None:
    weights, cash_floor = VALID_SAMPLES[name]
    engine = validate_weights(UNIVERSE, weights, cash_floor=cash_floor)
    contract = validate_target_weights(
        envelope_for_weights(UNIVERSE, weights, cash_floor=cash_floor)
    )
    assert engine == [], f"{name}: engine should accept, said {engine}"
    assert contract["valid"] is True, f"{name}: contract should accept"


# ---------------------------------------------------------------------------
# 1.1 — invalid samples: engine rejects for the same reason the contract does
# ---------------------------------------------------------------------------

INVALID_SAMPLES = {
    "sum-over-one": {"600519": 0.5, "000001": 0.7},
    "negative": {"600519": -0.1},
    "symbol-outside-universe": {"600519": 0.2, "300750": 0.2},
}


@pytest.mark.parametrize("name", sorted(INVALID_SAMPLES))
def test_invalid_weights_rejected_and_contract_agrees(name: str) -> None:
    weights = INVALID_SAMPLES[name]
    engine = validate_weights(UNIVERSE, weights)
    contract = validate_target_weights(envelope_for_weights(UNIVERSE, weights))
    assert engine, f"{name}: engine should reject"
    assert contract["valid"] is False, f"{name}: contract should reject"
    # Not merely "both said no": the engine's message is the contract's message
    # for the offending field, so a divergence in wording would also fail here.
    contract_reasons = {e["reason"] for e in contract["errors"]}
    assert any(reason in message for message in engine for reason in contract_reasons), (
        f"{name}: engine messages {engine} do not carry a contract reason "
        f"{contract_reasons}"
    )


def test_engine_never_normalizes() -> None:
    """A rejected mapping stays rejected — no rescaling into validity."""
    weights = {"600519": 0.8, "000001": 0.8}
    assert validate_weights(UNIVERSE, weights)
    # The same mapping with the offending period scaled would be valid; the
    # engine must not have done that scaling itself.
    assert validate_weights(UNIVERSE, {"600519": 0.5, "000001": 0.5}) == []


def test_tolerance_boundary_is_the_contracts() -> None:
    """Σweights == 1 + tolerance/2 passes; the contract decides, not the engine."""
    just_over = 1.0 + WEIGHT_SUM_TOLERANCE / 2
    assert validate_weights(["A"], {"A": just_over}) == []
    clearly_over = 1.0 + WEIGHT_SUM_TOLERANCE * 10
    assert validate_weights(["A"], {"A": clearly_over})


def test_cash_floor_pushes_weights_over_the_combined_bound() -> None:
    """0.8 + cash 0.3 is over the combined bound; 0.8 + cash 0.2 is fine."""
    assert validate_weights(["A"], {"A": 0.8}, cash_floor=0.3)
    assert validate_weights(["A"], {"A": 0.8}, cash_floor=0.2) == []


# ---------------------------------------------------------------------------
# universe rules (same one-home principle)
# ---------------------------------------------------------------------------


def test_empty_universe_rejected() -> None:
    assert validate_universe([])


def test_duplicate_universe_rejected() -> None:
    assert validate_universe(["600519", "600519"])


def test_valid_universe_accepted() -> None:
    assert validate_universe(["600519", "000001"]) == []


# ---------------------------------------------------------------------------
# 1.3 — adapting a published contract instance to the engine's period shape
# ---------------------------------------------------------------------------


def _instance(**overrides) -> dict:
    base = {
        "schema_version": "1",
        "as_of": "2026-10-16",
        "universe": ["600519", "000001", "CASH"],
        "weights": {"600519": 0.5, "000001": 0.3},
        "cash_floor": 0.2,
        "producer": {"pack": "pack-strategy-lab", "skill": "portfolio-weights"},
    }
    base.update(overrides)
    return base


def test_valid_instance_adapts_to_period_shape() -> None:
    as_of, weights, cash_floor = weights_from_contract(_instance())
    assert as_of == "2026-10-16"
    assert weights == {"600519": 0.5, "000001": 0.3}
    assert cash_floor == 0.2


def test_instance_accepts_cash_symbol_in_universe_without_weighting_it() -> None:
    """A universe may name a cash placeholder; it simply carries no weight."""
    instance = _instance()
    instance["weights"] = {"600519": 0.4}
    instance["cash_floor"] = 0.6
    as_of, weights, cash_floor = weights_from_contract(instance)
    assert "CASH" not in weights
    assert cash_floor == 0.6


def test_invalid_instance_rejected_by_the_contract_layer() -> None:
    with pytest.raises(ContractError) as exc:
        weights_from_contract(_instance(weights={"600519": 0.5, "000001": 0.7}))
    assert "Σweights" in str(exc.value)


def test_published_instance_is_validated_as_a_whole() -> None:
    """The static path judges the instance as published, producer and all."""
    assert validate_contract_instance(_instance()) == []
    assert validate_contract_instance(_instance(producer={"pack": "p"}))
    assert validate_contract_instance(
        _instance(schema_version="2")
    ), "an unsupported schema version must be refused"


def test_dataclass_round_trip_matches_dict_adaptation() -> None:
    """The dataclass path and the dict path agree — one contract, one answer."""
    instance = _instance()
    from_dataclass = weights_from_contract(TargetWeights.from_dict(instance).to_dict())
    from_dict = weights_from_contract(instance)
    assert from_dataclass == from_dict
