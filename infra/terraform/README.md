# infra/terraform

Terraform configuration for AWS (networking, compute, Postgres with pgvector,
secrets management). No credentials are stored here; AWS access comes from the
environment / CI OIDC role. Remote state configuration will be added in a later phase.

Status: not started.
