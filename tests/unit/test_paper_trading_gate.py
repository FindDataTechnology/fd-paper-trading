"""`scripts/check_paper_trading.py` 的合成判据测试。

这条脚本不重新实现判据，只把两条闸（标的可报价性、run 是否在推进）的结论合成
一个「现在能不能交易」。所以要测的是**合成规则**本身，尤其是它不能把「什么都没查」
读成「可交易」——那是同族闸最常见的假绿。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "check_paper_trading.py")


def _load():
    name = "paper_trading_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()


def test_all_layers_tradable_is_tradable():
    assert gate.combine([gate.Branch("a", gate.TRADABLE, 0),
                         gate.Branch("b", gate.TRADABLE, 0)]) == gate.TRADABLE


def test_one_blocked_layer_blocks_everything():
    assert gate.combine([gate.Branch("a", gate.TRADABLE, 0),
                         gate.Branch("b", gate.BLOCKED, 3)]) == gate.BLOCKED


def test_blocked_wins_over_degraded_regardless_of_order():
    a = gate.Branch("a", gate.BLOCKED, 1)
    b = gate.Branch("b", gate.DEGRADED, 0)
    assert gate.combine([a, b]) == gate.BLOCKED
    assert gate.combine([b, a]) == gate.BLOCKED


def test_empty_input_is_not_tradable():
    """一个分支都没有＝什么都没查。读成 tradable 就是这道闸最坏的那种假绿。"""
    with pytest.raises(ValueError):
        gate.combine([])


def test_self_test_passes():
    assert gate._self_test() == 0


def test_liveness_branch_raises_when_nothing_is_live():
    class FakeLiveness:
        @staticmethod
        def judge(facts, *, today, max_age_days):
            return []

    with pytest.raises(ValueError):
        gate._liveness_branch(FakeLiveness, 4, [])


def test_liveness_branch_blocks_on_a_stalled_run():
    class FakeLiveness:
        class Verdict:
            def __init__(self, state):
                self.run_id, self.strategy, self.state, self.detail = "r1", "s", state, "d"

            @property
            def counts(self):
                return self.state in ("runner_lag", "data_stalled")

        @staticmethod
        def judge(facts, *, today, max_age_days):
            return [FakeLiveness.Verdict("data_stalled")]

    b = gate._liveness_branch(FakeLiveness, 4, [])
    assert b.severity == gate.BLOCKED and b.hits == 1


def test_coverage_branch_blocks_on_a_counting_verdict():
    class FakeCoverage:
        class Verdict:
            def __init__(self, symbol, state):
                self.symbol, self.state, self.detail = symbol, state, "d"

            @property
            def counts(self):
                return self.state in ("stale", "unpriceable")

        @staticmethod
        def judge(facts, *, today, max_age_days, declared=None):
            return [FakeCoverage.Verdict("600000", "stale")]

    b = gate._coverage_branch(FakeCoverage, 4, {}, [])
    assert b.severity == gate.BLOCKED and b.hits == 1


def test_coverage_branch_downgrades_but_does_not_block_on_declared_gap():
    class FakeCoverage:
        class Verdict:
            def __init__(self, symbol, state):
                self.symbol, self.state, self.detail = symbol, state, "d"

            @property
            def counts(self):
                return False

        @staticmethod
        def judge(facts, *, today, max_age_days, declared=None):
            return [FakeCoverage.Verdict("007355", "declared_gap")]

    b = gate._coverage_branch(FakeCoverage, 4, {}, [])
    assert b.severity == gate.DEGRADED and b.hits == 0


def test_liveness_branch_degrades_but_states_why_when_a_tick_is_pending():
    """「还没到下一拍」不是命中，但也不许静默读成 tradable——原因必须出现在行里。"""
    class FakeLiveness:
        class Verdict:
            def __init__(self, state):
                self.run_id, self.strategy, self.state = "r1", "s", state
                self.detail = "下一拍约 9 分钟后"

            @property
            def counts(self):
                return self.state in ("runner_lag", "data_stalled")

        @staticmethod
        def judge(facts, *, today, max_age_days):
            return [FakeLiveness.Verdict("runner_waiting")]

    b = gate._liveness_branch(FakeLiveness, 4, [])
    assert b.severity == gate.DEGRADED and b.hits == 0
    assert any("runner_waiting" in line and "下一拍" in line for line in b.lines)
