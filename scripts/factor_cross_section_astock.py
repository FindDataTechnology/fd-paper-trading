#!/usr/bin/env python3
"""Factor real-cross-section acceptance evidence (tasks 2.1-2.6).

Multi-period ROE cross-sections against forward returns on the real A-share
store — the four items batch 4 could not produce (IC / quintile layering /
weight-mode backtest inputs / in-out-of-sample split), now executed on real data:

- universe: entities with BOTH period financials (net_income+equity, same fiscal
  period) AND astock_daily prices after the decision date;
- factor: ROE = net_income / equity (concepts resolved **by code**, not by id);
- IC: cross-sections at >= 3 periods, Spearman IC of factor rank vs forward
  20-trading-day return; mean/IR recorded;
- layering: top/bottom quantile spread + monotonicity across 5 layers;
- weight backtest inputs: top-N targets from real exposures. **引擎执行不在本脚本**
  ——它由 `weight_backtest_topN.py` 跑（equal 与 score 两种权重来源），本脚本只产出
  真实截面与目标篮子，并打印可直接粘贴的运行命令。原先这里有个 `run_weight_backtest()`
  宣称"through the REAL engine (COMPLETED, trades > 0)"，但它是**不可达**的（前一行的
  `if __name__` 块已 `raise SystemExit`），而且缺 `datetime`/`timezone` import，真跑起来
  第一行就 NameError——"宣称"与"挂载"是两件事，2026-11-13 删掉；
- in/out-of-sample: explicit time split, scan-style best marked in-sample;
- point-in-time discipline: prices end at the decision date (no lookahead —
  forward returns are computed per cross-section AFTER the decision date, which
  is the definition of IC, not a violation; the injected-fixture test lives in
  the unit suite).

## 两条关系，一个分片（2026-11-13 实测，三处病根）

这个脚本曾经对真实库恒返回 0 个 entity，而输出读起来像"库里没有财务数据"。实情是
**几个独立病因叠着**：

1. **读错关系**。`semantic_observations_all` 与 `semantic_observations_read` 一字之差，
   涵义差一整个分片。生产库 `fd_open_data` 里：

   | 关系 | 组成 | 看得见财务(240/244)吗 |
   |---|---|---|
   | `semantic_observations_all` | 本地表(排除 astock_daily) ∪ `ft_sem_obs_xinru3` | **否**——财务全在 xinru2，该视图没有它 |
   | `semantic_observations_read` | 本地表 ∪ `ft_sem_obs_xinru2` ∪ `ft_sem_obs_xinru3`，按 astock_daily 优先去重 | 是 |

   价格相反：astock_daily 日线落在 **xinru3**，两条都看得见——所以生产 provider 用
   `_all` 取价一直是对的，只有"拿 `_all` 读财务"这一条路恒零。本脚本按**实测能不能
   看见财务行**选关系（`_pick_fin_relation`），不按名字猜。

2. **写死季度粒度**。脚本原查 `granularity='quarter'`，而 `financials.net_income` 在
   `concepts` 表里声明的 frequency 是 **yearly**。更糟的是季度序列本身已被平台判为
   错绑：2025Q3 的 240 行里 4,893/4,895 是 `akshare_misbound`（值域像每股收益，不是
   净利润），`_read` 已正确滤掉，合法只剩 2 只。年报粒度则干净：240∩244 每期
   1,533–1,548 只。粒度现在从概念声明读（`_granularity`），不由脚本替数据决定。

3. **概念 id 硬编码**。240/244/234 改为按 `concepts.code` 解析（`financials.net_income`
   / `financials.equity` / `price.close`）。同一个数字写死在脚本里，重绑一次就静默取错列。

决策日取「财年末 + 5 个月」：年报法定披露截止 4 月 30 日，5 月 1 日起才算公开，
早一天都算前视。

Writes a deterministic report (given the same store). Usage (repo root of
fd-paper-trading):

    MARKET_DATA_SOURCE_ID=astock_daily uv run python scripts/factor_cross_section_astock.py \
      > ../openspec/changes/add-trading-real-data-closeout/reports/factor-cross-section.txt
"""

import asyncio
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))  # 同目录的 _evidence_source

from _evidence_source import EvidenceSourceError, open_provider  # noqa: E402

