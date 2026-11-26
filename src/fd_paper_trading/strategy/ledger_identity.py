"""落账调用者身份（design D2，spec `paper-run-decision-ledger`）。

账本按 caller 隔离存储；身份**不能**由请求参数声明（谁都能声称自己是别人），
只能由验签头解析。fd-quant 侧 `fd_quant.caller` 验的是：

    X-Fd-Caller: <caller_id>.<unix_ts>.<hex hmac_sha256(key, "<caller_id>.<ts>")>

密钥在 `FD_QUANT_CALLER_HMAC_KEY`，与 fd-quant 共享同一份 secret 的两个挂载点。
本模块按**同一拼接**现签这个头——拼接顺序与密钥是两侧的对外契约，任何一侧都
不改它（fd-quant 的 `sign_caller` 是拼接的权威定义，这里逐字复刻其行为）。

设计取舍（D2）：

- **签头而非转发 bearer**：账本不消费 JWT，验签头是它唯一认的用户身份轨。
- **caller_id 取用户邮箱**：fd-quant 的 id 白名单允许 `@` 与 `.`，邮箱是服务内
  每个用户都有的稳定标识。用户改邮箱 ⇒ 另一份账本——这是账本"按 caller 隔离"
  的既定语义，文档写明（design Risks）。
- **每次落账现签**：验签窗 ±300s（fd-quant `CALLER_CLOCK_SKEW_S`），头随请求
  走、不需要独立寿命，缓存只会带来"过期头"这一类新失败模式。
- **密钥缺失 ⇒ 返回 None，不发无头请求**（任务 2.2）：无头请求会被账本解析成
  anonymous——把"没配身份"伪装成"匿名落了一笔"，正是本任务要堵的洞。调用方
  （runner）据此把落账计为失败并留痕 `no_identity_key`。
- **密钥不出现在任何返回值、日志或错误里**（任务 2.3）：本模块只把密钥读进
  局部变量参与运算；失败路径返回 None，不携带密钥片段。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

#: 与 fd-quant `fd_quant.caller.CALLER_HMAC_ENV` 同名的共享密钥 env。
CALLER_HMAC_ENV = "FD_QUANT_CALLER_HMAC_KEY"

#: 密钥未配置时的稳定原因码：runner 把它写进落账失败留痕（任务 2.2）。
REASON_NO_KEY = "no_identity_key"


def caller_header(user, *, now: int | float | None = None) -> str | None:
    """为该用户现签一个 `X-Fd-Caller` 头值；密钥未配置时返回 `None`。

    `now` 供测试注入（固定时间戳 ⇒ 同 key/id/ts 恒得同一 hex，与 fd-quant 的
    已知向量对拍）。生产路径不传——时间戳取当前时刻，落在 fd-quant 的验签窗内。
    """
    key = (os.environ.get(CALLER_HMAC_ENV) or "").strip()
    if not key:
        return None
    caller_id = user.email
    ts = int(time.time()) if now is None else int(now)
    sig = hmac.new(
        key.encode("utf-8"), f"{caller_id}.{ts}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{caller_id}.{ts}.{sig}"
