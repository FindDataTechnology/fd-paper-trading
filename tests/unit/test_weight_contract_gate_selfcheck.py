"""Reverse self-check for the contract-agreement claim (task 1.1).

A test suite that "engine agrees with contract" passes trivially if the engine
re-implements the rules — the two agree because they are the same code written
twice on the same day. This module proves the agreement is *load-bearing* by
patching the vendored contract and asserting the engine's verdict follows the
contract, not a private copy. If someone later forks the rule locally, these
tests fail: the engine's answer would stop tracking the contract's.
"""

from __future__ import annotations

import pytest

from fd_paper_trading.strategy import weights as weights_mod
from fd_paper_trading.strategy.weights import validate_weights

UNIVERSE = ["600519", "000001"]


def test_engine_verdict_follows_a_changed_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract now refuses a sum of 0.9 → the engine must refuse it too.

    If the engine kept its own "≤ 1" check, it would still accept 0.9 and this
    assertion fails, which is exactly the drift we want surfaced.
    """
    real = weights_mod.contract_errors

    def stricter(instance):
        errors = real(instance)
        total = sum(float(v) for v in (instance.get("weights") or {}).values())
        if total > 0.9:
            errors = list(errors) + [
                {"path": "weights", "reason": "Σweights must be ≤ 0.9 (fixture rule)"}
            ]
        return errors

    monkeypatch.setattr(weights_mod, "contract_errors", stricter)
    assert validate_weights(UNIVERSE, {"600519": 0.95}) != [], (
        "engine accepted a mapping the (patched) contract refuses — the weight "
        "rules are being applied locally instead of delegated"
    )
    assert validate_weights(UNIVERSE, {"600519": 0.5}) == []


def test_engine_verdict_follows_a_changed_universe_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract now requires ≥ 2 universe entries → the engine follows."""
    real = weights_mod.contract_errors

    def stricter(instance):
        errors = real(instance)
        if len(instance.get("universe") or []) < 2:
            errors = list(errors) + [
                {"path": "universe", "reason": "must have at least 2 entries (fixture rule)"}
            ]
        return errors

    monkeypatch.setattr(weights_mod, "contract_errors", stricter)
    from fd_paper_trading.strategy.weights import validate_universe

    assert validate_universe(["600519"]) != []
    assert validate_universe(["600519", "000001"]) == []


def test_engine_surfaces_the_contracts_own_reason_text() -> None:
    """Whatever the contract says is what the author reads — no paraphrase layer."""
    from fd_paper_trading._vendor.fd_quant import validate_target_weights
    from fd_paper_trading.strategy.weights import envelope_for_weights

    mapping = {"600519": 0.5, "000001": 0.7}
    contract_reasons = [
        e["reason"] for e in validate_target_weights(envelope_for_weights(UNIVERSE, mapping))["errors"]
    ]
    engine_messages = validate_weights(UNIVERSE, mapping)
    assert contract_reasons, "fixture should violate the contract"
    for reason in contract_reasons:
        assert any(reason in msg for msg in engine_messages), (
            f"contract reason {reason!r} is not visible in engine messages {engine_messages}"
        )
