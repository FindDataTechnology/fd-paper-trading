import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useBars } from "../api/hooks";
import { useQuote } from "../realtime/useRealtime";
import { mid } from "../lib/format";
import type { Bar } from "../api/types";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"] as const;

interface LastBar {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

export default function PriceChart({ symbol, height = 380 }: { symbol: string; height?: number }) {
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("5m");
  const [chartType, setChartType] = useState<"candle" | "line">("candle");
  const { data: bars, isFetching } = useBars(symbol, timeframe);
  const quote = useQuote(symbol);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null>(null);
  const lastBarRef = useRef<LastBar | null>(null);

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#171a21" },
        textColor: "#e6e8eb",
      },
      grid: {
        vertLines: { color: "#2a2f3a" },
        horzLines: { color: "#2a2f3a" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2a2f3a" },
      timeScale: { borderColor: "#2a2f3a" },
      width: el.clientWidth,
      height,
    });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      lastBarRef.current = null;
    };
  }, []);

  // (Re)build the series when bars or chart type change.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !bars || bars.length === 0) return;
    if (seriesRef.current) {
      chart.removeSeries(seriesRef.current);
      seriesRef.current = null;
    }

    if (chartType === "candle") {
      const series = chart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      });
      series.setData(
        bars.map((b: Bar) => ({
          time: b.ts as UTCTimestamp,
          open: Number(b.open),
          high: Number(b.high),
          low: Number(b.low),
          close: Number(b.close),
        }))
      );
      seriesRef.current = series;
    } else {
      const series = chart.addLineSeries({ color: "#3b82f6", lineWidth: 2 });
      series.setData(
        bars.map((b: Bar) => ({ time: b.ts as UTCTimestamp, value: Number(b.close) }))
      );
      seriesRef.current = series;
    }

    const last = bars[bars.length - 1];
    lastBarRef.current = {
      time: last.ts as UTCTimestamp,
      open: Number(last.open),
      high: Number(last.high),
      low: Number(last.low),
      close: Number(last.close),
    };
    chart.timeScale().fitContent();
  }, [bars, chartType]);

  // Update the latest bar in place on each quote tick.
  useEffect(() => {
    if (!quote || !seriesRef.current || !lastBarRef.current) return;
    const m = mid(quote.bid, quote.ask);
    const lb = lastBarRef.current;
    const updated: LastBar = {
      time: lb.time,
      open: lb.open,
      high: Math.max(lb.high, m),
      low: Math.min(lb.low, m),
      close: m,
    };
    if (seriesRef.current.seriesType() === "Candlestick") {
      (seriesRef.current as ISeriesApi<"Candlestick">).update({
        time: updated.time,
        open: updated.open,
        high: updated.high,
        low: updated.low,
        close: updated.close,
      });
    } else {
      (seriesRef.current as ISeriesApi<"Line">).update({
        time: updated.time,
        value: m,
      });
    }
    lastBarRef.current = updated;
  }, [quote]);

  return (
    <div className="card col gap8">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row gap8">
          <strong>{symbol}</strong>
          {quote && (
            <span>
              <span className="muted">bid</span> {quote.bid}{" "}
              <span className="muted">ask</span> {quote.ask}{" "}
              <span className="muted">mid</span> {mid(quote.bid, quote.ask).toFixed(2)}
            </span>
          )}
        </div>
        <div className="row gap8">
          <select value={chartType} onChange={(e) => setChartType(e.target.value as "candle" | "line")}>
            <option value="candle">Candlestick</option>
            <option value="line">Line</option>
          </select>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value as (typeof TIMEFRAMES)[number])}>
            {TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div ref={containerRef} />
      <div className="row gap8" style={{ justifyContent: "space-between" }}>
        <span className="badge sim">simulated data</span>
        {isFetching && <span className="muted" style={{ fontSize: 12 }}>loading bars…</span>}
      </div>
    </div>
  );
}
