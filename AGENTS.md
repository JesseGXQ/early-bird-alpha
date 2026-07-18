# AGENTS.md — Multi-factor Trend Following

## Commands

```powershell
pytest tests/                              # run all tests
python -m pytest tests/data/... -s        # single test with stdout
```

Python via conda (`C:\Users\Jesse\AppData\Local\anaconda3\python.exe`). No `pyproject.toml`, `setup.py`, or requirements files — dependency management is ad-hoc. No lint/typecheck tooling is configured.

## What is implemented

Only **`data/`** (the Data Engine) has production code. All other top-level dirs are **empty placeholders**:
- `factors/`, `strategy/`, `risk/`, `backtester/`, `executor/` — no code, no `__init__.py`.

## Architecture — Data Engine

Entry point: `DataFacade` (`data/facade.py`). Factory assembly in `data/factory.py`.

Two feed types:
- **ResearchFeed** (`data/feed/research_feed.py`) — historical adjusted price vectors (1d, 1min→Nmin). Primary method: `get_historical_vector(start_date, end_date, freq)`.
- **SimulationFeed** (`data/feed/simulation_feed.py`) — tick-level snapshot streams for event-driven simulation.

Three-tier rules database (all in `db/rules/`):
- **Tier 1** — `product_meta/`: static product attributes (YAML per product code)
- **Tier 2** — `instrument_registry/`: contract-level registration (list/list dates, multipliers)
- **Tier 3** — `instrument_daily_status/`: daily dynamic state (margin, commission, tradability)

Unified rules facade: `TradingRuleService` (`data/rules/trading_rule_service.py`).

Key config dataclasses (`data/config.py`): `RolloverConfig`, `AdjustmentConfig`, `SamplingConfig`, `EnvironmentConfig`.

Data directories (defaults in `EnvironmentConfig`, some overridden in tests):
- Raw 1min: `D:\量化策略\CTA策略\RAW_DATA\1min` (absolute, machine-local)
- Raw TICK: `D:\量化策略\CTA策略\RAW_DATA\TICK`
- Master tables: `db/derived/master_tables/`
- Calendars: `db/calendar/trading_calendars/`

## Tests

- All tests live under `tests/data/` (calendar, cleaning, feed, rules, storage).
- Fixtures are `session`-scoped and load real data from `db/rules/`, `db/calendar/`, `db/market/`.
- Some tests use hardcoded absolute paths to raw data (see `test_research_feed.py`, `test_file_locator.py`) — these require specific local disk state and will fail in a clean checkout.
- VS Code debug preset: `pytest -s` on the current file (`.vscode/launch.json`).

## Conventions

- Code comments and documentation are **Chinese** (bilingual where English is used for code identifiers).
- Product codes: 1–2 uppercase letters (RB, IF, AG, TF, etc.).
- Contract naming: `{PRODUCT}{year}{month}` — e.g., `RB2510`, `IF2109`.
- Rollover config signature string: `b{buffer_days}_r{ratio_int}_t{transition_days}` (e.g., `b3_r110_t1`).
- Copilot instructions at `.github/copilot-instructions.md` enforce "analyze only, don't modify code unless explicitly asked" — this applies to GitHub Copilot, not OpenCode.

## Gotchas

- Any work requiring the empty placeholder dirs (`factors/`, `strategy/`, etc.) must create them from scratch.
- No dependency manifest exists; adding imports may require manual `pip install` / conda install.
- Raw data paths are absolute (`D:\量化策略\CTA策略\RAW_DATA\...`) — this is a single-machine setup.