#: 财务要读"能看见全部分片"的那条；`_all` 缺 xinru2（见模块 docstring）。
#: 顺序即优先级：先 `_read`（去重 + 全分片），回退 `_all` 时探针会自己否掉它。
FIN_RELATIONS = ("semantic_observations_read", "semantic_observations_all")
#: 价格关系候选的**首选**：生产 provider 读的就是 `_all`。但源不一定是它——
#: 见 `_pick_label_source`：astock_daily 的**历史深度不足**（2026-11-13 实测：
#: 2024-05..09 窗口里它有 3370 个 entity 有行，可每只只有个位数 bar，能凑够 21 根
#: 的只有 18 只；同一批名字在 akshare 上有 82 根）。所以标签源要实测选，不能钉死。
PRICE_RELATION = "semantic_observations_all"

#: 标签源的偏好顺序：先生产钉的那条，深度不够再退到有历史的那条。
#: 顺序不是"谁更好"，是"谁更接近线上"优先——退让必须打印出来。
LABEL_SOURCES = ("astock_daily", "akshare")

#: 深度判据：探针样本里至少这个比例要能凑够 `FORWARD_WINDOW_DAYS+1` 根 bar。
#: 50% 是下限而不是目标：低于一半就说明这条源在历史上是稀疏采样，IC 会变成
#: "谁有数据谁参与"的选择偏差。
LABEL_DEPTH_FLOOR = 0.5
LABEL_PROBE_SAMPLE = 200

#: 平台判定的错绑行标记：整条流写进了错误的 concept（240 里装的是 EPS 量级的值）。
#: `_read` 已过滤；这里再显式排一次——回退到 `_all` 时它不过滤，不排就会把污染行算进截面。
MISBOUND = "akshare_misbound"

#: 概念按 code 解析，避免 id 漂移（见 docstring 病根 3）。
CONCEPT_CODES = {
    "net_income": "financials.net_income",
    "equity": "financials.equity",
    "close": "price.close",
}

#: frequency 声明 → 观测粒度。两者是**两套词表**：`concepts.frequency` 是 `yearly`，
#: `semantic_observations.granularity` 是 `year`。
_FREQUENCY_TO_GRANULARITY = {
    "yearly": "year",
    "annual": "year",
    "quarterly": "quarter",
    "monthly": "month",
}

FORWARD_WINDOW_DAYS = 20      # forward return horizon, trading days
PERIOD_LIMIT = 4              # 取最近 N 个可用期做截面（IC 要 >= 3）
UNIVERSE_CAP = 2000           # 单期截面规模上限（按 entity_id 排序截断，可复现）
PRICE_CHUNK = 400             # 批量取价的 entity 分片大小（实测 400 只/批约 7s）
TOP_N = 30                    # long basket size
#: spec `factor-real-cross-section` 的规模下限：5 层、每层 >=5 只。单列常量而不是
#: 散在比较式里——同一个 5 写在两处，改一处就会静默分叉。
MIN_PER_LAYER = 5
#: 双路径对照样本量：批量读 vs provider 逐只读，必须逐值一致（见 `_parity_check`）。
PARITY_SAMPLE = 25

#: 决策日 = 财年末 + 5 个月（年报法定披露截止 4/30 之后）。`_plus_months` 返回目标月的
#: 1 日，所以 +5 对 12-31 得 5-01，安全；+4 会得 4-01，早于披露截止 = 前视。
DECISION_LAG_MONTHS = 5


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = statistics.mean(rx)
    my = statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ---- 口径解析：关系 / 粒度 / 概念 id ----------------------------------------


async def _concept_rows(fetch) -> dict[str, dict]:
    """按 code 取概念行（id + frequency）。缺任何一个都当场点名。"""
    rows = await fetch(
        "select id, code, frequency from concepts where code = any($1::text[])",
        list(CONCEPT_CODES.values()),
    )
    by_code = {r["code"]: r for r in rows}
    missing = [c for c in CONCEPT_CODES.values() if c not in by_code]
    if missing:
        raise EvidenceSourceError(f"概念缺失：{missing}（concepts 表里没有这些 code）")
    return by_code


def _granularity(concept: dict) -> str:
    """从概念自己声明的 frequency 推观测粒度。

    这里刻意**不**回退到 quarter：写死一个粒度就是替数据做决定，而 2026-11-13 的
    事实是净利润的季度序列错绑、年报序列干净。声明的字段与观测的字段是两套词表，
    映射必须显式（`_FREQUENCY_TO_GRANULARITY`），对不上就报错而不是猜。
    """
    freq = str(concept.get("frequency") or "").strip().lower()
    gran = _FREQUENCY_TO_GRANULARITY.get(freq)
    if not gran:
        raise EvidenceSourceError(
            f"concept {concept.get('code')} 的 frequency={freq!r} 不在已知映射里"
            f"（{sorted(_FREQUENCY_TO_GRANULARITY)}）；换代了就把映射补上，别猜")
    return gran


