# Proposal

## Why

壹座侧「模拟交易」这条路今天是断的，而且断在用户接触的第一格：卡片叫用户「在服务 Web 铸造
API token（`fdt_`）并粘贴」，可这句话指的两样东西都不存在——服务没有公网域名（
`paper.finddatatech.cloud` 无 DNS 记录，`cheap6.mesh.finddata.cn:31894` 公网端口实测
CLOSED），SPA 里也**没有任何令牌页面**（`web/src` 全仓 `/tokens` 零命中，导航只有
Dashboard / Orders / Strategies）。生产数据是这句话的实证：`user_paper_trading_credentials`
0 行、`installed_packs` 里没有任何交易包——这条线从未被任何用户成功接入过。

同一轮实测还留下三处规格与代码互相矛盾的记账（详见 `design.md` 的证据表）：主撮合器
规格说「SHALL run off the event loop (offloaded)」而代码是同步算术调用；`market-data`
把 Polygon / fd-open-data 两个 provider 写成「三实现之一」，而注册表选择器根本选不到
它们；`astock-market-data` 在仓内仍写「钉死单一源」，而根账与已上产代码都已是
「按实体类型走价格族」。

## What Changes

- **线有一个公网入口**：`https://paper.finddatatech.cloud` 反代到 fd-prod/NodePort 31894，
  与 `wire` / `lex` 同形（Caddy 站点块 + DNS A 记录 + argo/模板不参与，因为 Caddyfile
  不在任何仓）。入口只承载本服务自己的 REST/SPA/MCP，不经过注册处网关（网关会剥掉
  调用方 `Authorization`，见 `tool-surface.json` 的 `consumption_note`）。
- **SPA 有令牌页**：新增 `/settings/tokens`（或等价入口）——列出、铸造一次、吊销
  `fdt_` 令牌，调的是既有的 `POST/GET/DELETE /api/v1/tokens`。铸造后的明文只显示一次，
  页面明确写出「这串值填进壹座的模拟交易卡片」。
- **线清单的 `public_base_url` 指向该入口**，`/.well-known/line-manifest.json` 仍匿名可读。
- **三处规格漂移收口**：删掉撮合器的 offload 条款（保留「纯算术、无第三方撮合引擎」）；
  Polygon / fd-open-data 从「可选实现」降为「代码内保留但不可由配置选中」的事实陈述；
  仓内 `astock-market-data` 改为与根账/代码一致的「价格族」模型。
- **不做**：不改网关的凭据转发模型（逐用户消费走线自己的入口是既定边界，让网关学会转发
  第三方 bearer 会破坏它的信任模型）；不放宽 `stale_quote` 窗口（让用户拿 12 天前的价格
  成交比拒绝更糟）；不动行情采集链（那是 `restore-astock-daily-and-trading-readiness`）。

## Capabilities

### New Capabilities

- `service-origin`: 这条线对最终用户的公网入口——哪些路径对外、哪些必须匿名、它与注册处
  网关的分工，以及入口缺省时服务如何降级（mesh 仍可运维，用户的粘贴路径给出可读失败）。

### Modified Capabilities

- `service-deployment`: `Contact with the cluster is over the private mesh` 由「不要求公网域名」
  改为「mesh 是集群内通路，公网入口是用户通路，两者并存且互不替代」。
- `user-auth`: `Revocable API tokens` 增加「SPA 必须提供铸造面」的条款——只给 REST 端点、
  不给页面，等于要求用户手敲 curl，而壹座的卡片文案承诺的是页面。
- `web-shell`: 导航与路由增加令牌页（`Single-page application shell` 的可达性判据）。
- `order-execution`: 删除 `Stateless fill calculator` 里的 offloaded 条款。
- `market-data`: 把 Polygon 那条从「可选实现」改为「保留但配置不可选」，并修正 Purpose
  里的「三实现」。
- `astock-market-data`: 用价格族模型替换「钉死单一源」（对齐根账与代码）。

## Impact

- 代码：`fd-paper-trading/web/src/pages/` 新增令牌页 + 路由/导航；`docs/line-auth-evidence.md`
  更新入口事实；`line-manifest.json` 的 `public_base_url` 改值。
- 运维：cheap1 `/etc/caddy/Caddyfile` 新增站点块（不在仓，步骤落 `fd-infra-deploy/docs/`）；
  DNS（dnspod，`finddatatech.cloud` 区）新增 `paper` A 记录指向公网边缘 `103.236.89.212`。
- 依赖：无新增；`web` 侧复用既有 API 客户端与 hooks。
- 风险：新公网入口扩大了暴露面——它暴露的是已有认证（JWT/`fdt_`）保护的 REST/SPA/MCP，
  没有新增未认证端点；`/health` 与 `/.well-known` 的匿名面与 mesh 入口现状一致。
