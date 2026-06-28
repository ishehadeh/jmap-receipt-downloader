# Receipt Downloader

Downloads purchase receipts from a Fastmail email folder via the JMAP API and saves them as PDFs, images, or text files, organized by month.

## How it works

1. Connects to Fastmail via JMAP using an API token.
2. Fetches emails from a mailbox (default: `Receipts`) within a date range.
3. Matches each email against rules defined in `rules.yaml` (first match wins; unmatched emails are skipped).
4. Runs the matched action to produce a file in `out/YYYY-MM/YYYY-MM-DD_Subject.ext`.

## Requirements

- Python 3.10+
- Playwright with Chromium (for `html`, `fetch_link`, and `screenshot_link` actions)
- A [Fastmail API token](https://www.fastmail.com/settings/security/tokens) with `Mail (read-only)` scope

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

Or use Docker (Playwright is bundled in the image):

```bash
docker compose build
```

## Usage

```bash
export FASTMAIL_TOKEN=your_token_here
python receipts.py --after 2026-06-01 --before 2026-07-01
```

**All flags:**

| Flag | Default | Description |
|---|---|---|
| `--token` | `$FASTMAIL_TOKEN` | Fastmail API token |
| `--folder` | `Receipts` | Mailbox folder name |
| `--after` | *(required)* | Start date inclusive, `YYYY-MM-DD` |
| `--before` | *(required)* | End date exclusive, `YYYY-MM-DD` |
| `--rules` | `rules.yaml` | Path to rules file |
| `--out` | `./out` | Output directory |
| `--dry-run` | off | Print what would be done without saving |

### Docker

```bash
FASTMAIL_TOKEN=your_token docker compose run --rm receipts \
  --after 2026-06-01 --before 2026-07-01
```

Output is written to `./out` on the host via the volume mount in `docker-compose.yml`.

## Output structure

```
out/
  2026-06/
    2026-06-15_Your_Receipt_1234.pdf
    2026-06-20_Order_Confirmation.png
    2026-06-22_Payment_Confirmation.txt
```

Files are never overwritten; a numeric suffix (`_1`, `_2`, …) is appended on collision.

## Rules

Rules live in `rules.yaml`. They are matched in order — the first match wins. Emails that match no rule are skipped.

```yaml
rules:
  - name: "Human-readable label"
    match:                        # all fields optional, ANDed together
      from: "exact@address.com"
      from_domain: "example.com"  # matches subdomains too
      from_regex: "billing@.*"
      subject: "substring"
      subject_regex: "Order #\\d+"
    action: html                  # see actions below
    options: {}
```

### Actions

| Action | What it produces | Key options |
|---|---|---|
| `html` | PDF rendered from the email's HTML body | `body_part: text` to use plain-text instead |
| `text` | `.txt` file from the email's text body | `body_part: html` to use HTML instead |
| `save_attachment` | Downloads attached files | `mime_types: [application/pdf]` to filter |
| `fetch_link` | HTTP-GETs a URL from the email; HTML responses are printed to PDF via Playwright | `link_pattern: "stripe"` to filter URLs by regex |
| `screenshot_link` | Full-page Playwright screenshot (PNG) of a URL in the email | `link_pattern: "squareup\\.com/r/"` |

## Testing

Tests use a real in-process HTTP server (no external services needed). Playwright tests require Chromium.

```bash
pip install -r requirements-dev.txt
pytest                          # all tests except playwright
pytest -m "not playwright"      # skip browser tests
```

Via Docker:

```bash
docker compose run --rm test
```