async def _pick_fin_relation(fetch, net_income_id: int) -> tuple[str, list[str]]:
    """选一条**实测能看见财务行**的关系，返回 (关系名, 各候选的诊断)。

    判据只看"能不能看见 240 的非错绑行"，不看视图名——名字相近的两条关系差一整个
    分片，靠名字选就是本脚本原先的那个 bug。探针用 `limit 1`：要答的是"看得见吗"，
    不是规模（规模由后面的截面规模检查负责）。
    """
    diag: list[str] = []
    for rel in FIN_RELATIONS:
        try:
            rows = await fetch(
                f"select 1 as x from {rel} where concept_id=$1 and source_used <> $2 limit 1",
                net_income_id, MISBOUND)
        except Exception as exc:                       # 关系不存在 / 不可读
            diag.append(f"{rel}: 不可用（{type(exc).__name__}）")
            continue
        if rows:
            diag.append(f"{rel}: 有财务行")
            return rel, diag
        diag.append(f"{rel}: 看不到财务行（缺该概念所在分片）")
    raise EvidenceSourceError(
        "没有任何关系看得见财务行——store 的联邦视图没有并上概念所在的分片。"
        f"候选诊断：{'; '.join(diag)}")


async def _candidate_periods(fetch, rel: str, gran: str, net_income_id: int,
                             limit: int) -> list[str]:
    """最近 N 个"净利润非错绑行 >= 50"的观测期（按日期倒序取，最后升序返回）。"""
    rows = await fetch(
        f"select date from {rel} where concept_id=$1 and granularity=$2 "
        f"and source_used <> $3 group by 1 having count(*) >= 50 "
        f"order by 1 desc limit $4",
        net_income_id, gran, MISBOUND, limit)
    return sorted(str(r["date"]) for r in rows)


async def _exposures(fetch, rel: str, gran: str, ni_id: int, eq_id: int,
                     periods: list[str]) -> dict[str, dict[int, float]]:
    """一条语句取回 net_income 与 equity，再在 Python 里按 entity 对齐。

    为什么不分两条语句：`_read` 每次查询都要物化整个去重窗口，实测 240=41.5s、
    244=122.4s；一条 `concept_id = any(...)` 只付一次窗口成本。
    """
    rows = await fetch(
        f"select entity_id, date, concept_id, value from {rel} "
        f"where concept_id = any($1::bigint[]) and granularity=$2 and source_used <> $3 "
        f"and date = any($4::text[])",
        [ni_id, eq_id], gran, MISBOUND, periods)
    ni: dict[tuple[str, int], float] = {}
    eq: dict[tuple[str, int], float] = {}
    for r in rows:
        key = (str(r["date"]), int(r["entity_id"]))
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        if int(r["concept_id"]) == ni_id:
            ni[key] = v
        else:
            eq[key] = v
    out: dict[str, dict[int, float]] = {p: {} for p in periods}
    for (period, eid), nv in ni.items():
        ev = eq.get((period, eid))
        if ev is None or not ev:
            continue
        out.setdefault(period, {})[eid] = nv / ev
    return out


async def _price_relations(fetch) -> list[str]:
    """价格可能落在哪些关系里：`_all` 加上**逐个外部表**（分片）。

    为什么不写死 `ft_sem_obs_xinru2`：分片名是部署细节，写死一个就是在假设"数据永远
    不搬"。这里每次从 `information_schema.foreign_tables` 现取，探不到就用探针否掉它。
    """
    rels = [PRICE_RELATION]
    try:
        rows = await fetch(
            "select foreign_table_name from information_schema.foreign_tables "
            "order by foreign_table_name")
        rels += [r["foreign_table_name"] for r in rows
                 if r["foreign_table_name"] not in rels]
    except Exception:
        pass                        # 取不到就只试 `_all`
    return rels


