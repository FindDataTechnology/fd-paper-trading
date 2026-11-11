"""回测可比性判定（change1 task 6.2）。

判据：**同数据 + 同策略 + 同计算参数**跑两次 → 统计必须逐位一致；
数据变了 → 该区间标不可比，不拿新旧数字做任何比较。

三件事都必须分开，否则这个工具会撒谎：
1. 指纹相同但策略不同（本轮实测：03849c8c vs 741671d1，指纹一样、成交 6 vs 0）；
2. 指纹相同、策略相同，但**计算参数**不同（实测：一次带 cost_model 一次不带，
   final_equity 差 10.60）——这是应当的差异，不是缺陷；
3. 指纹缺失 ≠ 指纹相同（缺的那侧根本没被检查过）。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "compare_backtest_fingerprints.py"
sys.path.insert(0, str(REPO / "scripts"))

from compare_backtest_fingerprints import compare  # noqa: E402

FP = "a" * 64


def _run(params=None, stats=None, sid="s1", ver=1):
    p = {"start": "2026-08-21", "end": "2026-10-16", "initial_cash": "100000",
         "data_coverage": {"600036": {"fingerprint": FP, "as_of_max": "2026-11-07"}}}
    p.update(params or {})
    return {"strategy_id": sid, "strategy_version": ver, "params": p,
            "stats": stats or {"total_return": "0.1", "trade_count": 6, "final_equity": "101000"}}


# ------------------------------------------------- 可比且一致


def test_same_data_strategy_params_are_digit_identical():
    """同数据同策略同参数 → 统计逐位一致。

    **两边必须是两个独立对象且各带真实统计**：早先这条用了同一个 `_run()`，
    连破坏"比较统计"那一段都照样绿——恒真的判据等于没有判据（本轮实测到过）。
    """
    before = _run(stats={"total_return": "0.1", "trade_count": 6, "final_equity": "101000"})
    after = _run(stats={"total_return": "0.1", "trade_count": 6, "final_equity": "101000"})
    out = compare(before, after)
    assert out["comparable"] is True
    assert out["stats_identical"] is True
    assert out["not_comparable_symbols"] == []


def test_same_inputs_but_different_stats_is_flagged_as_defect():
    """同数据同策略同参数，统计却不等 → 这是实现缺陷，必须点名。

    这条与上一条成对：上一条证明"一致时判一致"，这条证明"不一致时判不一致"。
    只留前一条的话，把比较逻辑删掉也照样绿。
    """
    out = compare(_run(stats={"total_return": "0.1", "final_equity": "101000"}),
                  _run(stats={"total_return": "0.2", "final_equity": "102000"}))
    assert out["stats_identical"] is False
    assert any("实现缺陷" in n for n in out["notes"])


# ------------------------------------------------- 三种"不可比"必须分开


def test_fingerprint_change_marks_the_interval_not_comparable():
    after = _run()
    after["params"]["data_coverage"]["600036"] = {
        "fingerprint": "b" * 64, "as_of_max": "2026-11-09"}
    out = compare(_run(), after)
    assert out["comparable"] is False
    assert out["not_comparable_symbols"] == ["600036"]
    assert out["symbols"]["600036"]["reason"] == "fingerprint_changed"
    # 不可比时不许比较统计——那正是"拿噪声当提升"
    assert out["stats_identical"] is None


def test_missing_fingerprint_is_not_equal_to_a_fingerprint():
    """缺指纹的一侧没被检查过，不能当成"一致"。"""
    after = _run()
    after["params"]["data_coverage"]["600036"] = {}
    out = compare(_run(), after)
    assert out["symbols"]["600036"]["reason"] == "fingerprint_missing"
    assert out["comparable"] is False


def test_different_strategy_does_not_compare_stats():
    """指纹只覆盖数据；跨策略比统计是把一个策略的收益读成另一个的退化。"""
    out = compare(_run(sid="s1"), _run(sid="s2", stats={"total_return": "-0.5"}))
    assert out["comparable"] is True          # 数据可比
    assert out["stats_identical"] is None     # 但统计没比
    assert any("不是同一个策略" in n for n in out["notes"])


def test_same_strategy_but_different_cost_model_is_explained_not_flagged():
    """实测形态：一次带 cost_model、一次不带 → 统计本就该不同。"""
    before = _run()
    after = _run(params={"cost_model": {"enabled": True, "commission_rate": "0.0003"}},
                 stats={"total_return": "0.09", "trade_count": 6, "final_equity": "100990"})
    out = compare(before, after)
    assert out["stats_identical"] is None, "参数不同还去比统计，必然误报缺陷"
    assert any("计算参数不同" in n for n in out["notes"])
    assert not any("实现缺陷" in n for n in out["notes"])


def test_no_shared_symbol_is_not_a_pass():
    after = _run()
    after["params"]["data_coverage"] = {"000001": {"fingerprint": FP}}
    out = compare(_run(), after)
    assert out["comparable"] is False
    assert out["stats_identical"] is None
    assert any("没有共同的标的" in n for n in out["notes"])


# ------------------------------------------------- CLI 契约（退出码）


def _cli(tmp_path, payload):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "--db", str(f)],
                          capture_output=True, text=True)


def test_cli_exit_zero_when_identical(tmp_path):
    r = _cli(tmp_path, {"before": _run(), "after": _run()})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "全部可比" in r.stdout


def test_cli_exit_one_when_fingerprint_changed(tmp_path):
    after = _run()
    after["params"]["data_coverage"]["600036"] = {"fingerprint": "c" * 64}
    r = _cli(tmp_path, {"before": _run(), "after": after})
    assert r.returncode == 1
    assert "已标注不可比区间" in r.stderr


def test_cli_exit_one_when_params_differ(tmp_path):
    r = _cli(tmp_path, {"before": _run(),
                        "after": _run(params={"initial_cash": "200000"})})
    assert r.returncode == 1
    assert "计算参数不同" in r.stderr or "没有任何可比区间" in r.stderr
