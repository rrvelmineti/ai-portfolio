# infra/terraform

The AWS production path for the platform (ADR 0008). **Validated in CI on every pull
request and never applied from this repository.** The free public demo runs elsewhere;
this is where the same container image goes when the platform needs production
guarantees.

| Directory | What it creates |
|---|---|
| `modules/network` | VPC over two named AZs, public + private subnets, NAT, encrypted VPC flow logs, a closed default security group |
| `modules/ecr` | Image repository: immutable tags, scan on push, KMS encryption, untagged-image expiry |
| `modules/database` | RDS Postgres 16 (pgvector available) in private subnets: TLS required, KMS storage, Multi-AZ, backups, deletion protection, master password managed by RDS in Secrets Manager |
| `modules/secrets` | Secrets Manager entries for the app's secret env vars. **Values are set outside Terraform**, so no secret enters state or the repo |
| `modules/service` | ECS Fargate service (non-root, read-only root filesystem) behind an HTTPS ALB with AWS WAF (AWS managed rules + per-IP rate limit), deployment circuit breaker with rollback |
| `envs/production` | Composition: one KMS key (rotation on), the ALB access-log bucket, and the modules above |

Same rules as the demo: migrations and the app-role bootstrap run from CI with the
**owner** credentials (`scripts/migrate_and_bootstrap.py`, reading the RDS master secret);
the app's `DATABASE_URL` secret uses the restricted `ai_portfolio_app` role. Only the ALB
(inside the VPC range) may set `X-Forwarded-For`.

## CI checks (`.github/workflows/ci.yml`, job `terraform`)

```bash
terraform fmt -recursive -check
cd envs/production && terraform init -backend=false -lockfile=readonly && terraform validate
tflint --init && tflint --recursive --config "$PWD/.tflint.hcl"
uvx checkov==3.4.10 -d . --framework terraform
```

No AWS credentials exist in CI, and nothing runs `plan` or `apply`. The checkov
exceptions are inline (`#checkov:skip=...`), each with its reason.

## Applying it later

Not yet. When it's time: an S3 state bucket (`terraform init -backend-config=...`), a CI
OIDC role with least privilege, an ACM certificate and an image digest as
`-var` inputs, and a reviewed `plan` before every `apply`.
