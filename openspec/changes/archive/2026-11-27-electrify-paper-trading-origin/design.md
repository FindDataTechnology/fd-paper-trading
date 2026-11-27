# Design

## Context

See `proposal.md` - Why. 本文件只记把这些改动落下去时需要的现场事实，以及几个必须现在定的取舍。

**现场事实（2026-11-25 实测，逐条可复现）**

| 事实 | 判据 |
|---|---|
| 本线没有公网域名 | `dig +short paper.finddatatech.cloud` 空；`paper-trading.finddatatech.cloud` 空；对照 `wire.finddatatech.cloud` → `103.236.89.212` |
| mesh 主机名公网解析但端口不通 | `cheap6.mesh.finddata.cn` 公网解析到 `8.218.137.223`；`/dev/tcp/8.218.137.223/31894` → Connection refused；走 tailnet 时 `/health` 200 |
| Caddy 只放行清单一条路径 | cheap1 `/etc/caddy/Caddyfile:269-274`：仅 `handle /fd-paper-trading/.well-known/line-manifest.json`，其余落 `reverse_proxy 100.64.0.13:18080`（注册处网关），网关对非 `/mcp` 一律 401 |
| 公网边缘是 SafeLine，不是 Caddy | cheap1 `:443` 由 nginx/tengine（safeline-mgt 容器）监听；Caddy 只监听 `*:8080`。SafeLine **site 31** 的 `upstreams = ["http://172.22.222.1:8080"]`，`server_names` 列了 22 个域名（wire / lex / connector / wanxing …），**不含 paper** |
| 证书是单张多 SAN，无通配 | `openssl s_client -servername wire.…` 的 SAN 列表逐一列出 22 个域，无 `*.finddatatech.cloud`；维护脚本 `fd-infra-deploy/tools/host/edge/renew-certs.sh` 的 `DOMAINS=(...)` 就是这张 SAN 表，`-d` 加一行即扩一个域名，签发在 cheap-3、经 sshfs webroot 做 ACME、再 `push-cert.py` 上传给 SafeLine 站点 |
| 服务 SPA 无令牌页 | `web/src` 无 `/tokens` 引用；`components/Layout.tsx` 三个 NavLink（`/`、`/orders`、`/strategies`） |
| REST token 端点本身是好的 | pod 内 `POST /api/v1/tokens` → 201，明文 `fdt_` 前缀；该 token 直打 `/api/v1/accounts`、`/api/v1/strategies`、MCP `initialize` 全 200 |
| 注册处网关不转发调用方凭据 | 同一真 token 打 `https://mcp.finddatatech.cloud/fd-paper-trading/mcp` → `{"error":"Authentication required"}`；打 mesh → `initialize` 成功 |
| TLS 在公网边缘终止 | 全部站点块是 `http://<host>:8080`，`curl -v https://wire.…` 走完 TLS 握手 → 边缘有 443 终结者（Caddy 之前的 LB） |

## Goals / Non-Goals

**Goals**
- 用户从壹座卡片点「打开服务」能到真页面，并在该页面铸出可用的 `fdt_` 令牌。
- 新入口与既有 mesh 通路并存：集群内 workload 仍走 mesh（不引入跨集群 DNS 依赖），
  运维仍可直连，用户走公网。
- 一处权威：入口地址只出现在 `line-manifest.json.public_base_url` 与 Caddy 站点块，
  不再散落硬编码。

**Non-Goals**
- 不做 SSO/Logto 登录（`line-manifest.identity` 里声明的 OIDC 端点在本服务侧没有实现，
  登录仍是本服务的邮箱+密码；OIDC 迁移是 `converge-line-identity-and-keys#8.3`）。
- 不在网关侧新增凭据转发（见 Decisions D2）。
- 不解决行情断供（`restore-astock-daily-and-trading-readiness`）。

## Decisions

### D1 公网入口用「线自有域名 + 边缘加域 + Caddy 站点块」，不用注册处网关路径

三段链路，缺一不可：

```
浏览器 ──443/TLS──> cheap1 SafeLine site 31 ──http──> 172.22.222.1:8080 (Caddy)
                                                        └─ handle paper.finddatatech.cloud ──> 100.64.0.13:31894
```

1. **SafeLine**（cheap-1，`https://127.0.0.1:9443` API）：把 `paper.finddatatech.cloud` 加进
   site 31 的 `server_names`（该站点的 upstream 已是 `http://172.22.222.1:8080`，即 Caddy 的
   共享监听，**不需要新站点**）。加域名 = `PUT /api/open/site/31`；证书另行扩 SAN（见下）。
