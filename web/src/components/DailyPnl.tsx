import { useDailyPnl } from "../api/hooks";
import { money, pnlClass, signed } from "../lib/format";

/**
 * 账户日度盈亏（spec: daily-pnl-tracking）。
 *
 * 这个卡片**不再自己算**。改前它把持仓行上的终身 `realized_pnl` 求和、再拿成本价当
 * 未实现基准，于是 "Daily P&L" 实际是"开户以来"，而标题后还缀着 "(indicative)"
 * 当挡箭牌。现在三个数由 `GET /accounts/{id}/daily-pnl` 给出：今日已实现（日界之后
 * 的成交）+ 以**上一交易日收盘**为基准的未实现。缺口由服务端在 `gaps` 里点名，
 * 这里只负责显示——"能算的都算了"必须让人看得见。
 */
export default function DailyPnl({ accountId }: { accountId: string | null }) {
  const { data, isLoading } = useDailyPnl(accountId);

  if (!accountId) return null;
  if (isLoading || !data) {
    return (
      <div className="card" style={{ textAlign: "center" }}>
        <div className="muted" style={{ fontSize: 12 }}>Daily P&L</div>
        <div className="muted" style={{ fontSize: 20 }}>…</div>
      </div>
    );
  }

  const daily = Number(data.daily_pnl);
  const hasGaps = data.gaps.length > 0;
  const legacyFills = data.unattributed_fills ?? 0;
  // 两种"不完整"：估值缺口（有持仓算不出）与归属缺口（历史成交没记录逐笔盈亏）。
  const incomplete = hasGaps || legacyFills > 0;

  return (
    <div className="card" style={{ textAlign: "center" }} data-baseline={data.baseline}>
      <div className="muted" style={{ fontSize: 12 }}>
        Daily P&L{incomplete ? " (partial)" : ""}
      </div>
      <div className={pnlClass(daily)} style={{ fontSize: 24, fontWeight: 600 }}>
        {signed(daily)}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Realized today: {money(Number(data.realized_today))} | Unrealized vs previous
        close: {money(Number(data.unrealized))}
      </div>
      {hasGaps && (
        <div
          className="muted"
          style={{ fontSize: 11, marginTop: 4 }}
          title={data.gaps.map((g) => `${g.symbol}: ${g.reason}`).join("\n")}
        >
          Excluded: {data.gaps.map((g) => g.symbol).join(", ")}
        </div>
      )}
      {legacyFills > 0 && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          {legacyFills} fill(s) without per-fill P&L recorded
        </div>
      )}
    </div>
  );
}
