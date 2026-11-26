# CLAUDE.md — ai-portfolio

## Project goal

`ai-portfolio` is a production-grade AI portfolio monorepo. It holds **one shared
platform** and **four products**:

| Product | Folder |
|---|---|
| Claims Onboarding | `products/claims-onboarding` |
| Query Desk | `products/query-desk` |
| Rewards Advisor | `products/rewards-advisor` |
| Meal Vision | `products/meal-vision` |

Industry standards apply: tests, CI, no secrets in code, synthetic data only,
type hints everywhere.

## Golden rule

**Build one phase at a time. Plan first, then wait for explicit approval before
writing any files for that phase.** Phases and their status live in `docs/plan.md`.

## Stack

- Python 3.12
- FastAPI
- Postgres + pgvector
- LangChain, LangSmith
- Presidio (PII detection / redaction)
- Docker
- Terraform (AWS)
- GitHub Actions
- Tooling: `uv` (dependencies + lockfile), Ruff, pytest, pytest-cov, mypy, pre-commit

## Coding rules

- **Type hints everywhere.** Ruff `ANN` rules and mypy (strict) enforce this.
- **pytest for every module**, with **≥ 80% coverage** enforced in CI
  (`--cov-fail-under=80`).
- **Ruff** for lint and formatting; code must pass `ruff check` and `ruff format --check`.
- **Structured JSON logging** only — no bare `print` in application code.
- **Secrets only from environment variables.** Never commit secrets; `.env` is
  gitignored and `.env.example` documents the variable names with dummy values.
- **Synthetic data only.** No real customer, personal, or production data anywhere
  in the repo (fixtures, seeds, notebooks, tests).
- Shared platform code lives under `core/` (never a folder or package named after a
  stdlib module, e.g. `platform`). Each component ships its own package, such as
  `core/common/src/ai_platform_common`. Product
  folders use hyphens; their Python packages use underscores (e.g. `claims_onboarding`).

## Workflow rules

- **One git branch per phase**, named `phase-X.Y-<short-name>` (e.g. `phase-0.1-foundation`).
- **Small, focused commits** with clear messages.
- **Update `docs/CHANGELOG.md`** in every phase.
- **Write a short decision note in `docs/decisions/`** for every major design choice,
  named `NNNN-short-title.md` (context, decision, consequences).
- Before finishing a phase: run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy`, and `uv run pytest`, and report the results.

## Folder structure

```
ai-portfolio/
├── core/                 # shared platform components
│   ├── gateway/          # model gateway: the only way products call AI models
│   ├── auth/             # authentication, roles, tenants, audit log
│   ├── observability/    # JSON logging, tracing, LangSmith integration
│   ├── guardrails/       # PII redaction (Presidio), input/output safety checks
│   └── common/           # shared utilities and base types
├── products/
│   ├── claims-onboarding/
│   ├── query-desk/
│   ├── rewards-advisor/
│   └── meal-vision/
├── infra/
│   └── terraform/        # AWS infrastructure as code
├── .github/
│   └── workflows/        # CI/CD pipelines
└── docs/
    ├── plan.md
    ├── CHANGELOG.md
    └── decisions/        # architecture decision records
```

## Common commands

```bash
uv sync                        # install dependencies
uv run pre-commit install      # enable git hooks
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy                    # type check
uv run pytest                  # tests + coverage
```
