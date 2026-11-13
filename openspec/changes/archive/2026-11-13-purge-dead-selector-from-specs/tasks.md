## 1. 规格（三个 capability，每个一份 delta、每个文件一个 MODIFIED）

> 2026-11-13 复核补记：这份 change 归档时六项全未勾，导致"忘了勾"与"真没做"无法区分。
> 下面逐条对**当下**的 spec 正文重新核过（不是凭记忆勾）：
>
> - 1.1 `market-data:124` 现为「Whichever provider the startup selection resolves to
>   (see "Provider is swappable"), that same provider instance SHALL feed …」——条件已去死。
> - 1.2 `service-deployment:120/129` 现为「`MARKET_DATA_SOURCE_ID` 选择它时给出明确启动错误」/
>   「`MARKET_DATA_SOURCE_ID` 没有选择需要该凭据的源」——判据已换到唯一选择器。
> - 1.3 `service-identity:71-73` 现为「the market-data selection variable is
>   `MARKET_DATA_SOURCE_ID` (the legacy `MARKET_DATA_PROVIDER` was removed and fails startup
>   when still set)」——不再是"保持不变"名单里的旧名。
> - 1.6 三项各跑 `--strict`：exit 0 / 0 / 0（`market-data` 只有一条长度 INFO）；全仓
>   `grep MARKET_DATA_PROVIDER openspec/specs/` 只剩三处"已移除/失败启动"的陈述。
> - 1.4/1.5 是那次归档本身的动作（validate → archive --yes + 对账），归档记录即证据；
>   本次无法重跑（change 已在 archive 下）。

- [x] 1.1 `market-data`：One price world 的条件去死
- [x] 1.2 `service-deployment`：凭据场景条件改用 `MARKET_DATA_SOURCE_ID`
- [x] 1.3 `service-identity`：把 `MARKET_DATA_PROVIDER` 从"保持不变"名单移除
- [x] 1.4 `openspec validate purge-dead-selector-from-specs --strict`
- [x] 1.5 `openspec archive ... --yes`，对账 `~ 3 modified`
- [x] 1.6 三个 capability 各跑一次 `--strict`；全仓 `grep MARKET_DATA_PROVIDER openspec/specs/`
      只应剩"已移除"的陈述
