"""Weight-track spec validation through the API (task 2.1) and the authoring
surface exposed with the catalog (task 2.2).

Task 2.1 asks for one case per violation class, each answering `400`-family with
the violations enumerated — so the assertions here check *which* violation is
named, not merely that the request failed. A test that only asserted a 4xx would
pass if the endpoint rejected everything for an unrelated reason.

The engine-level rules (universe dedup, every_n_bars, the two-source rule, weight
legality) are exercised here through the API because that is where the spec puts
the requirement; the rule implementations themselves are covered by
``test_weight_contract.py`` and the spec normalizer's own tests.
"""

import pytest

from fd_paper_trading.persistence.models import (
    DEFAULT_GROUP_CONFIG,
    DEFAULT_GROUP_ID,
    UserGroup,
)
from fd_paper_trading.persistence.repositories import UserRepository

WEIGHT_SOURCE = "def target_weights(ctx):\n    return {'A': 0.5}\n"


async def _enable_custom_code(app, email="user@example.com"):
    """A code-track weight spec is gated by the group's ``allow_custom_code``
    (default off), so these tests opt in explicitly — the gate itself is asserted
    separately, and letting it fire here would make every code-track case fail for
    a reason unrelated to what it is checking."""
    async with app.state.db.session_factory() as session:
        user = await UserRepository(session).get_by_email(email)
        session.add(
            UserGroup(
                id=DEFAULT_GROUP_ID,
                name="default",
                config={**DEFAULT_GROUP_CONFIG, "allow_custom_code": True},
            )
        )
        user.group_id = DEFAULT_GROUP_ID
        await session.commit()


async def _create(client, headers, spec, name="w"):
    return await client.post(
        "/api/v1/strategies",
        json={"name": name, "description": "d", "spec": spec},
        headers=headers,
    )


def _violations(body: dict) -> list[str]:
    """Every violation message the API returned, flattened for substring checks."""
    fields = (body.get("error") or body).get("fields") or []
    return [f.get("message", "") for f in fields]


def _base(**overrides) -> dict:
    spec = {
        "kind": "weight",
        "universe": ["A", "B"],
        "rebalance": {"every_n_bars": 5},
        "source": WEIGHT_SOURCE,
    }
    spec.update(overrides)
    return spec


# ---------------------------------------------------------------------------
# 2.1 — the happy path, so the rejections below mean something
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_weight_spec_creates_version_1(client, app, auth_headers):
    headers = await auth_headers()
    await _enable_custom_code(app)
    r = await _create(client, headers, _base())
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["version"] == 1
    assert body["spec"]["universe"] == ["A", "B"]
    assert body["spec"]["rebalance"]["every_n_bars"] == 5
    assert "weights code over 2 symbol(s)" in body["spec_summary"]


@pytest.mark.asyncio
async def test_valid_static_series_spec_creates(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 2},
            "weights_series": {"2026-01-02": {"A": 0.5, "B": 0.4}},
        },
    )
    assert r.status_code == 201, r.json()
    assert r.json()["spec"]["weights_series"] == {"2026-01-02": {"A": 0.5, "B": 0.4}}