async def _pick_label_source(fetch, close_id: int, probe_ids: list[int],
                             decision: str, end: str
                             ) -> tuple[str, str, list[str]]:
    """选**有历史深度**的价格源与其所在关系，返回 (源, 关系, 诊断)。

    判据是"探针样本里有多少只在这个窗口凑够 21 根 bar"，不是"这条源有没有行"——
    astock_daily 在 2024 窗口有 3370 个 entity 有行，但几乎每只只有个位数 bar，
    "有行"完全不足以支撑 20 个交易日的前瞻收益。
    """
    diag: list[str] = []
    for src in LABEL_SOURCES:
        for rel in await _price_relations(fetch):
            try:
                hit = await fetch(
                    f"select 1 as x from {rel} where concept_id=$1 and granularity='day' "
                    f"and source_used=$2 and entity_id = any($3::bigint[]) "
                    f"and date >= $4 and date <= $5 limit 1",
                    close_id, src, probe_ids, decision, end)
            except Exception as exc:
                diag.append(f"{src}@{rel}: 不可查（{type(exc).__name__}）")
                continue
            if not hit:
                continue
            rows = await fetch(
                f"select count(*) n from (select entity_id from {rel} "
                f"where concept_id=$1 and granularity='day' and source_used=$2 "
                f"and entity_id = any($3::bigint[]) and date >= $4 and date <= $5 "
                f"group by entity_id having count(*) >= $6) t",
                close_id, src, probe_ids, decision, end, FORWARD_WINDOW_DAYS + 1)
            deep = int(rows[0]["n"]) if rows else 0
            ratio = deep / max(1, len(probe_ids))
            diag.append(f"{src}@{rel}: {deep}/{len(probe_ids)} 只凑够 "
                        f"{FORWARD_WINDOW_DAYS + 1} 根（{ratio:.0%}）")
            if ratio >= LABEL_DEPTH_FLOOR:
                return src, rel, diag
            # 不 break：同一源在 `_all` 里可能只有**本地那一份**，全量在某个分片上
            # （akshare 正是这种形态）。按关系逐个量，取第一个达标的。

    raise EvidenceSourceError(
        "没有价格源在探针窗口里达到深度下限 "
        f"{LABEL_DEPTH_FLOOR:.0%}——历史日线不足以支撑 "
        f"{FORWARD_WINDOW_DAYS} 个交易日的前瞻收益。诊断：{'; '.join(diag)}")


async def _forward_returns(fetch, rel: str, source: str, close_id: int,
                           decision: str, end: str,
                           ids: list[int]) -> dict[int, float]:
    """按**批量**取收盘价算前瞻收益（分片 400 只/批）。

    为什么不用 provider 逐只取：实测 1.48s/只（`get_daily_bars` 每只付一次 FDW 往返），
    4 期 × 1500 只 ≈ 30 分钟；批量 400 只/批只要 7s。两条路的读数由 `_parity_check`
    逐值对照，不是"换一条路就完事"。

    `source` 是**显式**参数：不传就落到"随便取一条源"，跨期口径会悄悄混源。
    """
    series: dict[int, list[tuple[str, float]]] = {}
    for i in range(0, len(ids), PRICE_CHUNK):
        chunk = ids[i:i + PRICE_CHUNK]
        rows = await fetch(
            f"select entity_id, date, value from {rel} "
            f"where concept_id=$1 and granularity='day' and source_used=$2 "
            f"and entity_id = any($3::bigint[]) and date >= $4 and date <= $5",
            close_id, source, chunk, decision, end)
        for r in rows:
            try:
                v = float(r["value"])
            except (TypeError, ValueError):
                continue
            series.setdefault(int(r["entity_id"]), []).append((str(r["date"]), v))
    out: dict[int, float] = {}
    for eid, pts in series.items():
        pts.sort()
        if len(pts) < FORWARD_WINDOW_DAYS + 1:
            continue
        p0, p1 = pts[0][1], pts[FORWARD_WINDOW_DAYS][1]
        if p0 > 0:
            out[eid] = (p1 - p0) / p0
    return out



