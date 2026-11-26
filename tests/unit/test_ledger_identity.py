"""落账调用者身份的判据（spec `paper-run-decision-ledger` 任务 2.1–2.3）。

核心是**两侧拼接一致**的对拍：fd-quant 的 `fd_quant.caller.sign_caller` 是拼接
的权威定义（`hmac_sha256(key, f"{id}.{ts}")` 的十六进制），这里用**已知向量**
（同一 key/id/ts 恒得同一 hex）钉住，fd-quant 侧的验签测试用同一条向量——两侧
各自的测试共享向量，拼接任何一侧变了都会红。

密钥纪律（任务 2.3）：失败路径不携带密钥片段；`grep -r FD_QUANT_CALLER_HMAC_KEY src/`
只应命中读取处（由 test 下面的源码断言守护）。
"""
from __future__ import annotations

import hashlib
import hmac

from fd_paper_trading.strategy import ledger_identity
from fd_paper_trading.strategy.ledger_identity import caller_header

#: 已知向量（key/id/ts → hex）。fd-quant 侧 tests 对同一向量断言 verify 通过。
KEY = "shared-test-key"
CALLER_ID = "aloadtree@gmail.com"
TS = 1764000000
EXPECTED_HEX = hmac.new(
    KEY.encode("utf-8"), f"{CALLER_ID}.{TS}".encode("utf-8"), hashlib.sha256
).hexdigest()


class _User:
    """runner 里传的是 ORM User；对拍只需要 email。"""

    def __init__(self, email: str) -> None:
        self.email = email


def test_same_key_id_ts_yields_the_fd_quant_vector(monkeypatch):
    """同一 key/id/ts → 与 fd-quant sign_caller 的拼接/算法一致的 hex（任务 2.1）。"""
    monkeypatch.setenv(ledger_identity.CALLER_HMAC_ENV, KEY)
    header = caller_header(_User(CALLER_ID), now=TS)
    assert header == f"{CALLER_ID}.{TS}.{EXPECTED_HEX}"
    # 头的形状：三段、时间戳居中、第三段是十六进制签名
    parts = header.rsplit(".", 2)
    assert len(parts) == 3 and parts[0] == CALLER_ID and int(parts[1]) == TS
    assert len(parts[2]) == 64 and all(c in "0123456789abcdef" for c in parts[2])


def test_missing_key_returns_none_not_a_headless_value(monkeypatch):
    """密钥未配 ⇒ 不生成头（任务 2.2）：调用方据此计失败，绝不发无头请求。"""
    monkeypatch.delenv(ledger_identity.CALLER_HMAC_ENV, raising=False)
    monkeypatch.setenv(ledger_identity.CALLER_HMAC_ENV, "   ")  # 空白视同未配
    assert caller_header(_User(CALLER_ID), now=TS) is None


def test_failure_leaks_no_key_material(monkeypatch):
    """密钥不出现在任何失败输出里（任务 2.3）：失败路径只返回 None。"""
    monkeypatch.delenv(ledger_identity.CALLER_HMAC_ENV, raising=False)
    result = caller_header(_User("u@x.io"), now=TS)
    assert result is None
    assert "super-secret-key-bytes" not in repr(result)


def test_key_env_name_matches_fd_quant():
    """两侧读同一个 env 变量——名字漂移等于密钥悄悄分叉。"""
    assert ledger_identity.CALLER_HMAC_ENV == "FD_QUANT_CALLER_HMAC_KEY"


def test_source_reads_the_key_only_in_one_place():
    """任务 2.3 的机械判据：`FD_QUANT_CALLER_HMAC_KEY` 在 src/ 只出现在读取处
    （ledger_identity 的 env 名声明与取值），不出现在 runner 或别的模块。"""
    import subprocess
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    hits = subprocess.run(
        ["grep", "-rn", "FD_QUANT_CALLER_HMAC_KEY", str(src)],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert hits, "env 名应当出现在读取处"
    for line in hits:
        assert "ledger_identity" in line, f"密钥 env 名泄漏到别的模块: {line}"
