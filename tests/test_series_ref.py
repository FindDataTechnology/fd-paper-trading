"""交易路附带 `PriceSeriesRef`（change1 task 4.3）。

判据是「落库含四个字段」，而这条判据有三种失败形态，各配一条用例：

1. 造不出来却硬造 → 口径 `unknown` 被写成 `none`，三路可比性静默失真；
2. 出来了但契约不认 → 调用方拿到一个它无法断言的形状；
3. 出不来时什么都不说 → 「没问过」与「问过、没有」分不开。

还有一条是**指纹语义**的坑：provider 覆盖结果里的 `fingerprint` 是序列值指纹，
契约要的是身份指纹。用错那个，同样的输入会得到不同的指纹。
"""
from __future__ import annotations

from fd_paper_trading._vendor.fd_quant.contracts.models import (
    compute_price_series_fingerprint,
)
from fd_paper_trading.marketdata.series_ref import build_series_ref, caliber_for

BASE = dict(
    source_id="astock_daily", symbol="600036", adjusted="forward",
    as_of_max="2026-07-10", window_start="2025-07-10", window_end="2026-07-10",
    row_count=244, adjustment_anchor="2026-07-10",
)


# ------------------------------------------------------- 口径映射


def test_forward_maps_to_qfq():
    assert caliber_for("forward") == "qfq"
    assert caliber_for("backward") == "hfq"
    assert caliber_for("none") == "none"
    assert caliber_for("not_applicable") == "not_applicable"


def test_contract_vocabulary_passes_through():
    """已经是契约词的取值不该再被映射一遍（否则 provider 演进要改两处）。"""
    assert caliber_for("qfq") == "qfq"
    assert caliber_for("hfq") == "hfq"


def test_unknown_caliber_is_not_mapped_to_none():
    """**最关键的一条**：`unknown` = 证明不了复权，不能当"确认不复权"。"""
    assert caliber_for("unknown") is None
    assert caliber_for("") is None
    assert caliber_for(None) is None
    assert caliber_for("whatever") is None


def test_unknown_caliber_withholds_the_ref():
    ref, reason = build_series_ref(**{**BASE, "adjusted": "unknown"})
    assert ref is None
    assert reason == "caliber_unknown"


# ------------------------------------------------------- 三个"出不来"的原因


def test_missing_source_id_withholds_the_ref():
    ref, reason = build_series_ref(**{**BASE, "source_id": None})
    assert ref is None and reason == "no_source_id"


def test_missing_as_of_max_withholds_the_ref():
    ref, reason = build_series_ref(**{**BASE, "as_of_max": None})
    assert ref is None and reason == "no_as_of_max"


# ------------------------------------------------------- 出得来时的形状


def test_ref_carries_the_four_required_fields():
    ref, reason = build_series_ref(**BASE)
    assert reason is None
    for key in ("source_id", "adjustment", "as_of_max", "fingerprint"):
        assert ref[key], f"{key} 不得为空"
    assert ref["adjustment"] == "qfq"
    assert ref["as_of_max"] == "2026-07-10"
    assert ref["row_count"] == 244


def test_ref_passes_the_contracts_own_validator():
    from fd_paper_trading._vendor.fd_quant.contracts.models import (
        check_price_series_ref,
    )

    ref, _ = build_series_ref(**BASE)
    assert check_price_series_ref(ref) == []


def test_fingerprint_is_the_identity_fingerprint_not_the_value_one():
    """钉住"用哪个指纹"：身份指纹 = compute_price_series_fingerprint 的结果。

    这条设计的意图是防止有人图省事，把 provider 覆盖结果里的
    ``fingerprint``（序列值指纹）直接塞进 ref——那会让"同样输入、不同指纹"，
    比不出 ref 更糟。
    """
    ref, _ = build_series_ref(**BASE)
    expected = compute_price_series_fingerprint(
        source_id=BASE["source_id"], symbol=BASE["symbol"],
        adjustment="qfq", as_of_max=BASE["as_of_max"],
        adjustment_anchor=BASE["adjustment_anchor"], db_source=None,
        window_start=BASE["window_start"], window_end=BASE["window_end"],
        row_count=BASE["row_count"],
    )
    assert ref["fingerprint"] == expected


