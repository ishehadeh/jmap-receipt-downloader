# Agent guide

## Project overview

Single-file Python CLI (`receipts.py`) that pulls emails from Fastmail via JMAP and saves receipts as PDFs/PNGs/text. Rules in `rules.yaml` map senders/subjects to actions. No database, no framework.

## Key files

| File | Role |
|---|---|
| `receipts.py` | All logic: JMAP client, rule matching, output-path helpers, five action functions, CLI |
| `rules.yaml` | Ordered list of match → action rules; first match wins |
| `tests/` | pytest suite using real in-process HTTP servers (`pytest-httpserver`) |
| `Dockerfile` / `docker-compose.yml` | Production image (Playwright bundled); `test` service for CI |

## Running tests

```bash
pip install -r requirements-dev.txt
pytest                     # skips playwright-marked tests automatically unless Chromium is installed
pytest -m "not playwright" # explicitly skip browser tests
```

Docker (includes Chromium, runs everything):
```bash
docker compose run --rm test
```

## Adding a new rule

Edit `rules.yaml` only. No Python changes needed for new senders.

1. Identify the match condition (sender domain is most common).
2. Choose an action (`html`, `save_attachment`, `fetch_link`, `screenshot_link`, or `text`).
3. Add a `name:` label and any `options:` the action needs.
4. Test with `--dry-run` to confirm the email matches before committing.

Use `--dry-run` to preview which emails match which rules:
```bash
python receipts.py --after 2026-06-01 --before 2026-07-01 --dry-run
```

## Adding a new action

1. Define `action_<name>(client, email, rule, out_dir)` in `receipts.py`.
2. Register it in the `ACTIONS` dict near the bottom of the file.
3. Add a test in `tests/test_actions.py` using the `jmap_client` and `receipt_server` fixtures from `conftest.py`.

## Code conventions

- All code lives in `receipts.py`. Resist splitting into modules unless the file grows substantially.
- `make_output_path()` handles all file naming and collision avoidance — always use it.
- Playwright imports are deferred (`from playwright… import …` inside functions) so the CLI works without Playwright installed when only non-browser actions are used.
- `extract_links()` searches HTML body first, then text body; `link_pattern` is a regex filter applied to the URL string.

## Environment

- `FASTMAIL_TOKEN` — required at runtime; never hardcode.
- `PLAYWRIGHT_BROWSERS_PATH` — set by Docker image; locally, `playwright install chromium` puts browsers in the default location.
