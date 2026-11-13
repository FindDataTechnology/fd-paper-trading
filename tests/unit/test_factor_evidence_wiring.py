"""因子证据脚本的口径接线：关系、粒度、标签源。

这三件都是 2026-11-13 在真库上撞出来的（详见
`scripts/factor_cross_section_astock.py` 的模块 docstring）：

- `semantic_observations_all` 与 `semantic_observations_read` 一字之差，差一整个分片；
  财务行全在 xinru2，而 `_all` 没并它 → 用 `_all` 读财务**恒零**，输出却长成
  "库里没有财务数据"。
- `concepts.frequency`（yearly）与 `semantic_observations.granularity`（year）是两套
  词表，写死 `quarter` 就把"净利润季度序列错绑"读成"没有数据"。
- `astock_daily` 在某历史窗口有 3370 个 entity 有行，但每只只有个位数 bar：
  "有行"不等于"够算 20 个交易日的前瞻收益"。

这些测试不看真库，只把**判据本身**钉住——尤其"浅的那条源必须被否掉、并换到深的那条"，
因为那是这次真跑里唯一会静默出事的地方（浅源也会返回数据，只是样本少到 IC 没意义）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import factor_cross_section_astock as fx  # noqa: E402
from _evidence_source import EvidenceSourceError  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeDB:
    """按 SQL 片段分派的假库；`calls` 记录每次执行，用来断言"取的是哪条源"。"""

    def __init__(self, *, relations=None, financials=None, prices=None,
                 foreign_tables=None, concepts=None, probes=None):
        self.relations = relations or []
        self.financials = financials or {}
        self.prices = prices or {}
        self.foreign_tables = foreign_tables or []
        self.concepts = concepts or []
        # 探针命中要按 (关系, 源) 判：只看关系名会让"某源在 _all 里只有本地一份"
        # 这种形态被误判成"这条源在 _all 里有数据"。
        self.probes = probes if probes is not None else {(r, "*") for r in self.relations}
        self.calls: list[tuple[str, tuple]] = []

    async def __call__(self, sql, *args):
        s = sql.strip()
        self.calls.append((s, args))
        if s.startswith("select id, code, frequency from concepts"):
            return self.concepts
        if s.startswith("select foreign_table_name from information_schema.foreign_tables"):
            return [{"foreign_table_name": t} for t in self.foreign_tables]
        if s.startswith("select 1 as x from"):
            rel, src = _rel_of(s), args[1]
            hit = (rel, src) in self.probes or (rel, "*") in self.probes
            return [{"x": 1}] if hit else []
        if s.startswith("select count(*) n from (select entity_id from"):
            rel = _rel_of(s)
            return [{"n": self.prices.get((rel, args[1]), 0)}]
        if s.startswith("select date from"):
            return [{"date": d} for d in self.financials.get("periods", [])]
        if s.startswith("select entity_id, date, concept_id, value from"):
            return self.financials.get("rows", [])
        if s.startswith("select entity_id, date, value from"):
            rel, src, ids = _rel_of(s), args[1], args[2]
            out = []
            for eid in ids:
                for k, v in enumerate(self.prices.get((rel, src, "series"), {}).get(eid, [])):
                    out.append({"entity_id": eid, "date": f"d{k:02}", "value": str(v)})
            return out
        raise AssertionError(f"未预期的 SQL: {s[:70]}")


def _rel_of(sql: str) -> str:
    """从 `from <rel>` / `from <rel> where` 里抠关系名。"""
    for token in sql.replace("\n", " ").split():
        if token.startswith("semantic_observations") or token.startswith("ft_sem_obs"):
            return token.split("(")[0].strip(",)")
    return ""


# ---- 1. 关系：看不见财务的那条必须被跳过 -------------------------------------


def test_fin_relation_skips_the_view_without_financials(monkeypatch):
    """`_all` 没行、`_read` 有行 ⇒ 选 `_read`，并把两条的诊断都留下。"""
    monkeypatch.setattr(fx, "FIN_RELATIONS",
                        ("semantic_observations_all", "semantic_observations_read"))
    db = FakeDB(relations=["semantic_observations_read"])
    rel, diag = run(fx._pick_fin_relation(db, 240))
    assert rel == "semantic_observations_read"
    assert any("semantic_observations_all" in d and "看不到" in d for d in diag), diag
    assert any("semantic_observations_read" in d and "有财务行" in d for d in diag), diag


def test_fin_relation_names_every_candidate_when_none_has_financials(monkeypatch):
    """都看不见财务时必须报错并点名——空手返回零行正是这次误读的源头。"""
    monkeypatch.setattr(fx, "FIN_RELATIONS",
                        ("semantic_observations_all", "semantic_observations_read"))
    db = FakeDB(relations=[])
    with pytest.raises(EvidenceSourceError) as ei:
        run(fx._pick_fin_relation(db, 240))
    msg = str(ei.value)
    assert "semantic_observations_all" in msg and "semantic_observations_read" in msg


# ---- 2. 粒度：两套词表要显式映射，未知就报错 ---------------------------------


@pytest.mark.parametrize("freq,expected", [("yearly", "year"),
                                           ("Quarterly", "quarter"),
                                           ("monthly", "month")])
def test_granularity_maps_declared_frequency(freq, expected):
    assert fx._granularity({"code": "x", "frequency": freq}) == expected


def test_granularity_refuses_unknown_frequency_instead_of_guessing():
    with pytest.raises(EvidenceSourceError) as ei:
        fx._granularity({"code": "financials.net_income", "frequency": "weekly"})
    assert "weekly" in str(ei.value), "报错要点名具体取值，否则读者不知道换代成什么了"


def test_concept_rows_names_the_missing_code():
    db = FakeDB(concepts=[{"id": 240, "code": "financials.net_income",
                           "frequency": "yearly"}])
    with pytest.raises(EvidenceSourceError) as ei:
        run(fx._concept_rows(db))
    msg = str(ei.value)
    assert "financials.equity" in msg and "price.close" in msg


# ---- 3. 截面：按 (期, entity) 对齐，权益为 0 的不进截面 -----------------------


def test_exposures_aligns_by_period_and_entity_and_drops_zero_equity():
    rows = [
        # 正常：ROE = 100/1000
        {"entity_id": 1, "date": "2024-12-31", "concept_id": 240, "value": "100"},
        {"entity_id": 1, "date": "2024-12-31", "concept_id": 244, "value": "1000"},
        # 权益为 0：不能算（除零会变成 inf，混进 IC 就是一条假信号）
        {"entity_id": 2, "date": "2024-12-31", "concept_id": 240, "value": "50"},
        {"entity_id": 2, "date": "2024-12-31", "concept_id": 244, "value": "0"},
        # 只有净利润、没有权益：不进截面
        {"entity_id": 3, "date": "2024-12-31", "concept_id": 240, "value": "70"},
        # 期别错开：不是同期的两行不能拼成一个 ROE
        {"entity_id": 4, "date": "2024-12-31", "concept_id": 240, "value": "10"},
        {"entity_id": 4, "date": "2023-12-31", "concept_id": 244, "value": "999"},
    ]
    db = FakeDB(financials={"rows": rows})
    out = run(fx._exposures(db, "semantic_observations_read", "year", 240, 244,
                            ["2024-12-31"]))
    assert out["2024-12-31"] == {1: pytest.approx(0.1)}


# ---- 4. 前瞻收益：源显式传入、要够 21 根、按第 21 根算 -----------------------


def test_forward_returns_pins_the_source_and_needs_21_bars():
    series = {
        1: [100.0] * 20 + [110.0],     # 21 根 → 可算，+10%
        2: [100.0] * 19,               # 20 根 → 不够，剔除
    }
    db = FakeDB(prices={("ft_sem_obs_xinru2", "akshare", "series"): series})
    out = run(fx._forward_returns(db, "ft_sem_obs_xinru2", "akshare", 234,
                                  "2024-05-01", "2024-09-01", [1, 2]))
    assert set(out) == {1}
    assert out[1] == pytest.approx(0.10)
    sql, args = db.calls[-1]
    assert args[1] == "akshare", "源必须来自参数——写死 source_used='astock_daily' 会静默混源"


# ---- 5. 标签源：浅的那条必须被否掉，换到深的那条 ----------------------------


def test_label_source_rejects_shallow_source_and_falls_through(monkeypatch):
    """生产形态：astock_daily 在历史窗口只有 18/200 只凑够 21 根，akshare 有全部。

    这条是本轮最该守的一条：**浅源也会返回数据**，如果只看"有没有行"，就会拿到
    18 只的截面并把它当成 IC 的证据。
    """
    monkeypatch.setattr(fx, "LABEL_SOURCES", ("astock_daily", "akshare"))
    monkeypatch.setattr(fx, "PRICE_RELATION", "semantic_observations_all")
    db = FakeDB(
        relations=["semantic_observations_all", "ft_sem_obs_xinru2"],
        foreign_tables=["ft_sem_obs_xinru2"],
        # akshare 在 `_all` 里也有零星本地行（真实形态），但深度为 0——
        # 判据必须按"深度"而不是"有没有行"。
        probes={("semantic_observations_all", "astock_daily"),
                ("semantic_observations_all", "akshare"),
                ("ft_sem_obs_xinru2", "akshare")},
        prices={
            ("semantic_observations_all", "astock_daily"): 18,   # 浅
            ("semantic_observations_all", "akshare"): 0,          # 本地零深度
            ("ft_sem_obs_xinru2", "akshare"): 200,                # 深
        })
    src, rel, diag = run(fx._pick_label_source(db, 234, list(range(200)),
                                              "2024-05-01", "2024-09-01"))
    assert (src, rel) == ("akshare", "ft_sem_obs_xinru2")
    assert any("astock_daily" in d and "18/200" in d for d in diag), diag


def test_label_source_refuses_when_no_source_is_deep_enough():
    db = FakeDB(relations=["semantic_observations_all"],
                prices={("semantic_observations_all", "astock_daily"): 3})
    with pytest.raises(EvidenceSourceError) as ei:
        run(fx._pick_label_source(db, 234, list(range(200)), "2024-05-01", "2024-09-01"))
    msg = str(ei.value)
    assert "3/200" in msg, "报错必须给读数，否则分不清'没数据'与'数据太浅'"
    assert "前瞻" in msg
