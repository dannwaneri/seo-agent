# seo-agent

A local SEO audit agent built with Python, Browser Use, and the Claude API. Visits real pages in a visible browser window, extracts SEO signals, checks for broken links, and writes a structured report — resumable if interrupted.

I ran it on my own published articles. Every single one failed.

## What It Does

- Reads a URL list from `input.csv`
- Visits each URL in a real Chromium browser (not a headless scraper)
- Extracts title, meta description, H1s, and canonical tag via Claude API
- Checks for broken same-domain links asynchronously using httpx
- Detects edge cases (404s, login walls, redirects) and pauses for human input
- Writes results to `report.json` incrementally — safe to interrupt and resume
- Generates a plain-English `report-summary.txt` on completion

## Stack

- [Browser Use](https://github.com/browser-use/browser-use) — real browser navigation via Playwright
- [Anthropic Claude API](https://console.anthropic.com) — structured SEO signal extraction
- [httpx](https://www.python-httpx.org/) — async broken link detection
- Python 3.11+, flat JSON state files, no database required

## Prerequisites

- Python 3.11 or higher
- An Anthropic API key
- Windows, macOS, or Linux

## Installation

```bash
git clone https://github.com/dannwaneri/seo-agent
cd seo-agent
pip install -r requirements.txt
playwright install chromium
```

## Configuration

Set your API key as an environment variable:

```bash
# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-..."

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

Add your URLs to `input.csv`:

```
url
https://example.com
https://example.com/about
https://example.com/contact
```

## Usage

Interactive mode — pauses on edge cases (login walls, 404s) and asks what to do:

```bash
python index.py
```

Auto mode — skips edge cases automatically, logs them to `needs_human[]` in state, continues:

```bash
python index.py --auto
```

Resume after interruption — already-audited URLs are skipped automatically:

```bash
python index.py --auto
# Starting audit: 4 pending, 3 already done.
```

## Output

**`report.json`** — structured audit result per URL:

```json
{
  "url": "https://example.com",
  "title": { "value": "Example", "length": 7, "status": "PASS" },
  "description": { "value": null, "length": 0, "status": "FAIL" },
  "h1": { "count": 1, "value": "Welcome", "status": "PASS" },
  "canonical": { "value": "https://example.com", "status": "PASS" },
  "broken_links": { "count": 0, "status": "PASS" },
  "flags": ["Meta description is missing"]
}
```

**`report-summary.txt`** — plain-English summary:

```
https://example.com          | FAIL [description]
https://example.com/about    | PASS
https://example.com/contact  | FAIL [title, canonical]

1/3 URLs passed
```

## PASS/FAIL Rules

| Field | FAIL condition |
|-------|---------------|
| Title | Missing or longer than 60 characters |
| Description | Missing or longer than 160 characters |
| H1 | Missing (count = 0) or multiple (count > 1) |
| Canonical | Missing |
| Broken links | Any same-domain link returning non-200 status |

The 60-character title limit is a display threshold, not a ranking penalty. Titles over 60 characters get truncated in Google search results. The agent flags display risk, not a ranking violation.

## Project Structure

```
seo-agent/
├── index.py          # Main audit loop
├── browser.py        # Playwright browser driver
├── extractor.py      # Claude API extraction layer
├── linkchecker.py    # Async broken link checker
├── hitl.py           # Human-in-the-loop pause logic
├── reporter.py       # Report writer
├── state.py          # State persistence
├── input.csv         # Your URL list
├── requirements.txt
├── .env.example
└── .gitignore
```

## Scheduling

For weekly agency audits, create a batch file and schedule it with Windows Task Scheduler or cron.

**Windows (`run-audit.bat`):**

```batch
@echo off
set ANTHROPIC_API_KEY=your-key-here
cd /d C:\path\to\seo-agent
python index.py --auto
```

**macOS/Linux (cron):**

```bash
# Every Monday at 7am
0 7 * * 1 cd /path/to/seo-agent && ANTHROPIC_API_KEY=your-key python index.py --auto
```

## Cost

Claude Sonnet 4 is priced at $3 per million input tokens and $15 per million output tokens. A typical page audit uses ~500 input tokens and ~300 output tokens — roughly $0.006 per URL. A 20-URL weekly audit costs about $0.12.

## Tutorial

Full step-by-step walkthrough on freeCodeCamp:
[How to Build a Local SEO Audit Agent with Browser Use and Claude API](https://www.freecodecamp.org/news/how-to-build-a-local-seo-audit-agent-with-browser-use-and-claude-api)

## Author

Daniel Nwaneri — [DEV.to](https://dev.to/dannwaneri) · [GitHub](https://github.com/dannwaneri)

## License

MIT
