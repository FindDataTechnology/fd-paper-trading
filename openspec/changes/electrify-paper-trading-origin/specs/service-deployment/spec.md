# Spec Delta

## Purpose

把「mesh 是唯一通路」修正为「mesh 是集群内通路、公网入口是用户通路」——两者并存，且
不互相替代。

## MODIFIED Requirements

### Requirement: Contact with the cluster is over the private mesh

The service SHALL be reachable from other cluster workloads over the private mesh address, and
that mesh path SHALL remain sufficient for in-cluster callers and operators whether or not a
public entry exists. A public entry MAY exist for end users (see `service-origin`); it SHALL NOT
replace or weaken the mesh path, and the service SHALL NOT depend on the public entry to be
operable from inside the cluster.

#### Scenario: Mesh reachability

- **WHEN** another workload in the cluster calls the service's MCP endpoint over the mesh
  address
- **THEN** the request reaches the service

#### Scenario: 无公网入口时集群内仍可运维

- **WHEN** 服务部署时没有任何公网 DNS 记录或公网站点块
- **THEN** 集群内调用方与运维仍可经 mesh 完成读与写

#### Scenario: No public domain required

- **WHEN** the service is deployed without any public DNS record
- **THEN** it remains fully operable for mesh callers and the registry gateway

#### Scenario: 公网入口存在时 mesh 逐字节不变

- **WHEN** 公网入口上线后，一个集群内调用方仍用未改动的 mesh 地址访问
- **THEN** 请求行为与上线前一致（端点、凭据、返回形状均不变）
