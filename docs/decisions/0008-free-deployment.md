# 0008. Free public demo deployment (host decision open) and the AWS production path

- Status: Accepted, except the hosting choice, which is **open** (see "Hosting")
- Date: 2026-11-27

## Context

Recruiters should be able to open a live demo of the platform. Hard rule from the owner:
**no paid services and no credit card anywhere.** If something needs a card, stop.

What the demo has to run:
- the auth app with the guardrails as built and measured: spaCy `en_core_web_lg` for PII
  and the guard-small ONNX classifier for prompt injection;
- about 1.45 GB peak memory, measured locally with both models loaded.

Free tiers changed in 2026 (checked on vendor pages on 2026-11-27):

| Host | Card? | Fits about 1.5 GB? | Note |
|---|---|---|---|
| Hugging Face Spaces (Docker) | — | — | **Docker Spaces now need a paid plan** |
| Koyeb | **Yes** ($29 hold since Feb 2026) | no (512 MB) | |
| Fly.io | **Yes** for the free allowance | — | trial machines stop after 5 min |
| Google Cloud Run | **Yes** (billing account) | yes | no hard cap; overage is billed |
| Render free | No | **no (512 MB)** | sleeps after 15 min, about 1 min to wake |
| Leapcell Hobby | No | **yes (up to 4 GB serverless)** | see limits below |

Database: **Neon Free** (no card, never paused for inactivity, scales to zero after
5 minutes and resumes in under a second, pgvector on every plan; over-quota suspends
compute until next month, never bills). Supabase pauses idle projects after a week and
Render's free Postgres expires after 30 days.

## Decision

### Hosting: open

- **Chosen, pending a spike:** Leapcell Hobby (serverless, 2 GB) plus Neon Free.
- **The spike is paused.** Leapcell's GitHub sign-in fails for the owner, so nothing has
  been deployed. Until it works, the platform is built and tested to be host-neutral and
  every deploy workflow is switched off (below).
- **What the spike must confirm:**
  - it builds our Dockerfile, or deploys a prebuilt image, or at least runs our shared
    build script;
  - the Python version;
  - memory headroom at 2 GB;
  - cold-start time and GB-hours per cold visit (`/diag/warmup`);
  - Leapcell's proxy address ranges for `TRUSTED_PROXY_CIDRS` (`/diag/client`).

  Stop if a build takes over 10 minutes or anything asks for a card.
- **Fallback (option C), only with the owner's approval:** Render free, with a labelled
  "lite" profile (spaCy `sm`, rules only, no classifier), because 512 MB can't hold the
  measured system. The demo would then say so, since our published accuracy numbers are
  for `lg` + guard-small.
- **No other host is used without asking the owner.**

**Leapcell limits and vendor risk:**
- 3 GB-hours of execution a month (about 90 minutes at 2 GB);
- 100k invocations; 2 GB transfer;
- 60 build minutes a month, at most 10 per build;
- only `/tmp` is writable;
- over any limit it returns HTTP 429; it never bills.

It's a small, young vendor. Because everything is a plain container plus a shared build
script, moving to another host is a configuration change. Keep-alive pings are not used:
on Leapcell they burn execution time, and on Render they break the acceptable-use policy.

### What runs where

```
visitor ─HTTPS─▶ host proxy ─▶ demo app (container, non-root, read-only except /tmp)
                                 │  connects as ai_portfolio_app (restricted)
                                 ▼
                               Neon Postgres ◀── GitHub Actions (owner role):
                                                  migrate, bootstrap role, seed,
                                                  nightly reset
```

- **One build script** (`scripts/build_runtime.sh`) for both the Dockerfile and the
  host's build command: `uv sync --frozen` without dev tools, plus the verified
  guard-model download. Models are baked in, not downloaded at startup. Image: 2.56 GB
  uncompressed.
- **Startup is cheap:**
  - no migrations at startup (`RUN_MIGRATIONS_ON_START` is for local use only);
  - spaCy and the classifier load lazily, on the first AI call, so health checks,
    crawlers and logins don't pay for them;
  - `/robots.txt` disallows everything and every response carries
    `X-Robots-Tag: noindex, nofollow`.

### Database roles

| Role | Used by | Can |
|---|---|---|
| **Owner** (e.g. `neondb_owner`) | CI only (`NEON_OWNER_DATABASE_URL` Actions secret) | migrations, `scripts/migrate_and_bootstrap.py`, seeding, the nightly reset |
| **`ai_portfolio_app`** | the running app | exactly `ai_platform_demo.roles.GRANTS`: no DDL, no TRUNCATE, no DELETE anywhere; on `auth.audit_log` only SELECT and INSERT |

- The bootstrap is idempotent. It rotates the password from `APP_DB_PASSWORD`, and it
  **refuses to run if any table lacks an explicit privilege decision**.
- Tests on a real Postgres prove the app role gets "permission denied" on UPDATE, DELETE
  and TRUNCATE of the audit log, even with the retention flag set.

### Audit retention policy

The audit log stays append-only for the app, always. The demo can't grow without bound,
so:

- **Only the owner role** may delete audit rows, and only rows **older than 7 days**, and
  only inside a transaction that sets `ai_portfolio.audit_retention = 'on'`. All three
  are enforced by the audit trigger (auth migration 0002).
- UPDATE and TRUNCATE remain blocked for every role, owner included.
- The nightly reset prunes **demo-tenant** rows older than 7 days and then writes a
  `demo.reset` audit row with the counts of what it removed. Other tenants' rows are
  never touched.

### Guest logins and the nightly reset

- **Seeded guests:** `demo-viewer`, `demo-analyst` and `demo-reviewer`. The landing page
  shows their passwords, which come from host settings.
