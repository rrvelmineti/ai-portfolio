# ai-portfolio

Production-grade AI portfolio monorepo: one shared platform and four products
(claims-onboarding, query-desk, rewards-advisor, meal-vision).

- Working rules and stack: [`CLAUDE.md`](CLAUDE.md)
- Phase plan and status: [`docs/plan.md`](docs/plan.md)
- Changes: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## Quick start

```bash
uv sync
uv run pre-commit install
uv run pytest
```

All data in this repository is synthetic. Secrets are supplied via environment
variables only — see `.env.example`.