# ---------------------------------------------------------------------------
# 2.1 — one case per violation class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_universe_rejected_naming_universe(client, auth_headers):
    headers = await auth_headers()
    r = await _create(client, headers, _base(universe=[]))
    assert r.status_code in (400, 422), r.json()
    assert any("universe" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_duplicate_universe_rejected(client, auth_headers):
    headers = await auth_headers()
    r = await _create(client, headers, _base(universe=["A", "A"]))
    assert r.status_code in (400, 422), r.json()
    assert any("duplicate" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_non_positive_every_n_bars_rejected(client, auth_headers):
    headers = await auth_headers()
    for bad in (0, -3):
        r = await _create(client, headers, _base(rebalance={"every_n_bars": bad}))
        assert r.status_code in (400, 422), (bad, r.json())
        assert any("every_n_bars" in v for v in _violations(r.json())), (bad, r.json())


@pytest.mark.asyncio
async def test_both_sources_rejected(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(weights_series={"2026-01-02": {"A": 0.5}}),
    )
    assert r.status_code in (400, 422), r.json()
    assert any("exactly one" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_neither_source_rejected(client, auth_headers):
    headers = await auth_headers()
    spec = _base()
    del spec["source"]
    r = await _create(client, headers, spec)
    assert r.status_code in (400, 422), r.json()
    assert any("exactly one" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_illegal_series_sum_rejected_naming_date_and_sum(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(source=None, weights_series={"2026-01-02": {"A": 0.7, "B": 0.5}}),
    )
    assert r.status_code in (400, 422), r.json()
    joined = " ".join(_violations(r.json()))
    assert "2026-01-02" in joined, f"the offending date must be named: {joined}"
    assert "1.2" in joined, f"the offending sum must be named: {joined}"


@pytest.mark.asyncio
async def test_negative_weight_rejected(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(source=None, weights_series={"2026-01-02": {"A": -0.1}}),
    )
    assert r.status_code in (400, 422), r.json()
    assert any("non-negative" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_series_symbol_outside_universe_rejected(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(source=None, weights_series={"2026-01-02": {"Z": 0.5}}),
    )
    assert r.status_code in (400, 422), r.json()
    assert any("not in universe" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_non_iso_series_key_rejected(client, auth_headers):
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(source=None, weights_series={"January": {"A": 0.5}}),
    )
    assert r.status_code in (400, 422), r.json()
    assert any("ISO date" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_source_without_entry_point_rejected(client, app, auth_headers):
    """The weight track's entry point is target_weights, not on_bar."""
    headers = await auth_headers()
    await _enable_custom_code(app)
    r = await _create(client, headers, _base(source="def on_bar(ctx):\n    pass\n"))
    assert r.status_code in (400, 422), r.json()
    assert any("target_weights" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_source_with_import_rejected(client, app, auth_headers):
    headers = await auth_headers()
    await _enable_custom_code(app)
    r = await _create(
        client, headers, _base(source="import os\ndef target_weights(ctx):\n    return {}\n")
    )
    assert r.status_code in (400, 422), r.json()
    assert any("import" in v for v in _violations(r.json())), r.json()


@pytest.mark.asyncio
async def test_illegal_weights_are_not_normalized_and_nothing_is_stored(
    client, auth_headers
):
    """A rejected spec must not leave a strategy behind, scaled or otherwise."""
    headers = await auth_headers()
    r = await _create(
        client, headers,
        _base(source=None, weights_series={"2026-01-02": {"A": 0.7, "B": 0.5}}),
        name="not-stored",
    )
    assert r.status_code in (400, 422)
    listed = await client.get("/api/v1/strategies", headers=headers)
    assert [s for s in listed.json() if s["name"] == "not-stored"] == []


# ---------------------------------------------------------------------------
# 2.3 — the old kinds still validate and version-bump
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_and_code_tracks_unchanged(client, auth_headers):
    headers = await auth_headers()
    t = await _create(
        client, headers,
        {"kind": "template", "template": "sma_cross", "symbol": "AAPL",
         "params": {"fast": 2, "slow": 3, "quantity": 10}},
        name="t",
    )
    assert t.status_code == 201, t.json()
    c = await _create(
        client, headers,
        {"kind": "code", "symbol": "AAPL", "source": "def on_bar(ctx): pass"},
        name="c",
    )
    # The default group has allow_custom_code off; the rejection must be the
    # group gate, not a spec error, which is what this asserts.
    assert c.status_code in (201, 403), c.json()
    if c.status_code == 403:
        assert c.json()["error"]["code"] == "custom_code_not_allowed"


@pytest.mark.asyncio
async def test_editing_a_weight_spec_bumps_the_version(client, app, auth_headers):
    headers = await auth_headers()
    await _enable_custom_code(app)
    created = (await _create(client, headers, _base())).json()
    sid = created["id"]
    r = await client.patch(
        f"/api/v1/strategies/{sid}",
        json={"spec": _base(universe=["A", "B", "C"])},
        headers=headers,
    )
    assert r.status_code == 200, r.json()
    assert r.json()["version"] == 2
    assert r.json()["spec"]["universe"] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 2.2 — the catalog exposes the weight track's authoring surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_exposes_weight_track_metadata(client, auth_headers):
    headers = await auth_headers()
    r = await client.get("/api/v1/strategy-catalog", headers=headers)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["spec_kinds"] == ["template", "code", "weight"]
    # The template half is the same list the dedicated endpoint serves.
    assert {t["id"] for t in body["templates"]} == {
        "sma_cross", "rsi_reversion", "bollinger_breakout", "macd_cross", "dca",
    }
    wt = body["weight_track"]
    assert wt["kind"] == "weight"
    assert wt["rebalance"]["field"] == "every_n_bars"
    assert wt["rebalance"]["minimum"] == 1
    assert wt["universe"]["max_size"] == 20
    assert wt["universe"]["max_size_config_key"] == "max_universe_size"
    assert {s["form"] for s in wt["sources"]} == {"source", "weights_series"}
    assert wt["exactly_one_source"] == ["source", "weights_series"]


@pytest.mark.asyncio
async def test_catalog_metadata_is_machine_readable_and_complete(client, auth_headers):
    """Every field a client needs to author a weight spec without guessing."""
    headers = await auth_headers()
    wt = (await client.get("/api/v1/strategy-catalog", headers=headers)).json()[
        "weight_track"
    ]
    # Authoring needs: the universe bounds, the rebalance field name and type, and
    # both source forms with their field names — nothing else may be required.
    assert isinstance(wt["universe"]["min_size"], int)
    assert isinstance(wt["universe"]["max_size"], int)
    assert wt["rebalance"]["type"] == "integer"
    fields = {s["field"] for s in wt["sources"]}
    assert fields == {"source", "weights_series"}
    series_form = next(s for s in wt["sources"] if s["form"] == "weights_series")
    assert "mapping" in series_form["period_forms"]
    assert "target_weights_instance" in series_form["period_forms"]


@pytest.mark.asyncio
async def test_template_list_endpoint_shape_unchanged(client, auth_headers):
    """The metadata must not have been smuggled into the old list endpoint."""
    headers = await auth_headers()
    r = await client.get("/api/v1/strategy-templates", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list), "the list endpoint must stay a bare array"
    assert all(set(t) == {"id", "name", "description", "params_schema"} for t in body)