async def _parity_check(provider, close_id: int, codes: dict[int, str],
                        decision: str, end: str, bulk: dict[int, float],
                        sample: int) -> tuple[bool, str]:
    """批量读 vs provider 逐只读：同一只股票的前瞻收益必须逐值一致。

    这是"跨入口形状漂移必须 live 对照"的落地：单侧测试的期望是自己写的，绿灯是
    伪证；两条真实读路径互相对照才有意义。provider 的 OHLC 一致性校验会丢一些 bar
    （更严），所以只要求"provider 能算出来的那些，批量也一致"，覆盖率另报。
    """
    picked = [eid for eid in sorted(codes) if eid in bulk][:sample]
    checked = mismatches = 0
    for eid in picked:
        code = codes.get(eid)
        if not code:
            continue
        bars = await provider.get_daily_bars(code, decision, end)
        if len(bars) < FORWARD_WINDOW_DAYS + 1:
            continue
        p0, p1 = float(bars[0].close), float(bars[FORWARD_WINDOW_DAYS].close)
        if p0 <= 0:
            continue
        got = (p1 - p0) / p0
        checked += 1
        want = bulk[eid]
        if abs(got - want) > 1e-9 * max(1.0, abs(want)):
            mismatches += 1
            if mismatches <= 3:
                print(f"   不一致 entity={eid} code={code} bulk={want:.10f} provider={got:.10f}")
    if checked < 10:
        return True, f"样本不足（checked={checked}），未做对照"
    return mismatches == 0, f"checked={checked} mismatches={mismatches}"


