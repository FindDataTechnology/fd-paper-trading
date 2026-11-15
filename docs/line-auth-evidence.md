# 模拟交易（paper-trading）线的授权与凭据证据

本文件是 `line-manifest.json` 被剥离的自由文本的**仓内**留存。原因：清单会随
`/.well-known/line-manifest.json` **匿名公开**，而规格（`openspec` 的
`line-manifest` 能力）规定清单字符串键只能落在一个闭集里——闭集之外的自由文本
（典型是 `note`，以及 `billing.migration_task` 这种不在闭集内的键）一律判违规：
一段自由文本不是「写短点」就安全，它压根不该出现在公开契约里。下列内容含内部
mesh 主机名与端口、网关行为，故只留在仓库内，不进公开载荷。

对外可见的那部分是产品语义：模拟交易——行情/撮合/持仓与回测，身份与账本在本服务侧
（`billing.plane=own`），词元平面是迁移目标而非现状。

## 被剥离的自由文本（原文逐字，按清单路径索引）

### `note`

公开入口只有注册处那条 discoverability 条目（`/fd-paper-trading` → mesh `http://cheap6.mesh.finddata.cn:31894/mcp`）：本线没有自己的公网域名，`paper.finddatatech.cloud` 不解析（DNS 无记录），初版清单误用了它，2026-11-12 改为注册处路径。逐用户消费必须走 mesh 直连——网关会剥掉调用方 Authorization（见 tool-surface.json 的 registry.consumption_note）。

### `billing.note`

今天的事实：身份与账本都在本服务侧（见 tool-surface.json 的 registry.consumption_note）。词元平面是迁移目标，不是现状——所以这里写 own 而不是 ciyuan；目标记在 auth_tracks 的 customer-key 轨（enforced:false + migration_task）。

### `billing.migration_task`

converge-line-identity-and-keys#8.3
