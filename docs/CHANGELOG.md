# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Phase 0.1: Platform foundation
- `CLAUDE.md` with project goal, stack, coding and workflow rules.
- Monorepo folder structure (`platform/`, `products/`, `infra/terraform`,
  `.github/workflows`, `docs/decisions`) with a README in each folder.
- uv workspace on Python 3.12 with lockfile (`uv.lock`).
- Ruff (lint + format), strict mypy, pytest with an 80% coverage gate.
- Pre-commit hooks (Ruff, mypy, whitespace/YAML/TOML checks, private-key detection).
- GitHub Actions CI running lint, format check, type check and tests on every PR.
- Sample package `ai_platform_common` with tests.
- `docs/plan.md` and ADR 0001.
