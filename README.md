# ai-portfolio

Production-grade AI portfolio monorepo: one shared platform and four products
(claims-onboarding, query-desk, rewards-advisor, meal-vision).

- Working rules and stack: [`CLAUDE.md`](CLAUDE.md)
- Phase plan and status: [`docs/plan.md`](docs/plan.md)
- Changes: [`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- Architecture: [`docs/architecture.md`](docs/architecture.md)

## Live demo

**URL: coming soon.** The free host is still being chosen (ADR 0008). Until then, run the
same demo locally:

```bash
docker compose --profile demo up --build   # then open http://127.0.0.1:8080/
```

- **Guest logins** (tenant `demo`): `demo-viewer`, `demo-analyst` and `demo-reviewer`.
  Their passwords are shown on the demo's landing page. The admin account is not public.
- **Try:** ask the AI a question containing a (synthetic) name or email and see it masked
  before the model sees it; try a prompt injection and see it blocked; request a refund
  as one guest and approve it as another.
- **Limits:** 15 AI calls per guest a day, 60 a day for everyone and 1,500 a month. After
  that you'll see "Demo limit reached" and when it resets. Data is reset every night.
- **Cold starts:** the demo sleeps when idle, so the first visit can take a few seconds
  and the first AI call a few more while the guardrail models load.

## Quick start

```bash
uv sync
uv run pre-commit install
uv run pytest
```

All data in this repository is synthetic. Secrets are supplied via environment
variables only — see `.env.example`.
