"""把 provider 的覆盖事实翻成 `PriceSeriesRef`（change1 task 4.3）。

## 为什么要这一层

provider 报的是**取数口径的人话**（``adjusted="forward"``、``source="astock_daily"``、
一个序列值的指纹）。契约要的是**可断言的四个字段**（``source_id``/``adjustment``/
``as_of_max``/``fingerprint``）。两者不是同一套词，所以中间必须有翻译，
而翻译一旦散落在调用点就会出现第三份口径。

## 两条硬纪律

1. **不确定就不出 ref。** 口径 ``unknown`` 不能映射成 ``none``——"没证明复权"和
   "确认不复权"是两件事，写错这一处会让"研究/交易/结算三路可比"这件事静默失真。
   出不来时返回**原因字符串**，让调用方按三态留痕，而不是留一个空对象。
2. **发出去之前先用契约自己的校验器验一遍。** 造 ref 的代码和校验 ref 的代码要是
   两份独立实现，否则"我造的我验"恒真。这里直接调 `check_price_series_ref`，
   它红了就说明我造错了，此时宁可不出 ref。

注意 ``fingerprint`` 有两个同名不同义的量：
- provider 覆盖结果里的 ``fingerprint`` 是**序列值**的指纹（gapfill 里算的）；
- 契约要的 ``fingerprint`` 是**身份字段**的指纹（源/标的/口径/水位/窗口），
  由 `compute_price_series_fingerprint` 算。
本模块用后者；拿前者填会得到"同样的输入、不同的指纹"，比不出 ref 更糟。
"""
from __future__ import annotations

from fd_paper_trading._vendor.fd_quant.contracts.models import (
    SUPPORTED_SCHEMA_VERSION,
    check_price_series_ref,
    compute_price_series_fingerprint,
)

#: provider 的口径词 → 契约的口径词。**只映射确定的三个**；
#: ``unknown`` 与任何没见过的取值都落到 ``None``（= 出不来 ref）。
CALIBER_BY_ADJUSTED = {
    "forward": "qfq",
    "backward": "hfq",
    "none": "none",
    "not_applicable": "not_applicable",
    "qfq": "qfq",           # 已经是契约词的直接放行（provider 演进时不必两处改）
    "hfq": "hfq",
}


def caliber_for(adjusted: str | None) -> str | None:
    """provider 口径词 → 契约口径词；不确定返回 ``None``。"""
    if not adjusted:
        return None
    return CALIBER_BY_ADJUSTED.get(str(adjusted).strip().lower())


def build_series_ref(
    *,
    source_id: str | None,
    symbol: str,
    adjusted: str | None,
    as_of_max: str | None,
    window_start: str,
    window_end: str,
    row_count: int,
    adjustment_anchor: str | None = None,
    db_source: str | None = None,
) -> tuple[dict | None, str | None]:
    """造一个 `PriceSeriesRef`；出不来说明原因。

    返回 ``(ref, None)`` 或 ``(None, reason)``。reason 取值：

    ``no_source_id`` / ``caliber_unknown`` / ``no_as_of_max`` / ``invalid:<字段>``
    """
    if not source_id:
        return None, "no_source_id"
    calibration = caliber_for(adjusted)
    if calibration is None:
        return None, "caliber_unknown"
    if not as_of_max:
        return None, "no_as_of_max"
    if calibration in ("qfq", "hfq") and not adjustment_anchor:
        # 契约的硬规则：前/后复权没有锚点，读者无法判断两批是否可比。
        # 与其猜一个锚点，不如不出 ref——锚点存在 ``declarations.yaml`` 里。
        return None, "no_adjustment_anchor"

    ref = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "source_id": source_id,
        "symbol": symbol,
        "adjustment": calibration,
        "as_of_max": as_of_max,
        "window_start": window_start,
        "window_end": window_end,
        "row_count": int(row_count),
        "fingerprint": compute_price_series_fingerprint(
            source_id=source_id,
            symbol=symbol,
            adjustment=calibration,
            as_of_max=as_of_max,
            adjustment_anchor=adjustment_anchor,
            db_source=db_source,
            window_start=window_start,
            window_end=window_end,
            row_count=int(row_count),
        ),
    }
    if adjustment_anchor is not None:
        ref["adjustment_anchor"] = adjustment_anchor
    if db_source is not None:
        ref["db_source"] = db_source

    errors = check_price_series_ref(ref)
    if errors:
        # 自己造的东西被契约拒了：不出 ref，把第一个错位点说出来。
        return None, f"invalid:{errors[0].get('path', '?')}"
    return ref, None