def test_watermark_moving_changes_the_fingerprint():
    """水位前移 → 指纹必变。这是"两次结果不一致可归因为输入变了"的机器判据。"""
    a, _ = build_series_ref(**BASE)
    b, _ = build_series_ref(**{**BASE, "as_of_max": "2026-07-11"})
    assert a["fingerprint"] != b["fingerprint"]


def test_anchor_and_db_source_are_carried_as_given():
    ref, _ = build_series_ref(**BASE, db_source="astock_daily")
    assert ref["adjustment_anchor"] == "2026-07-10"
    assert ref["db_source"] == "astock_daily"


def test_qfq_without_anchor_withholds_the_ref():
    """契约的硬规则：前复权没有锚点，读者无法判断两批是否可比 → 不出 ref。

    这条是最容易被"顺手补个 None"绕过的地方：``adjustment_anchor: null`` 会被
    校验器拒（qfq/hfq 必填），而"补一个假锚点"会让不可比的两批看起来可比。
    """
    ref, reason = build_series_ref(**{**BASE, "adjustment_anchor": None})
    assert ref is None
    assert reason == "no_adjustment_anchor"


def test_unadjusted_needs_no_anchor():
    """``none`` / ``not_applicable`` 不要求锚点（没有复权就没有锚点问题）。"""
    ref, reason = build_series_ref(**{**BASE, "adjusted": "none",
                                      "adjustment_anchor": None})
    assert reason is None and ref["adjustment"] == "none"
    assert "adjustment_anchor" not in ref


def test_a_ref_the_contract_rejects_is_withheld_not_shipped():
    """造错了宁可不出：契约拒绝时必须返回原因，而不是把坏形状发出去。"""
    # 用契约不认的 schema_version 反证：走"自校验"这条路径
    import fd_paper_trading.marketdata.series_ref as m

    original = m.SUPPORTED_SCHEMA_VERSION
    try:
        m.SUPPORTED_SCHEMA_VERSION = "999"
        ref, reason = build_series_ref(**BASE)
    finally:
        m.SUPPORTED_SCHEMA_VERSION = original
    assert ref is None
    assert reason.startswith("invalid:")


# ------------------------------------------------------- 落库三态（路由层）

def test_route_lifts_a_valid_ref_into_run_params():
    from fd_paper_trading.api.routes.strategies import _series_ref_params

    ref, _ = build_series_ref(**BASE)
    out = _series_ref_params({"series_ref": ref})
    assert out["price_series_ref"] is ref
    assert "price_series_ref_unavailable" not in out


def test_route_reports_the_reason_when_the_provider_could_not_build_one():
    from fd_paper_trading.api.routes.strategies import _series_ref_params

    out = _series_ref_params({"series_ref_unavailable": "caliber_unknown"})
    assert out == {"price_series_ref_unavailable": "caliber_unknown"}


def test_route_says_nothing_when_the_provider_has_no_ref_capability():
    """simulated 等 provider 连 reason 都不报 → 两个键都不写。

    三种状态必须能分开：出了 / 出不来（有原因）/ 没这个能力（没这个键）。
    """
    from fd_paper_trading.api.routes.strategies import _series_ref_params

    assert _series_ref_params({"adjusted": "none"}) == {}


def test_route_withholds_a_ref_the_contract_rejects():
    """provider 造错了，路由这一关也要拦——两份实现才拦得住。"""
    from fd_paper_trading.api.routes.strategies import _series_ref_params

    bad = {"source_id": "", "symbol": "600036", "adjustment": "qfq",
           "as_of_max": "2026-07-10", "fingerprint": "short",
           "schema_version": "1"}
    out = _series_ref_params({"series_ref": bad})
    assert "price_series_ref" not in out
    assert out["price_series_ref_unavailable"].startswith("invalid:")
