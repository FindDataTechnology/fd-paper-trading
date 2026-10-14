<div align="center">

**[English](./README.md)** | **[中文](./README.zh-CN.md)**

</div>

# fd-paper-trading

多租户**模拟交易（paper trading）**服务，通过 FastAPI REST + WebSocket API 与 MCP server 对外提供服务。用户可以拥有多个模拟交易账户，通过 API 对实时市场数据进行下单，使用**模拟撮合**成交 —— 无需券商，无需真实资金。

## 功能特性

- **多用户认证** — JWT + 可撤销的 API Token（`fdt_` 前缀）
- **多账户管理** — 每个用户可以创建和管理多个隔离的模拟账户
- **订单执行** — 市价单和限价单，通过无状态纯 Python 撮合计算器进行模拟成交
- **持仓与资金账本** — 实时盈亏、平均成本、已实现收益
- **实时推送** — WebSocket 端点（`/ws`）推送实时行情、订单状态和成交事件
- **市场数据** — `SimulatedMarketDataProvider`（确定性，用于开发/测试）和 `PolygonMarketDataProvider`（真实价格，免费套餐为收盘价）
- **Web 界面** — React + TypeScript 单页应用，提供专业交易工作台：
  - **三栏布局**：自选股列表（实时报价）、价格图表和下单面板
  - **点击交易**：从自选股列表选择任意标的，图表和下单表单即时更新
  - **持仓管理**：快速平仓/减仓操作，带确认对话框
  - **每日盈亏追踪**：基于已实现 + 未实现盈亏的实时计算
  - **AI 聊天助手**：对话式界面，用于下单、查询账户和管理交易
  - **代码补全式搜索**、交互式图表（TradingView `lightweight-charts`）和实时推送

## 技术栈

- **后端：** Python 3.12+、FastAPI + Uvicorn、SQLAlchemy 2.0 async、Alembic
- **数据库：** PostgreSQL（生产）/ SQLite（测试）
- **认证：** JWT（`python-jose`）+ bcrypt + 可撤销 API Token
- **执行引擎：** 无状态纯 Python 撮合计算器（不依赖任何交易框架）
- **前端：** React 18 + TypeScript + Vite、TanStack Query + Zustand、TradingView `lightweight-charts`
- **测试：** pytest（后端）、Vitest + React Testing Library（前端）、Playwright（端到端）

## 快速开始

```bash
# 1. 安装 Python 依赖
uv sync

# 2. 配置环境变量
cp .env.example .env   # 编辑 DATABASE_URL、JWT_SECRET、市场数据 API Key

# 3. 启动 PostgreSQL 并执行数据库迁移
brew services start postgresql@14
createdb paper_trading
uv run alembic upgrade head

# 4. （可选）构建 Web 界面，让 FastAPI 在 / 路径提供服务
cd web && npm install && npm run build && cd ..

# 5. 启动 API（OpenAPI 文档地址：http://localhost:8000/docs）
uv run fd-paper-trading
```

> **注意：** 在 macOS 上，如果 5432 端口被占用，Homebrew 的 `postgresql@14` 可能运行在 5433 端口。请相应调整 `.env` 中的 `DATABASE_URL`。

### 开发模式

```bash
# 终端 1：FastAPI 后端，监听 :8000
uv run fd-paper-trading

# 终端 2：Vite 开发服务器，监听 :5173（自动代理 /api 和 /ws 到后端）
cd web && npm install && npm run dev
```

### 生产模式

```bash
# 构建 SPA 并从 FastAPI 统一提供服务
cd web && npm run build
uv run fd-paper-trading   # SPA + API + /ws 全部在 http://localhost:8000/
```

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  Web UI (React + Vite + TypeScript)                     │
│  - TanStack Query 用于数据请求                           │
│  - Zustand 用于状态管理                                  │
│  - TradingView lightweight-charts 用于图表展示           │
│  - WebSocket 客户端用于实时推送                          │
└────────────────────┬────────────────────────────────────┘
                     │ REST (/api/v1) + WebSocket (/ws)