2. **证书**：`fd-infra-deploy/tools/host/edge/renew-certs.sh` 的 `DOMAINS=(...)` 加一行
   `paper.finddatatech.cloud`，由 cheap-3 跑一次签发（多 SAN 单证书，`-d` 扩域），`push-cert.py`
   上传。**不加这一步，新域名会命中已有的 22-SAN 证书 → TLS 名称不匹配**。
3. **Caddy**（cheap1 `/etc/caddy/Caddyfile`）：新增 `http://paper.finddatatech.cloud:8080`
   站点块，三条 `handle` 按序：`/health`、`/.well-known/line-manifest.json`
   （`rewrite * /.well-known/line-manifest.json`）、兜底 `reverse_proxy 100.64.0.13:31894`。
   与 `wire` 站点块同形；**不配** `health_uri` 主动探测（见 Risks）。

- 备选一：把服务挂到 `mcp.finddatatech.cloud/fd-paper-trading/*` 上（现状）。否决：该域名的
  兜底是注册处网关，它对非 `/mcp` 路径 401 —— 用户永远到不了 SPA；而 `/mcp` 路径又因
  D2 无法带用户凭据。
- 备选二：直接让 Caddy 反代到 mesh IP `100.64.0.9:31894`。否决：`31894` 是 **NodePort**，
  应反代到集群任一节点 IP + NodePort（现用 `100.64.0.13` = cheap1 的 mesh 地址，与既有
  站点块一致）；写死某个 pod IP 会在重调度时断。
- 备选三：给 paper 建一个**独立 SafeLine 站点**（site 36+）。否决：SafeLine 社区版限 10 个
  站点且现在已有 12 个（超限靠别的机制在跑），单域名站点没有任何额外能力；site 31 的共享
  upstream 正是「一个 Caddy 按 Host 分发」的形状，加域名是它设计上的用法。

### D2 逐用户消费继续走线自有入口，不给网关加凭据转发

`fd-mcp-registry` 的 `INGRESS_RELAY_SERVERS` 只放行 `airegistry-tools`，且有一段显式注释
「Every other server gets no client auth header on egress; upstream creds come from the vault」。
把 paper-trading 加进白名单，等于让网关把客户端 `Authorization` 透传给一个第三方服务——这会
破坏网关「上游凭据来自 vault」的信任模型，也让本线的 `fdt_` 令牌绕过网关自己的 scope/计量面。

故：壹座侧 compose 用的 URL 指向 `paper.finddatatech.cloud/fd-paper-trading/mcp`（change B 改
`market-catalog.json` 的 `configTemplate`），网关条目继续只作 discoverability。

- 备选：给网关加一个 `relay_caller_credential` 元数据开关。这在本仓不可控（网关是独立部署），
  且与「上游 creds 来自 vault」的既有约束冲突；即使要做也应另立 change 并同时覆盖所有第三方
  直通的服务，而不是只为这条线开一个口。

### D3 令牌页放 `/settings/tokens`，不做「一键铸造并写进壹座」

本服务不知道壹座的存在，也不知道用户的壹座身份；它只能给出一串 `fdt_` 明文 + 一句「粘贴到
壹座 → 设置 → 连接 → 柏讯·模拟交易」。反向（壹座去服务侧代铸）需要服务给壹座一个服务账号
令牌——那等于把用户令牌的签发权交给平台，与本线「token 即身份、按调用者隔离」的模型冲突。
故保持「用户自己铸、自己贴」。

- 备选：把令牌嵌进 SPA 首页的注册流程（注册即自动铸一枚并显示）。否决：注册时还没有登录
  态，且用户可能在其它入口（MCP 客户端）注册；单独一页读起来也更清楚。

### D4 `public_base_url` 改值，`/.well-known` 保持匿名

`line-manifest.json.public_base_url` 从 `https://mcp.finddatatech.cloud/fd-paper-trading`
改为 `https://paper.finddatatech.cloud/fd-paper-trading`。清单判据（`line-manifest` 能力）
要求该 URL 下的 `/.well-known/line-manifest.json` 匿名 200 且 `content-type` 是 JSON——新
站点块必须显式给这条 `handle` 并排在 SPA 兜底之前（`wire` 站点块里已有该教训的注释：
「兜底会把任何未知路径回 index.html，投影判据会拿到 HTML」）。

站点块里 `/health` 也显式放行，与 mesh 现状一致（健康探针不携带凭据）。

### D5 规格收口的真源选择