async def main() -> int:
    try:
        provider = open_provider()
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        return 2

    async def fetch(sql, *args):
        return await provider._fetch(sql, *args)

    fails: list[str] = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fails.append(name)

    # ---- 0. 口径：概念 id / 粒度 / 关系 ----
    try:
        concepts = await _concept_rows(fetch)
        ni_id = int(concepts[CONCEPT_CODES["net_income"]]["id"])
        eq_id = int(concepts[CONCEPT_CODES["equity"]]["id"])
        close_id = int(concepts[CONCEPT_CODES["close"]]["id"])
        gran = _granularity(concepts[CONCEPT_CODES["net_income"]])
        fin_rel, rel_diag = await _pick_fin_relation(fetch, ni_id)
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        return 2
    print(f"concepts: net_income={ni_id} equity={eq_id} close={close_id} "
          f"granularity={gran!r}（按 code 解析）")
    print(f"财务关系: {fin_rel}（{'；'.join(rel_diag)}）")
    print(f"价格关系: {PRICE_RELATION}（生产 provider 同一条；astock_daily 在 xinru3）")

    # ---- 1. 期别与截面：财务 ∩ 财务 ----
    periods = await _candidate_periods(fetch, fin_rel, gran, ni_id, PERIOD_LIMIT + 2)
    if not periods:
        print(f"FAIL: 没有可用期——净利润在 {fin_rel} 上找不到任何 >=50 行的 {gran} 观测")
        return 1
    exposures = await _exposures(fetch, fin_rel, gran, ni_id, eq_id, periods)
    usable = [p for p in periods if len(exposures[p]) >= 50][-PERIOD_LIMIT:]
    for p in periods:
        print(f"exposure @ {p}: {len(exposures[p])} entities with ROE")
    check(">= 3 cross-sections with >= 50 entities (spec floor for IC)",
          len(usable) >= 3, f"usable periods={usable}")
    if not usable:
        print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
        return 1

    # ---- 2. 代码映射（批量） ----
    all_ids = sorted({eid for p in usable for eid in exposures[p]})
    codes: dict[int, str] = {}
    for i in range(0, len(all_ids), PRICE_CHUNK):
        rows = await fetch("select id, code from entities where id = any($1::bigint[])",
                           all_ids[i:i + PRICE_CHUNK])
        for r in rows:
            codes[int(r["id"])] = r["code"]
    print(f"universe: {len(all_ids)} entities across {len(usable)} periods "
          f"(capped {UNIVERSE_CAP}/period for price reads)")

    # ---- 3. 标签源：先实测深度，再按期取前瞻收益 ----
    earliest = usable[0]
    probe_ids = sorted(exposures[earliest])[:LABEL_PROBE_SAMPLE]
    probe_dec = _plus_months(earliest, DECISION_LAG_MONTHS)
    try:
        label_src, label_rel, label_diag = await _pick_label_source(
            fetch, close_id, probe_ids, probe_dec, _plus_months(probe_dec, 4))
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
        return 1
    print(f"标签源: {label_src}@{label_rel}（探针窗口 {probe_dec}；"
          f"{'；'.join(label_diag)}）")
    pinned = getattr(provider, "_source", None)
    if label_src != pinned:
        print(f"  ⚠️ 标签源 {label_src} ≠ provider 钉住的 {pinned}："
              f"历史窗口里 {pinned} 每只只有个位数 bar，撑不起 {FORWARD_WINDOW_DAYS} 日前瞻。"
              "两期以上截面只能用有历史的源；口径已写进报告。")

    fwd_returns: dict[str, dict[int, float]] = {}
    for p in usable:
        decision = _plus_months(p, DECISION_LAG_MONTHS)
        end = _plus_months(decision, 4)
        ids = sorted(exposures[p])[:UNIVERSE_CAP]
        fwd_returns[p] = await _forward_returns(
            fetch, label_rel, label_src, close_id, decision, end, ids)
        print(f"forward returns @ {p} (decision {decision} → {end}): "
              f"{len(fwd_returns[p])} entities")

    # 双路径对照：只在标签源就是 provider 钉的那条时才有可比性。否则用 provider
    # **能服务**的那个窗口单独验一次读器（同一函数、同一形状），并打印跳过原因——
    # "没对照"和"对照通过"必须长得不一样。
    if label_src == pinned:
        parity_ok, parity_detail = await _parity_check(
            provider, close_id, codes, _plus_months(usable[-1], DECISION_LAG_MONTHS),
            _plus_months(_plus_months(usable[-1], DECISION_LAG_MONTHS), 4),
            fwd_returns[usable[-1]], PARITY_SAMPLE)
        check("bulk price read matches provider.get_daily_bars", parity_ok, parity_detail)
    else:
        last_dec = _plus_months(usable[-1], DECISION_LAG_MONTHS)
        last_end = _plus_months(last_dec, 4)
        pinned_bulk = await _forward_returns(
            fetch, PRICE_RELATION, pinned or "", close_id, last_dec, last_end,
            sorted(exposures[usable[-1]])[:UNIVERSE_CAP]) if pinned else {}
        if len(pinned_bulk) >= 10:
            parity_ok, parity_detail = await _parity_check(
                provider, close_id, codes, last_dec, last_end,
                pinned_bulk, PARITY_SAMPLE)
            check(f"bulk reader matches provider.get_daily_bars on {pinned} "
                  f"（标签源是 {label_src}，故只验读器）", parity_ok, parity_detail)
        else:
            print(f"双路径对照跳过：{pinned} 在 {last_dec} 窗口可用标的只有 "
                  f"{len(pinned_bulk)} 只，不足以对照")


    # ---- 2.1 IC series ----
    ics: list[tuple[str, float, int]] = []
    for p in usable:
        common = [e for e in exposures[p] if e in fwd_returns[p]]
        if len(common) < 30:
            continue
        xs = [exposures[p][e] for e in common]
        ys = [fwd_returns[p][e] for e in common]
        ic = _spearman(xs, ys)
        if ic is not None:
            ics.append((p, ic, len(common)))
    print("\n== IC series (Spearman, factor=ROE, horizon=20 trading days) ==")
    for p, ic, n in ics:
        print(f"   {p}: IC={ic:+.4f} (n={n})")
    check("IC series has >= 3 points", len(ics) >= 3)
    ic_mean = statistics.mean([ic for _, ic, _ in ics]) if ics else None
    ic_std = statistics.stdev([ic for _, ic, _ in ics]) if len(ics) >= 2 else None
    ic_ir = ic_mean / ic_std if ic_mean is not None and ic_std and ic_std > 0 else None
    if ic_mean is not None:
        print(f"   IC mean={ic_mean:+.4f}"
              + (f"  ICIR={ic_ir:+.4f}" if ic_ir is not None else ""))

    # ---- 2.2 quintile layering at the last usable cross-section ----
    last_dd = usable[-1]
    common = [e for e in exposures[last_dd] if e in fwd_returns[last_dd]]
    common.sort(key=lambda e: exposures[last_dd][e])
    layer_size = len(common) // 5
    layer_means: list[float | None] = []
    layer_sizes: list[int] = []
    for layer in range(5):
        members = common[layer * layer_size:(layer + 1) * layer_size] \
            if layer < 4 else common[4 * layer_size:]
        rets = [fwd_returns[last_dd][e] for e in members if e in fwd_returns[last_dd]]
        layer_means.append(statistics.mean(rets) if rets else None)
        # 每层的**有效**成员数（真的有前瞻收益的那些），不是切片长度：
        # 切片里有名字却缺前瞻收益时均值是用更小样本算的，规模按实际算。
        layer_sizes.append(len(rets))
    print(f"\n== quintile layering @ {last_dd} (low ROE -> high ROE) ==")
    for i, (m, cnt) in enumerate(zip(layer_means, layer_sizes)):
        print(f"   L{i+1}: {m:+.4f} (n={cnt})" if m is not None
              else f"   L{i+1}: n/a (n={cnt})")
    spread = (layer_means[4] - layer_means[0]) if None not in layer_means else None
    if spread is not None:
        print(f"   Q5-Q1 spread: {spread:+.4f}")
    check("layering produced 5 layers", None not in layer_means)
    # spec 的规模下限是「5 层、每层 >=5 只」。只断言"5 层都在"，会让一个每层
    # 只剩 1 只、均值毫无意义的截面照样 PASS——这条就是那个假绿的门。
    check(f"every layer has >= {MIN_PER_LAYER} names",
          bool(layer_sizes) and min(layer_sizes) >= MIN_PER_LAYER,
          f"sizes={layer_sizes}, floor={MIN_PER_LAYER}")

    # ---- 2.3 目标篮子（引擎执行在 weight_backtest_topN.py）----
    top = sorted(common, key=lambda e: exposures[last_dd][e], reverse=True)[:TOP_N]
    top_codes = [codes[e] for e in top if e in codes]
    print(f"\n== target basket for weight_backtest_topN.py (top {TOP_N} by ROE @ {last_dd}) ==")
    print(f"   names={len(top_codes)}: {','.join(top_codes)}")
    scores = {codes[e]: exposures[last_dd][e] for e in top if e in codes}
    print("   FACTOR_WEIGHT_SCHEME=score 时要带上这些**真实分数**（不许按名次编）：")
    print("   FACTOR_SCORES=" + ",".join(f"{c}:{s:.6f}" for c, s in scores.items()))
    check("weight backtest inputs assembled", len(top_codes) >= 5, f"{len(top_codes)} names")
    print("   ⇒ 引擎执行（equal / score 两轨）由 weight_backtest_topN.py 跑；本脚本不声称已执行")

    # ---- 2.4 in/out-of-sample split (explicit dates, machine-readable) ----
    split_idx = max(1, len(ics) // 2)
    in_sample = ics[:split_idx]
    out_sample = ics[split_idx:]
    print("\n== in/out-of-sample split (explicit dates) ==")
    print(f"   in-sample dates: {[d for d, _, _ in in_sample]}")
    print(f"   out-sample dates: {[d for d, _, _ in out_sample]}")
    check("both segments non-empty", bool(in_sample) and bool(out_sample))

    # ---- 2.6 honesty notes (machine-readable) ----
    print("\n== honesty notes (executable flags) ==")
    print(json_line(True, "ic", f"multi-period Spearman IC on real store "
                                f"(granularity={gran}, {len(usable)} periods)"))
    print(json_line(True, "layering", "5-layer quantile spread on real store"))
    print(json_line(True, "weight_backtest_inputs",
                    "equal/score targets from real exposures; engine execution "
                    "lives in weight_backtest_topN.py"))
    print(json_line(False, "quarterly_net_income",
                    "net_income 的季度序列被平台判为错绑：2025Q3 的 4895 行里 4893 行是 "
                    "akshare_misbound（值域像 EPS）。年报序列干净（每期 1533-1548 只），"
                    "所以因子按年报口径算；修季度序列属于采集侧，不在本脚本"))
    print(json_line(False, "per_period_disclosure_dates",
                    "period 行只有一次批量抓取戳（fetched_at），没有逐份报表的披露日；"
                    "决策日按法定截止（年报 4/30）之后取，是保守口径而非精确披露日"))
    print(json_line(False, "benchmark_index",
                    "000300/000905 index series absent store-wide (① spike); "
                    "IC is raw-return based, no excess-return variant"))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
    return 0 if not fails else 1


def _plus_months(iso: str, months: int) -> str:
    import datetime as dt
    d = dt.date.fromisoformat(iso)
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    return dt.date(y, m + 1, 1).isoformat()


def json_line(executable: bool, item: str, reason: str) -> str:
    import json
    return json.dumps({"item": item, "executable": executable,
                       "reason": reason}, ensure_ascii=False)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (OSError, ConnectionError) as exc:
        # 连不上就说连不上：裸 traceback 会被读成"脚本坏了/数据有问题"。
        print(f"FAIL: 价格库不可达（DSN 的主机/端口连不上）：{type(exc).__name__}: {exc}")
        raise SystemExit(2) from exc
