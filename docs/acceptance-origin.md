# 验收记录：公网入口 `paper.finddatatech.cloud`（`electrify-paper-trading-origin` 5.x / 6.x）

> 真跑命令、真实公网、真实证书校验（无 `-k`），日期 2026-11-27。本文件是 `reports/origin-2026-11-25.md`
> 的施工进度的**验收侧**留存：施工记录讲「怎么搭起来」，这里讲「搭起来后真能用，且不能乱用」。

## 发版事实

| 项 | 值 |
|---|---|
| 仓内提交 | `cf11bb3`（令牌页可达 + 规格收口） |
| 镜像 tag | `sha-cf11bb3`（内容变了，铸了新 tag；hkccr→ccr relay 09:57 复制完成） |
| 部署清单 | `fd-infra-deploy/all-services/prod/fd-paper-trading.yaml` → `sha-cf11bb3`（`fd-mcp-ship` 10:01 滚版） |
| 生产 pod | `fd-paper-trading-5c65cbbb74-7277w` 1/1 Running（节点 `liuliangjkiimypbdzxa`） |
| 内容验尸 | cheap3 拉 ccr `sha-cf11bb3` 后断言 `/app/web/dist`：`settings/tokens=2`、`API 令牌=1`、`铸造新令牌=1` |

> 注：滚版过程中一个副本被 `cheap-4` 的 **ephemeral-storage 驱逐**（exit 137，节点磁盘满，非镜像缺陷），
> 另起副本接管，最终 `replicas=1 ready=1 available=1`。

## 入口匿名面矩阵（公网，TLS 校验开）

```
GET  /health                                 200 application/json
GET  /.well-known/line-manifest.json         200 application/json   public_base_url=https://paper.finddatatech.cloud/fd-paper-trading
GET  /                                       200 text/html（SPA）
GET  /api/v1/accounts        （无凭据）      401
GET  /api/v1/tokens          （无凭据）      401
```

## 真实用户路径（5.1）——全新用户，全程公网 https

```
register  201  acc-1795745787@example.com
login     200  JWT(243B)
POST /api/v1/tokens  201  fdt_-YuW…（明文仅此一次）
GET  /api/v1/tokens  200  列表项 token=null（明文不回落）
POST /api/v1/accounts 201（用 fdt_ 令牌建账户）
MCP  POST /fd-paper-trading/mcp/ initialize  200  serverInfo.name=fd-paper-trading v4.2.4
MCP  tools/call list_accounts（fdt_）        isError=false，返回该用户自己的账户
                                               cash=100000.0000，venue=PAPER-EB7628C7E59E
```

这是「用户经公网铸令牌 → 用该令牌驱动 MCP 工具拿到本人数据」的完整闭环，跑在**本次发版的
新镜像**上（SPA bundle 已从 `index-BjXDZ5Yj.js` 变为 `index-BWo3MuDW.js`，含 `Tokens` 导航项）。

## 反向验证（5.2）

```
GET  /api/v1/accounts（无凭据）                 401
DELETE /api/v1/tokens/{id}（吊销）              204
GET  /api/v1/accounts（用已吊销的同一 fdt_）     401
```

## 收尾

测试用户 `acc-1795745719@…`、`acc-1795745787@…` 及其 `api_tokens` / `accounts` 已从生产库删除
（`remaining=0`）。

## 未覆盖 / 遗留

- **`/mcp`（无尾斜杠）→ 405** 是 FastMCP 路由形状的既有行为，非本次引入；目录条目与壹座基线
  用的是带尾斜杠的 `/mcp/`，已对齐。
- **`tools/list` 未认证可见** 是 MCP 协议层行为（initialize 匿名），数据面在 `tools/call` 才鉴权；
  19 个工具名本就在公开的 `tool-surface.json` 里，不构成额外泄露面。
