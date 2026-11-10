"""判据探针窗：以源水位为右端（change1 task 6.5）。

从"400 天盲窗"改成"水位锚定的 35 天窗"，前提是**退化路径逐位不变**：问不到
水位时仍旧是 400 天。这个文件把四条退化路径都钉住，因为它们的共同失败形态是
"悄悄变成空窗口 → 判据未知"，而判据未知会被读成"这个源没有数据"。
"""
from __future__ import annotations

from datetime import date

from fd_paper_trading.strategy.runner import (
    CALIBER_PROBE_DAYS,
    CALIBER_PROBE_FALLBACK_DAYS,
    caliber_probe_window,
)

TODAY = date(2026, 11, 10)


def test_ok_watermark_anchors_window_at_as_of():
    start, end = caliber_probe_window(
        {"status": "ok", "as_of": "2026-07-10"}, TODAY)
    assert end == date(2026, 7, 10)
    assert (end - start).days == CALIBER_PROBE_DAYS


def test_no_watermark_keeps_the_fixed_fallback_window():
    """``None`` = 问不到，不是"没有数据"：退回 400 天，判据照常可评。"""
    start, end = caliber_probe_window(None, TODAY)
    assert end == TODAY
    assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS


def test_non_ok_status_falls_back():
    for status in ("no_data", "unreachable"):
        start, end = caliber_probe_window({"status": status, "as_of": None}, TODAY)
        assert end == TODAY
        assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS


def test_missing_as_of_falls_back():
    start, end = caliber_probe_window({"status": "ok", "as_of": None}, TODAY)
    assert end == TODAY
    assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS


def test_unparseable_as_of_falls_back():
    start, end = caliber_probe_window({"status": "ok", "as_of": "not-a-date"}, TODAY)
    assert end == TODAY
    assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS


def test_future_watermark_falls_back():
    """水位在未来是时钟/上游写错了。拿它当右端会得到一个空窗口，
    把配置错误伪装成数据缺失——所以按不可用处理。"""
    start, end = caliber_probe_window(
        {"status": "ok", "as_of": "2027-01-01"}, TODAY)
    assert end == TODAY
    assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS


def test_watermark_equal_to_today_is_usable():
    """今天是合法右端（新鲜源）：这条与"未来"只差一天，边界必须分明。"""
    start, end = caliber_probe_window(
        {"status": "ok", "as_of": TODAY.isoformat()}, TODAY)
    assert end == TODAY
    assert (end - start).days == CALIBER_PROBE_DAYS


def test_iso_datetime_as_of_is_truncated_to_date():
    """水位若带时间部分（上游写 Timestamp），取日期部分而不是解析失败。"""
    start, end = caliber_probe_window(
        {"status": "ok", "as_of": "2026-07-10T00:00:00Z"}, TODAY)
    assert end == date(2026, 7, 10)
    assert (end - start).days == CALIBER_PROBE_DAYS
