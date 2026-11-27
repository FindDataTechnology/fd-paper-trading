# Spec Delta

## Purpose

定义这条线对最终用户的公网入口：它承载哪些路径、哪些必须匿名、它与注册处网关的分工，
以及入口缺失时用户路径如何如实失败。它约束的是「用户的浏览器与 MCP 客户端怎么到达本服务」，
不是「服务怎么被构建和部署」（那是 `service-deployment`）。

## ADDED Requirements

### Requirement: 线自带公网入口

本线 SHALL 拥有一个可用的公网入口 `https://paper.finddatatech.cloud`，反代到本服务进程，
承载它的 SPA、REST（`/api/v1/*`）与 MCP（`/mcp`）。该入口 SHALL NOT 要求调用方持有注册处
凭据，因为逐用户消费使用调用者自己的 `fdt_` 令牌，而注册处网关不转发调用方凭据。

#### Scenario: 用户从公网到达 SPA

- **WHEN** 一个未登录用户打开 `https://paper.finddatatech.cloud/`
- **THEN** 返回 SPA 的 HTML（`200`），而不是注册处网关的认证错误
- **AND THEN** 用户可以在该页面上注册、登录并铸出 `fdt_` 令牌

#### Scenario: 调用者自己的令牌直打 MCP

- **WHEN** 一个 MCP 客户端用用户自铸的 `fdt_` 令牌 `POST /mcp` 发起 `initialize`
- **THEN** 握手成功且 `serverInfo.name` 为本服务的名字
- **AND THEN** 请求不经过注册处网关

### Requirement: 入口的匿名面只有健康与自描述

在公网入口上，只有 `GET /health` 与 `GET /.well-known/line-manifest.json` 可匿名访问；
其余路径 SHALL 要求有效凭据（JWT 或 `fdt_`），缺失或无效时返回 `401`。自描述端点 SHALL
返回 JSON（`content-type` 为 JSON），且 SHALL NOT 被 SPA 兜底吞成 `index.html`。

#### Scenario: 未认证的业务端点被拒

- **WHEN** 一个调用方不带任何凭据访问公网入口的 `/api/v1/accounts`、`/api/v1/orders` 或 `/mcp`
- **THEN** 每一处都返回 `401`

#### Scenario: 自描述端点匿名可读且是 JSON

- **WHEN** 匿名调用 `GET https://paper.finddatatech.cloud/.well-known/line-manifest.json`
- **THEN** 返回 `200` 且 `content-type` 为 JSON
- **AND THEN** 内容与清单契约的闭集键一致

#### Scenario: 健康端点匿名可读

- **WHEN** 匿名调用 `GET https://paper.finddatatech.cloud/health`
- **THEN** 返回 `200`

### Requirement: 入口缺失时用户路径如实失败

当公网入口不可用或未配置时，服务 SHALL 保持 mesh 通路可用（集群内调用方与运维不受影响），
SHALL NOT 静默降级为一个不可达的地址；任何把用户指向入口的产品面 SHALL 在入口不可达时
给出可读的失败说明，而不是展示一个空页面或无法完成的「去铸造」引导。

#### Scenario: 入口不可达时 mesh 仍可运维

- **WHEN** 公网入口未就绪，而集群内调用方使用 mesh 地址访问本服务
- **THEN** 请求正常完成

#### Scenario: 引导链接不指向不可达地址

- **WHEN** 产品面展示了本服务的「打开服务」链接，而该地址在本网络中不可达
- **THEN** 该链接的目标是本文要求的公网入口，而非仅 mesh 可达的地址

## MODIFIED Requirements