- **The demo admin stays private:** the landing page code never reads
  `DEMO_ADMIN_PASSWORD`, and the hosted app isn't given it; only CI holds it, for
  seeding.
- **Tests prove** that guests get 403 on creating or listing users, changing roles
  (including their own) and tenants, and that no response (`/`, `/docs`, `/openapi.json`,
  `/metrics`, `/auth/me`, `/demo/ask`, errors) contains the JWT secret, provider keys, the
  database password or the admin password.
- **The nightly reset** (`scripts/reset_demo.py`, owner role, one transaction):
  - deletes the demo tenant's approvals, AI-call records, refresh tokens and any users
    not in the seed;
  - restores the seeded users' passwords, roles, active flag and lock state;
  - deletes **earlier days'** usage counters but keeps today's and the month's, so a
    reset never gives back AI calls;
  - prunes audit rows (above).

### Abuse and cost protection

- **Login:** rate limited per client IP (existing). AI calls: limited per user and per
  IP (`DEMO_AI_RATE_LIMIT`, 5 a minute).
- **Caps** in Postgres (`demo.ai_usage`), so they survive cold starts:

  | Cap | Default |
  |---|---|
  | per guest, per UTC day | 15 |
  | all users, per UTC day | 60 |
  | all users, per UTC month | 1,500 |

  - The caps sit below the host's quota, so visitors see **"Demo limit reached … It
    resets at …"** (HTTP 429 with `Retry-After`) and not the host's 429.
  - A call is counted **before** the model call, so failures count too: the compute is
    spent either way.
- **Free providers only:** the `demo-free` alias is Gemini, then Groq. A test fails if a
  paid provider is ever added to it, and another proves a Gemini/Groq outage returns 503
  rather than falling back to Anthropic or OpenAI. No paid keys are given to the host.
  Gemini's free tier may use prompts to improve Google's products. That's acceptable only
  because all demo data is synthetic and PII is masked before any provider sees it.
- **Client IP for rate limits:**
  - `X-Forwarded-For` is honoured only when the TCP peer is in `TRUSTED_PROXY_CIDRS`;
  - the client is the right-most address not in those ranges, so a visitor can't fake
    an IP to dodge rate limits;
  - Leapcell's ranges are still to be confirmed in the spike. Until then
    `TRUST_PROXY_HEADERS` stays off, and all visitors share the proxy's address for rate
    limiting, which is stricter, not looser.

### CI/CD

- **Every PR and main** (`ci.yml`):
  - lint, types and tests;
  - **image build plus Trivy**, failing on CRITICAL vulnerabilities that have a fix;
  - a container smoke test (non-root, `/healthz`, robots);
  - compose config validation;
  - **Terraform validation**.
  - Third-party actions are pinned to commit SHAs. Trivy's action had tags hijacked
    earlier in 2026.
- **`deploy.yml`:** off until the repository variable `DEPLOY_ENABLED` is `true`. It
  runs after CI passes on main and never runs for PRs:
  1. migrate, bootstrap and seed with the owner secret;
  2. move the `deploy` branch, which the host builds from, to the tested commit;
  3. smoke-check `/readyz`, a guest login and one guarded AI call
     (`scripts/smoke_check.py`);
  4. if the check fails, move `deploy` back.

  It can't confirm which commit is live until the host is known; it waits and polls
  `/readyz`.
- **`demo-reset.yml`:** nightly at 03:17 UTC, off until `DEMO_RESET_ENABLED` is `true`.
  It shares a concurrency group with deploys.

### AWS production path

`infra/terraform` holds the production design, validated in CI (fmt, validate, tflint,
checkov) and **never applied**:
- VPC across two named AZs;
- ECR;
- RDS Postgres 16 (TLS, KMS, Multi-AZ, RDS-managed owner secret);
- Secrets Manager entries whose values are set outside Terraform;
- ECS Fargate (non-root, read-only root filesystem) behind an HTTPS ALB with WAF.

**What changes for production:**

| | Free demo | AWS |
|---|---|---|
| Compute | serverless, cold starts, 2 GB, models loaded lazily | always-on tasks (≥ 2), no cold starts; models could load eagerly at startup |
| Database | Neon free, scale to zero, 0.5 GB | RDS Multi-AZ, backups, deletion protection |
| Secrets | host settings and Actions secrets | Secrets Manager plus KMS, injected at task start |
| Edge | host's proxy | ALB + WAF (managed rules, per-IP rate limit); `TRUSTED_PROXY_CIDRS` = VPC range |
| Models | free tiers only (Gemini, Groq) | any gateway alias, including paid ones, with real cost tracking |
| Rate limits | in-memory (one instance) | a shared store (Redis) once there are several tasks |
| Deploys | move a branch, then smoke check | image digest to ECS, circuit breaker with rollback |

## Consequences

- **Total cost is $0** and nothing can bill: every free tier involved stops at its quota.
- **Visitors may wait:** a cold visit takes about 6–7 s, and the first AI call 3–4 s more
  for model loading. The landing page says so.
- **The Leapcell setup is unproven:** it depends on the spike. Everything else (roles,
  caps, reset, guests, image, CI, Terraform) is built and tested now, independent of
  the host.
- **Two switches to flip after the spike:** `DEPLOY_ENABLED` and `DEMO_RESET_ENABLED`,
  plus `DEMO_BASE_URL` and the host's environment settings.
- **Secret rotation:** provider keys, `JWT_SECRET` and `APP_DB_PASSWORD` are rotated by
  hand. Update the Actions secret and host setting, then redeploy; the bootstrap applies
  a new app-role password. Rotating `JWT_SECRET` logs everyone out.
