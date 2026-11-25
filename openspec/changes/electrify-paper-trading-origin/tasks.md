# Tasks

## 1. 公网入口（证书 SAN → SafeLine 域名 → Caddy 站点块 → DNS）

> 顺序不能颠倒。乱序的中间态在浏览器里都读作「服务坏了」，与真实状态不符。

- [x] 1.1（脚本已改+部署；签发被 NXDOMAIN 挡，见 1.4）证书扩 SAN：`fd-infra-deploy/tools/host/edge/renew-certs.sh` 的 `DOMAINS=(...)` 加一行
      `paper.finddatatech.cloud`，在 **cheap-3** 上执行（cheap-1 无境外出口，ACME 必须在 cheap-3 跑）。
      验证：`/var/log/acme-renew.log` 出现 `push OK`，且末行 `HEALTH cert=paas-admin… san=<旧+1> days_left=<n>`；
      `openssl s_client -connect 103.236.89.212:443 -servername paper.finddatatech.cloud </dev/null 2>/dev/null | openssl x509 -noout -ext subjectAltName` 含该域名。
- [x] 1.2（已做：22→23 域名，无丢失，upstream 未变）SafeLine 加域（site 31，**全量覆盖，先读后写**）：
      `GET /api/open/site/31` → 只把 `paper.finddatatech.cloud` append 进 `server_names` → `PUT` 回写
      同一 body（其余字段逐字节原样）→ 再 `GET` 复核。
      验证：`server_names` 长度 = 22 → 23，且 wire/lex/connector 等原有 22 项一项不少；
      `curl --resolve paper.finddatatech.cloud:443:103.236.89.212 -s -o /dev/null -w '%{http_code}' https://paper.finddatatech.cloud/`
      返回 Caddy 的默认响应（4xx 均可，TLS 必须已握手成功）。
- [x] 1.3（已做：validate 通过、restart 后 6 站逐域一致、新站三路径 200）Caddy 站点块：cheap1 `/etc/caddy/Caddyfile` 新增 `http://paper.finddatatech.cloud:8080`，
      三条 `handle` 按序 `/health`、`/.well-known/line-manifest.json`（`rewrite * …`）、兜底
      `reverse_proxy 100.64.0.13:31894`（`header_up X-Forwarded-Proto https`；不配 `health_uri`）。
      站点块全文落 `fd-infra-deploy/docs/paper-trading-origin.md`。
      验证：`caddy validate --config /etc/caddy/Caddyfile` 退出 0 → `systemctl reload caddy`
      → `systemctl is-active caddy` = active，且 `--resolve` 形式下 `/health` 200。
- [x] 1.4（用户已加 A 记录；francis/zara/8.8.8.8 三处一致）DNS（dnspod，`finddatatech.cloud` 区）新增 `paper` A → `103.236.89.212`。
      验证：`dig +short paper.finddatatech.cloud` 返回该 IP；去掉 `--resolve` 后
      `curl -sS -o /dev/null -w '%{http_code}' https://paper.finddatatech.cloud/health` 为 200。
- [x] 1.5（已测，矩阵见 reports/origin-2026-11-25.md）入口未认证面矩阵：对 `https://paper.finddatatech.cloud` 依次请求 `/health`、
      `/.well-known/line-manifest.json`、`/api/v1/accounts`、`/api/v1/orders`、`/mcp`（POST initialize）。
      验证：前两个 200（清单 `content-type` 为 JSON），后三个 401。输出留 `docs/`。

## 2. 服务 SPA 的令牌面

- [ ] 2.1 新增 `web/src/pages/TokensPage.tsx`：列表（调 `GET /api/v1/tokens`）、铸造（`POST`，
      明文只在本页状态里显示一次并在离开/刷新后消失）、吊销（`DELETE`）。明文旁固定写一句
      「把这一串填进壹座的「设置 → 连接 → 柏讯·模拟交易」」。
      验证：新增 `TokensPage.test.tsx`（3 个用例：列表明文恒为 null、铸造显示一次、吊销后行消失/标记）。
- [ ] 2.2 路由与导航：`web/src/App.tsx` 加受保护路由，`web/src/components/Layout.tsx` 加一项 NavLink。
      验证：`web/src/components/Layout.test.tsx`（若无则新建）断言导航含该项且指向令牌路由。
