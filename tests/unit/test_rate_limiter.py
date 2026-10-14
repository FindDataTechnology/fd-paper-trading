"""Unit tests for the per-account order rate limiter."""

import uuid

from fd_trade.execution.service import RateLimiter


def test_token_bucket_limits_burst():
    rl = RateLimiter(rate=1000.0, burst=2)  # high refill; burst is the gate
    aid = uuid.uuid4()
    assert rl.allow(aid) is True
    assert rl.allow(aid) is True
    assert rl.allow(aid) is False


def test_accounts_have_independent_buckets():
    rl = RateLimiter(rate=1000.0, burst=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    assert rl.allow(a) is True
    assert rl.allow(a) is False
    assert rl.allow(b) is True  # other account unaffected
