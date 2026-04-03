# seo-agent

A local SEO audit agent built with Python, Browser Use, and the Claude API. Visits real pages in a visible browser window, extracts SEO signals, checks for broken links, and writes a structured report — resumable if interrupted.

I ran it on my own published articles. Every single one failed.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Stack](#stack)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Output](#output)
- [PASS/FAIL Rules](#passfail-rules)
- [Cost](#cost)
- [Scheduling](#scheduling)
- [Premium Features](#premium-features)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [Tutorial](#tutorial)
- [License](#license)

---

## What It Does

- Reads a URL list from `input.csv`
- Visits each URL in a real Chromium browser (not a headless scraper)
- Extracts title, meta description, H1s, and canonical tag via Claude API
- Checks for broken same-domain links asynchronously using httpx
- Detects edge cases (404s, login walls, redirects) and pauses for human input
- Writes results to `report.json` incrementally — safe to interrupt and resume
- Generates a plain-English `report-summary.txt` on completion

---

## Stack

- [Browser Use](https://github.com/browser-use/browser-use) — real browser navigation via Playwright
- [Anthropic Claude API](https://console.anthropic.com) — structured SEO signal extraction
- [httpx](https://www.python-httpx.org/) — async broken link detection
- Python 3.11+, flat JSON state files, no database required

---

## Installation

```bash
git clone https://github.com/dannwaneri/seo-agent
cd seo-agent
pip install -r requirements.txt
playwright install chromium
```

---

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

---

## Usage

Interactive mode — pauses on edge cases (login walls, 404s) and asks what to do:

```bash
python main.py
```

Auto mode — skips edge cases automatically, logs them to `needs_human[]` in state, continues:

```bash
python main.py --auto
```

Resume after interruption — already-audited URLs are skipped automatically:

```bash
python main.py --auto
# Starting audit: 4 pending, 3 already done.
```

You can also run the core module directly (identical behavior, no premium features):

```bash
python core/index.py --auto
```

---

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

---

## PASS/FAIL Rules

| Field | FAIL condition |
|-------|----------------|
| Title | Missing or longer than 60 characters |
| Description | Missing or longer than 160 characters |
| H1 | Missing (count = 0) or multiple (count > 1) |
| Canonical | Missing |
| Broken links | Any same-domain link returning non-200 status |

The 60-character title limit is a display threshold, not a ranking penalty. Titles over 60 characters get truncated in Google search results. The agent flags display risk, not a ranking violation.

---

## Cost

The free tier routes every URL through Claude Sonnet for extraction. The premium tier adds a cost curve that routes cheaper checks first.

| Tier | What runs | Approximate cost per URL |
|------|-----------|--------------------------|
| Tier 1 | Deterministic Python checks (title length, H1 count, link parsing) | $0 |
| Tier 2 | Claude Haiku — meta description suggestion | ~$0.0001 |
| Tier 3 | Claude Sonnet — full extraction + opening paragraph rewrite | ~$0.006 |

Free users always use Tier 3. Premium users with `--tiered` use Tier 1 first, escalate to Tier 2 only when description signals are weak, and escalate to Tier 3 only when deeper extraction is needed. A 20-URL weekly audit at Tier 3 costs about $0.12.

---

## Scheduling

For weekly agency audits, create a batch file and schedule it with Windows Task Scheduler or cron.

**Windows (`run-audit.bat`):**

```batch
@echo off
set ANTHROPIC_API_KEY=your-key-here
cd /d C:\path\to\seo-agent
python main.py --auto
```

**macOS/Linux (cron):**

```bash
# Every Monday at 7am
0 7 * * 1 cd /path/to/seo-agent && ANTHROPIC_API_KEY=your-key python main.py --auto
```

---

## Premium Features

Premium features require a license key set as an environment variable:

```bash
# macOS/Linux
export SEO_AGENT_LICENSE="your-license-key"

# Windows PowerShell
$env:SEO_AGENT_LICENSE = "your-license-key"
```

All premium flags also require `--pro`. Running `--pro` without `SEO_AGENT_LICENSE` set exits immediately with a clear error.

### Multi-client project isolation (`--project`)

Separate input, state, and reports per client. Each project lives in `projects/NAME/`.

```bash
python main.py --pro --project acme --auto
python main.py --pro --project globex --auto
```

Each project gets its own `input.csv`, `state.json`, `report.json`, and `reports/` directory. Projects are created automatically on first run.

### Cost-curve routing (`--tiered`)

Routes each URL through the cheapest check first. Escalates to more expensive models only when needed (see [Cost](#cost) table above).

```bash
python main.py --pro --tiered --auto
```

### PDF reports

When running with `--pro`, a formatted PDF report is generated automatically at `reports/audit_report.pdf` (or `projects/NAME/reports/audit_report.pdf` with `--project`). The PDF includes a pass/fail dashboard, per-field severity ratings (HIGH/MEDIUM/LOW), and fix recommendations for every failing field.

### AI rewrite suggestions (`--rewrite`)

Generates structured rewrite suggestions for every audited URL using the cost curve:

- **Tier 1 (free):** title truncation to 60 characters, H1 recommendation, anchor text suggestions for broken links
- **Tier 2 (Haiku):** meta description suggestion for pages with missing or failing descriptions
- **Tier 3 (Sonnet):** engaging opening paragraph rewrite

```bash
python main.py --pro --rewrite --auto
```

### Voice-matched rewrites (`--voice-sample`)

Provide a text file containing a sample of your writing. The Sonnet opening paragraph rewrite will match your tone and style.

```bash
python main.py --pro --rewrite --voice-sample my-writing-sample.txt --auto
```

---

## Architecture

```
seo-agent/
├── main.py               # Unified entry point (free + pro flags)
├── config.py             # License key validation
├── input.csv             # Default URL list
├── requirements.txt
├── .gitignore
│
├── core/                 # MIT licensed — PRs welcome
│   ├── __init__.py
│   ├── browser.py        # Playwright browser driver
│   ├── extractor.py      # Claude API extraction layer
│   ├── linkchecker.py    # Async broken link checker
│   ├── hitl.py           # Human-in-the-loop pause logic
│   ├── reporter.py       # Report writer (JSON + summary)
│   ├── state.py          # State persistence + run history
│   └── index.py          # Standalone core entry point
│
└── premium/              # Proprietary — not open for contributions
    ├── __init__.py
    ├── cost_curve.py      # Three-tier routing logic
    ├── multi_client.py    # Project folder management
    ├── enhanced_reporter.py  # PDF generation with severity ratings
    └── rewrite_agent.py   # AI-powered rewrite suggestions
```

**`core/`** contains the complete, fully functional audit engine. It is MIT licensed and accepts pull requests. You can run the entire audit pipeline using only `core/` — no premium code is loaded unless you pass `--pro`.

**`premium/`** contains value-added features built on top of the core. It is proprietary and closed source. The premium modules are never imported unless `--pro` is present and a valid `SEO_AGENT_LICENSE` is set.

---

## Contributing

Pull requests are welcome for anything inside `core/`. That includes:

- Bug fixes in the browser driver, extractor, link checker, or reporter
- Support for new SEO signals (Open Graph tags, schema markup, etc.)
- Performance improvements to the async link checker
- Additional PASS/FAIL heuristics

The `premium/` directory is closed. Please do not open PRs that modify files under `premium/`.

To contribute:

```bash
git clone https://github.com/dannwaneri/seo-agent
cd seo-agent
pip install -r requirements.txt
playwright install chromium
# make your changes in core/
# run the inline acceptance tests before submitting
python core/index.py  # runs __main__ test block if present
```

---

## Tutorial

Full step-by-step walkthrough on freeCodeCamp:
[How to Build a Local SEO Audit Agent with Browser Use and Claude API](https://www.freecodecamp.org/news/how-to-build-a-local-seo-audit-agent-with-browser-use-and-claude-api)

---

## Author

Daniel Nwaneri — [DEV.to](https://dev.to/dannwaneri) · [GitHub](https://github.com/dannwaneri)

---

## License

`core/` is MIT licensed. See [LICENSE](LICENSE).

`premium/` is proprietary. All rights reserved.
