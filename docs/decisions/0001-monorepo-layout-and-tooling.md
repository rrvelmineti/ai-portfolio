# 0001. Monorepo layout and Python tooling

- Status: Accepted
- Date: 2026-11-26

## Context

The repo holds one shared platform and four products that will each grow their own
dependencies. We need reproducible installs, consistent quality gates, and a layout
that keeps platform and product code clearly separated.

A top-level folder named `platform/` was required. If it were an importable Python
package it would shadow the standard-library `platform` module, which pytest and
many libraries import.

## Decision

- **uv workspace.** The root `pyproject.toml` is a non-packaged workspace root; each
  platform component and product becomes a workspace member with its own
  `pyproject.toml`. One shared `uv.lock` pins everything.
- **`src/` layout per member, distinct package names.** `platform/` and `products/`
  are plain folders, never packages. Code lives in e.g.
  `platform/common/src/ai_platform_common`; products use underscore names
  (`claims_onboarding`).
- **Quality gates:** Ruff (lint + format, incl. `ANN` for type hints, `S` for security,
  `T20` to ban `print`), strict mypy, pytest with `--import-mode=importlib` and an
  80% coverage floor. The same checks run in pre-commit (via `uv run`, so versions
  match the lockfile) and in GitHub Actions.

## Consequences

- Adding a component = add a folder with `pyproject.toml`, register it in
  `[tool.uv.workspace].members`, and add its paths to mypy `files`.
- Coverage is measured across all of `platform/` and `products/`, so every new
  module must ship with tests to keep CI green.
- Contributors need `uv` installed; CI uses `astral-sh/setup-uv`.
