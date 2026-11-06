## ADDED Requirements

### Requirement: 只读数据源凭据经部署环境注入

provider 依赖的只读数据源连接串（如分片库联邦视图 DSN）SHALL 经部署环境的
secret 机制注入运行容器，SHALL NOT 出现在镜像内、仓库代码或镜像构建产物中。凭据
仅具只读权限；未配置凭据时服务 SHALL 正常启动，该 provider 标记为不可用
（配置选择它时给出明确启动错误），服务其余功能不受影响。

#### Scenario: 凭据不落镜像与代码

- **WHEN** 镜像构建产物与仓库内容被检查
- **THEN** 找不到数据源连接串；它只存在于部署环境的 secret 注入

#### Scenario: 未配置凭据不拖垮服务

- **WHEN** 部署未注入该 DSN 且 `MARKET_DATA_PROVIDER` 不是该 provider
- **THEN** 服务正常启动，其余 provider 与全部交易功能不受影响

#### Scenario: 选择该 provider 但缺凭据是明确启动错误

- **WHEN** 配置选择该 provider 而凭据缺失
- **THEN** 启动以明确错误失败并指名缺失的配置项，不以 simulated 静默顶替