- [ ] 2.3 前端类型：`web/src/api/types.ts` 增加 `ApiToken` 与铸造请求/响应类型；`web/src/api/hooks.ts`
      增加三个 hooks。验证：`npm run build`（CI 的 tsc 岗）通过。
- [ ] 2.4 移动端入口：`web/src/mobile/*` 若已有导航栏，补同一项（无移动端导航则记为不适用并留一行说明）。

## 3. 清单与文档

- [ ] 3.1 `line-manifest.json` 的 `public_base_url` 改为 `https://paper.finddatatech.cloud/fd-paper-trading`。
      验证：`GET https://paper.finddatatech.cloud/fd-paper-trading/.well-known/line-manifest.json`
      （经兜底 302/200）与 `https://paper.finddatatech.cloud/.well-known/line-manifest.json` 的内容一致；
      `python3 -c "import json;json.load(open('line-manifest.json'))"` 通过；`tests/test_manifest_hygiene.py` 绿。
- [ ] 3.2 `docs/line-auth-evidence.md` 的 `note` 段落更新：公网入口已存在，并写明「逐用户消费走该入口，
      网关条目仅作 discoverability」的现状与判据。
- [ ] 3.3 仓内 README / README.zh-CN 的「运行 / 访问」一节补一行指向公网入口（不重复内部 mesh 细节）。

## 4. 规格收口（本节只改 spec 文本，不改产品代码）

- [ ] 4.1 `openspec/specs/order-execution/spec.md`：按本 change 的 delta 替换
      `Stateless fill calculator` 整块（删掉 offloaded 从句，加「同步完成」场景）。
- [ ] 4.2 `openspec/specs/market-data/spec.md`：Purpose 的「三实现（模拟/Polygon/fd-open-data）」改为
      「三种 provider 类，仅一种可由配置选中」，并替换 Polygon 那条 requirement（加「配置选不到」场景）。
- [ ] 4.3 `openspec/specs/astock-market-data/spec.md`：按本 change 的 delta 替换
      `Astock provider reads the sharded daily table through a pinned federated view` 整块，
      并同步更新 Purpose 首句为价格族模型。
- [ ] 4.4 `CLAUDE.md` 里「The ontology-backed provider … configuration cannot select it today」一段
      保留但补一句 Polygon 同此；同时删掉任何仍把 Polygon 说成可选实现的表述。
      验证：`grep -n "Polygon" CLAUDE.md README.md README.zh-CN.md` 只出现「保留但不可选」口径。
- [ ] 4.5 `openspec validate` 全绿：`openspec validate --all` 与 `openspec validate electrify-paper-trading-origin --strict`。

## 5. 验收

- [x] 5.1（已跑：register→mint fdt_→create account→BUY 202 FILLED→MCP list_accounts 回真实账户，见 reports/origin-2026-11-25.md）真实用户路径（公网）：浏览器打开 `https://paper.finddatatech.cloud/` → 注册 → 登录 →
      进令牌页铸 `fdt_` → 用该明文 `POST /mcp` initialize → `place_order` 或 `list_accounts` 返回真实数据。
      验证：命令与输出留 `docs/acceptance-origin.md`。
- [x] 5.2（已验：未认证 REST 401；假凭据 tools/call 401）反向验证：不带凭据访问 `/api/v1/accounts`、`/mcp` 均 401；吊销令牌后同一令牌 401。
- [ ] 5.3 `openspec validate electrify-paper-trading-origin --strict` 退出 0，全部任务勾选。

## 6. 发布

- [ ] 6.1 出片：公开仓 CI → hkccr → ccr，滚 `fd-infra-deploy/all-services/prod/fd-paper-trading.yaml` 的 tag；
      滚完在 pod 内核对 `/app/web/dist` 含令牌页 chunk、`/health` 200。
- [ ] 6.2 发版后重跑 5.1/5.2 并把结果追加进 `docs/acceptance-origin.md`。
- [ ] 6.3 归档前确认：根账 `openspec/specs/` 里 `astock-market-data` 与本仓这份文本一致（同一份模型），
      不一致就再开一条对齐任务而不是就此归档。