| 漂移 | 真源 | 动作 |
|---|---|---|
| 撮合器 offload | 代码（同步纯算术，~0.5µs）与 `design D10` 的既定取舍 | 删 spec 里的 offloaded 从句，保留「无第三方撮合引擎 / 状态不在计算器里」 |
| Polygon / fd-open-data 可选 | `selection.py` 只认注册表 `access.kind`，没有任何源声明这两种 kind | spec 改为「代码内保留，配置不可选中」，Purpose 的「三实现」改为「三种 provider 类，仅一种可由配置选中」 |
| 仓内 `astock-market-data` 钉死单源 | 根账（价格族模型）+ 已上产代码（`_concepts_by_type` / `_family`） | 用根账的 requirement 文本替换仓内旧账；仓内这份是被 `2026-11-15-fund-price-channel` 超越的陈旧副本 |

## Risks / Trade-offs

- **扩大公网暴露面** → 新站点块暴露的每一路径都已由既有 `_TokenGuard`/JWT 依赖保护；
  匿名面只有 `/health` 与 `/.well-known`（与 mesh 现状逐字相同）。发布前在 pod 内跑一次
  `curl` 矩阵：对未认证的 `/api/v1/accounts`、`/api/v1/orders`、`/mcp` 应为 401。
- **DNS 与证书都在本仓外（dnspod 控制台 / cheap-3 的 acme.sh + SafeLine API）** → 任务写成可照做
  的步骤并给出验证命令；**证书与域名必须同批**，只加 DNS 不加 SAN 会得到一个 TLS 名称不匹配的
  不可用入口。若域名暂不可加，§5 的验收以 `curl --resolve paper.finddatatech.cloud:443:103.236.89.212`
  的形式完成（需先有 SAN），DNS 单独留痕。
- **Caddyfile 不在任何仓** → 站点块全文写进 `fd-infra-deploy/docs/`，含 `caddy validate` 与
  `systemctl reload caddy`（**不是 restart**，避免瞬断其它站点）；回滚 = 删块 + reload。
- **SafeLine 的 `PUT /api/open/site/31` 是全量覆盖** → 先 `GET` 出当前 body、只 append
  `server_names`、其余字段原样回写；改完立即 `GET` 复核 `server_names` 长度 = 旧 + 1 且尾部
  域名仍在。误覆盖会让 22 个已有域名一起失效（这是本 change 最危险的一步）。
- **Caddy health check 副作用**（`paas` 站点的既有教训）→ 本站点块**不配** `health_uri`
  主动探测，避免「健康端点变 401 就整体判死」。若日后要配，health_uri 只能是 `/health`。
- **`public_base_url` 改值后旧链接失效** → 旧值指向的是网关条目，本就是 discoverability
  而非消费面；壹座 / 客户端的真实消费地址由 change B 同步改，两者同批发布。

## Migration Plan

1. 证书：`renew-certs.sh` 的 `DOMAINS` 加 `paper.finddatatech.cloud` → cheap-3 上跑一次 →
   日志出现 `push OK` 且 `HEALTH … san=<23> days_left=…`。
2. SafeLine：`GET /api/open/site/31` → append `server_names` → `PUT` → `GET` 复核 23 个域名都在。
   此步完成后 `curl --resolve paper.…:443:103.236.89.212 https://paper.…` 的 TLS 握手已能通过
   （Caddy 还没配，会拿到 Caddy 的 404/默认回退——这是预期，不是故障）。
3. Caddy：加站点块 → `caddy validate` → `systemctl reload caddy` → 用 `--resolve` 验三条路径。
4. DNS：dnspod 加 `paper` A 记录 → `103.236.89.212` → `dig +short` 复核 → 去掉 `--resolve` 再验一次。
5. 服务侧：令牌页与清单改值随下一次正常出片（公开仓 CI → hkccr → ccr → 滚 tag），无数据迁移。
6. 回滚（逆序即可）：Caddy 删块 reload → SafeLine 去掉域名 → DNS 记录可保留（无 Caddy 块即 404，
   无害）→ 镜像回滚到上一 tag。证书 SAN 不需要回退。

**顺序不能颠倒**：SAN → SafeLine → Caddy → DNS。任何一步提前，用户看到的是 TLS 错或 404，
而这两者在浏览器里都读作「这个服务坏了」，与真实状态（还差一步）不符。

## Open Questions

- 无阻断性未知。`git` 里可查的待定项只有一条：`/health` 是否需要在公网入口上暴露
  版本号之外的任何东西——现状 `{"status":"ok","last_surface_report":"ok"}` 不含敏感信息，
  按原样保留。
