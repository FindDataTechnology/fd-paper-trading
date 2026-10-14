import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { EquityPointOut } from "../api/types";

function toTime(date: string): UTCTimestamp {
  // Equity points are daily ISO dates; treat them as UTC midnight timestamps.
  return Math.floor(new Date(`${date}T00:00:00Z`).getTime() / 1000) as UTCTimestamp;
}

/**
 * Line chart of a run's equity curve. Rebuilds the data when ``points``
 * changes; creates/tears down the chart on mount/unmount.
 */
export default function EquityCurveChart({
  points,
}: {
  points: EquityPointOut[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

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
      rightPriceScale: { borderColor: "#2a2f3a" },
      timeScale: { borderColor: "#2a2f3a" },
      width: el.clientWidth,
      height: 260,
    });
    chartRef.current = chart;
    const series = chart.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
    });
    seriesRef.current = series;

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
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const data = points.map((p) => ({
      time: toTime(p.date),
      value: Number(p.equity),
    }));
    series.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} style={{ width: "100%" }} />;
}