┌────────────────────▼────────────────────────────────────┐
│  FastAPI 后端                                           │
│  - 认证（JWT + API Token）                              │
│  - 账户管理                                              │
│  - 订单下单与取消                                        │
│  - 持仓与资金账本                                        │
│  - 市场数据 API                                          │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│PostgreSQL│  │  撮合计算  │  │  行情  │
│(账本)    │  │  (撮合)   │  │  Data    │
└──────────┘  └──────────┘  └──────────┘
```

**执行模型（v1）：** 成交由无状态纯 Python 计算器完成（一条报价 + 一张订单 → 成交结果）。账户状态存储在数据库中，按 `account_id` 隔离。

## 交易工作台

Web 界面提供专业交易工作台，为活跃交易优化：

```
┌───────────────────────────────────────────────────────────────┐
│ 自选股（左）│ 价格图表  │ 下单面板（右）│
├──────────────────┼──────────────┼─────────────────────────────┤
│ AAPL    $175.50▲│              │ 方向：[买入 ▼] 卖出          │
│ TSLA    $250.20▼│ │ 类型：[市价 ▼] 限价          │
│ NVDA    $890.10▲│   [图表]     │ 数量：[100]       限价:$175  │
│ SPY     $450.00▶│              │                             │
│ QQQ     $400.25▶│              │ [买入 AAPL] [卖出 AAPL] │
│              总盈亏：(+$127)│                             │
├──────────────────┴──────────────┴─────────────────────────────┤
│ 持仓表（每行都有快速平仓/减仓按钮）                            │
└───────────────────────────────────────────────────────────────┘
```

**核心功能：**
- **自选股列表**：追踪标的的实时报价（AAPL、TSLA、NVDA、SPY、QQQ），通过 WebSocket 更新
- **点击交易**：点击自选股列表中的任意标的，图表和下单面板即时更新
- **下单面板**：始终可见的下单表单，绑定到当前选中的标的
- **持仓操作**：快速平仓/减仓按钮，带确认对话框
- **每日盈亏**：基于已实现收益 + 未实现盯市盈亏的实时计算

**说明：** 每日盈亏为参考值（前端计算），可能与后端计算略有差异。移动端响应式布局和用户自定义自选股列表将在未来版本中实现。

## 常用命令

```bash
# 后端
uv run pytest                    # 运行所有后端测试（内存 SQLite）
uv run pytest tests/unit/        # 仅运行单元测试
uv run pytest tests/e2e/         # 仅运行端到端测试
uv run alembic upgrade head      # 执行数据库迁移
uv run alembic downgrade -1      # 回滚最后一次迁移

# 前端
cd web && npm test               # Vitest 组件测试
cd web && npx playwright test    # Playwright 端到端测试
cd web && npm run build          # 生产构建
cd web && npm run dev            # 开发服务器（热重载）
```

## API 概览

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册用户 |
| POST | `/api/v1/auth/login` | 获取 JWT 访问令牌 |
| POST | `/api/v1/tokens` | 创建可撤销的 API Token |
| GET | `/api/v1/tokens` | 列出 API Token |
| DELETE | `/api/v1/tokens/{id}` | 撤销 API Token |
| GET | `/api/v1/accounts` | 列出你的账户 |
| POST | `/api/v1/accounts` | 创建模拟账户 |
| GET | `/api/v1/accounts/{id}` | 账户摘要（余额、权益、计数） |
| POST | `/api/v1/accounts/{id}/deposit` | 存款 / 取款（有符号金额） |
| POST | `/api/v1/accounts/{id}/reset` | 重置账户（取消订单、平仓、恢复余额） |
| GET | `/api/v1/accounts/{id}/positions` | 列出持仓 |
| GET | `/api/v1/accounts/{id}/orders` | 列出订单（可选 `?status=`） |
| POST | `/api/v1/orders` | 下单（市价/限价，模拟成交，返回 202） |
| DELETE | `/api/v1/orders/{id}` | 取消挂单 |
| GET | `/api/v1/orders/{id}` | 查询单个订单 |
| GET | `/api/v1/instruments/search?q=` | 按代码/名称搜索标的 |
| GET | `/api/v1/instruments/{symbol}/bars` | 历史 K 线（`?timeframe=&limit=`） |
| WS | `/ws` | 推送行情 + 订单/成交事件（首条消息认证） |

**认证方式：** `Authorization: Bearer <jwt>` 或 `Authorization: Bearer <api-token>`（Token 以 `fdt_` 开头）。

**WebSocket：** 首条消息发送 `{"type":"auth","token":"<jwt>"}`，然后 `{"type":"subscribe","symbol":"AAPL"}` / `{"type":"unsubscribe","symbol":"AAPL"}`。

**错误格式：** 统一格式：`{"error": {"code": "...", "message": "...", "fields": [...]}}`。

## 参与贡献

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 进行修改并添加测试
4. 运行测试套件：`uv run pytest && cd web && npm test`
5. 提交并写描述性信息：`git commit -m "Add your feature"`
6. 推送到你的 Fork：`git push origin feature/your-feature`
7. 发起 Pull Request

请确保你的代码遵循现有风格，并在提交前通过所有测试。

## 许可证

MIT —— 见 [LICENSE](./LICENSE)。
