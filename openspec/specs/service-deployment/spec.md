# service-deployment Specification

## Purpose
How the paper-trading service is built, deployed, configured, and released: the image
pipeline, where its deployment manifests live, the database it owns, the port it is
reachable on, and the environment it needs at startup.

## Requirements

### Requirement: Container image and immutable release tags

The service SHALL be built into a container image that serves both the API and the built
SPA from a single process, and SHALL be released only under immutable tags. Deployments
SHALL NOT reference a floating tag.

#### Scenario: One process serves API and SPA

- **WHEN** the container is started
- **THEN** the REST API, the WebSocket endpoint, and the built SPA are all served from the
  same port

#### Scenario: Deployed image is immutable

- **WHEN** the deployed manifest's image field is inspected
- **THEN** it names a specific immutable tag rather than a floating one

### Requirement: Deployment manifests live in the infrastructure repository

The service's complete workload manifests (Deployment, Service, and any persistent volume
claim) SHALL be owned by the `fd-infra-deploy` repository under its production manifest
tree, and SHALL be reconciled by ArgoCD so that out-of-band edits are reverted.

#### Scenario: Manifests are in the infrastructure repository

- **WHEN** the service's Deployment and Service manifests are located
- **THEN** they reside in `fd-infra-deploy`'s production manifest tree rather than only in
  the service repository

#### Scenario: Manual drift is reverted

- **WHEN** the running Deployment's image or environment is changed directly through the
  cluster API instead of through git
- **THEN** ArgoCD restores the git-declared state within a reconciliation cycle

### Requirement: Dedicated PostgreSQL instance

The service SHALL store production state in a PostgreSQL database it owns, following the
per-service convention, and SHALL NOT share a database with another service. The
connection string SHALL be supplied through the deployment environment and SHALL NOT be
committed to any repository.

#### Scenario: Own database

- **WHEN** the service's production connection string is inspected
- **THEN** it names a database dedicated to this service

#### Scenario: Credentials are not in git

- **WHEN** the service repository and the infrastructure manifest tree are searched for a
  plaintext database URL carrying a password
- **THEN** no such value is found; the value is supplied through a cluster secret

#### Scenario: Missing configuration fails loudly

- **WHEN** the service starts in production without a database URL configured
- **THEN** it fails to start rather than silently falling back to an ephemeral local store

### Requirement: NodePort ledger is updated first

The service's NodePort SHALL be registered in the port ledger before any manifest
referencing it is applied, and the ledger SHALL be the authoritative record of the
allocation.

#### Scenario: Ledger has the row

- **WHEN** the port ledger is inspected
- **THEN** the service's NodePort row exists with its purpose and owner recorded

#### Scenario: Ledger precedes the manifest

- **WHEN** the change that introduces the NodePort is reviewed
- **THEN** the ledger update appears in the same change, ahead of the manifest that uses it

### Requirement: Resource requests fit the namespace quota

The service's resource requests and limits SHALL fit within the namespace quota, and where
they do not, the quota SHALL be raised in the same change that introduces the workload.

#### Scenario: Pod is admitted

- **WHEN** the Deployment is applied to the production namespace
- **THEN** the pod is admitted without an exceeded-quota rejection

#### Scenario: Quota change is recorded

- **WHEN** the quota needed to be raised to admit the workload
- **THEN** the raise is part of the same change, with the reason recorded alongside it

### Requirement: Contact with the cluster is over the private mesh

The service SHALL be reachable from other cluster workloads over the private mesh address,
and SHALL NOT require a public domain to be operated.

#### Scenario: Mesh reachability

- **WHEN** another workload in the cluster calls the service's MCP endpoint over the mesh
  address
- **THEN** the request reaches the service

#### Scenario: No public domain required

- **WHEN** the service is deployed without any public DNS record
- **THEN** it remains fully operable for mesh callers and the registry gateway
