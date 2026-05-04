# seo-agent

A local SEO co-pilot built with Python, Browser Use, and the Claude API. Visits real pages in a visible browser window, extracts SEO signals, checks for broken links, scores backlinks, surfaces GSC quick wins, maps internal link clusters, and writes structured reports — resumable if interrupted.

Ran it on my own sites. Found a title cannibalising its own homepage, a position 9.5 query with 0% CTR, two missing internal links, and an orphan page with no path to it.

Everything is open source.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Stack](#stack)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Modules](#modules)
- [Output](#output)
- [PASS/FAIL Rules](#passfail-rules)
- [Cost](#cost)
- [Scheduling](#scheduling)
- [Environment Variables](#environment-variables)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [Writing](#writing)
- [License](#license)

---

## What It Does

**Core audit** (runs on a URL list):

- Visits each URL in a real Chromium browser — not a headless scraper
- Extracts title, meta description, H1s, and canonical tag via Claude API
- Checks for broken same-domain links asynchronously using httpx
- Detects edge cases (404s, login walls, redirects) and pauses for human input
- Writes results to `report.json` incrementally — safe to interrupt and resume
- Generates a plain-English `report-summary.txt` on completion

**Standalone modules** (run independently on any data):

- `qualify-backlinks` — score a list of referring domains for niche relevance and traffic quality
- `gsc-insights` — parse a Search Console export and find quick wins and cannibalisation
- `relevance-score` — score candidate pages as internal link sources for a target URL
- `cluster-audit` — map your full site into topic clusters, find orphans and missing hubs

---

## Stack

- [Browser Use](https://github.com/browser-use/browser-use) — real browser navigation via Playwright
- [Anthropic Claude API](https://console.anthropic.com) — structured SEO signal extraction (Haiku for modules, Sonnet for core)
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

Set your Anthropic API key:

```bash
# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-..."

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

Or add it to a `.env` file in the project root — the agent loads it automatically:

```
ANTHROPIC_API_KEY=sk-ant-...
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

### Core audit

Interactive mode — pauses on edge cases (login walls, 404s):

```bash
python main.py
```

Auto mode — skips edge cases, logs them to `needs_human[]` in state, continues:

```bash
python main.py --auto
```

Resume after interruption — already-audited URLs are skipped automatically:

```bash
python main.py --auto
# Starting audit: 4 pending, 3 already done.
```

With project isolation (each client gets separate input, state, and reports):

```bash
python main.py --project acme --auto
python main.py --project globex --auto
```

With cost-curve tiered routing (cheaper checks first):

```bash
python main.py --tiered --auto
```

With AI rewrite suggestions:

```bash
python main.py --rewrite --auto
python main.py --rewrite --voice-sample my-writing-sample.txt --auto
```

### Standalone modules

**Backlink qualifier** — score a `.txt` or `.csv` list of referring domain URLs:

```bash
python main.py qualify-backlinks backlinks.txt --niche "AI agents python"
python main.py qualify-backlinks backlinks.txt --niche "SEO tools" --project acme
```

**GSC insights** — parse a Search Console query export:

```bash
python main.py gsc-insights gsc-export.csv
python main.py gsc-insights gsc-export.csv --min-impressions 100 --project acme
```

**Relevance scorer** — score candidate pages as internal link sources:

```bash
python main.py relevance-score --target https://example.com/target-page --pages pages.txt
python main.py relevance-score --target https://example.com/target --pages pages.txt --project acme
```

**Cluster audit** — map site pages into topic clusters:

```bash
python main.py cluster-audit --pages pages.txt
python main.py cluster-audit --pages pages.txt --project acme
```

---

## Modules

All modules use Claude Haiku. Prompts are in `prompts/`. Results write to markdown files in the project directory or repo root.

### Backlink Qualifier

Scores each referring domain on three axes:

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| Niche relevance | 50% | Topical alignment with your target niche (0–100) |
| Traffic quality | 30% | Estimated real traffic vs. spam traffic (0–100) |
| Spam score | 20% (inverted) | Link farm signals, thin content, private blog network patterns (0–100) |

**Composite score** = `niche * 0.50 + traffic * 0.30 - spam * 0.20`

Tiers: Insert Worthy (≥80), Useful (60–79), Neutral (40–59), Borderline (20–39), Avoid (<20 or spam >70).

Fetches pages via real browser. Caches results to flat JSON — crash at URL 47, restart at URL 48.

### GSC Insights

Parses a Search Console query export CSV. Flags quick wins: position 4–20, impressions ≥50 (configurable), CTR <5%. Sends the top 50 rows to Haiku with a prompt that specifically asks for queries where two pages compete — the cannibalisation signal you can't see by sorting a spreadsheet.

### Relevance Scorer

Scores candidate pages as sources for internal links pointing at a target URL:

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| Topical alignment | 40% | How closely the source page's topic matches the target |
| Anchor opportunity | 35% | Whether the source page naturally mentions the target's topic |
| Link equity | 25% | Estimated page strength as a link source |

Checks existing links deterministically before scoring — never recommends a link that already exists.

Tiers: Strong Link (≥75), Good Opportunity (55–74), Possible (35–54), Skip (<35).

### Cluster Audit

Builds the full internal link graph from a page list. Counts incoming links per page — zero incoming means orphan. Sends the complete graph to Haiku: cluster mapping, missing hub detection, cross-cluster link suggestions, and a prioritised fix list.

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

Module outputs write to markdown files: `backlink-report.md`, `gsc-insights-report.md`, `relevance-report.md`, `cluster-report.md`.

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

The default audit routes every URL through Claude Sonnet. `--tiered` routes cheaper checks first.

| Tier | What runs | Approximate cost per URL |
|------|-----------|--------------------------|
| Tier 1 | Deterministic Python checks (title length, H1 count, link parsing) | $0 |
| Tier 2 | Claude Haiku — meta description suggestion | ~$0.0001 |
| Tier 3 | Claude Sonnet — full extraction + opening paragraph rewrite | ~$0.006 |

Without `--tiered`, every URL uses Tier 3. With `--tiered`, Tier 1 runs first and escalates only when needed. A 20-URL audit at Tier 3 costs about $0.12.

Module runs (backlink qualifier, GSC insights, etc.) all use Haiku — cost is negligible for typical site sizes.

---

## Scheduling

For weekly audits, schedule a batch file or cron job.

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

## Environment Variables

| Variable | Required for | Notes |
|----------|-------------|-------|
| `ANTHROPIC_API_KEY` | Everything | Claude API access |
| `PAGESPEED_API_KEY` | `--pagespeed` | Free at [console.cloud.google.com](https://console.cloud.google.com) |
| `SMTP_HOST` | `--email` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `--email` | e.g. `587` |
| `SMTP_USER` | `--email` | Your email address |
| `SMTP_PASSWORD` | `--email` | App password recommended |
| `SMTP_FROM` | `--email` | Sender address |

---

## Architecture

```
seo-agent/
├── main.py               # Entry point — core audit + module sub-commands
├── config.py             # SMTP and PageSpeed config helpers
├── input.csv             # Default URL list
├── requirements.txt
├── .gitignore
│
├── core/                 # Audit engine
│   ├── browser.py        # Playwright browser driver
│   ├── extractor.py      # Claude API extraction layer
│   ├── linkchecker.py    # Async broken link checker
│   ├── hitl.py           # Human-in-the-loop pause logic
│   ├── reporter.py       # Report writer (JSON + summary)
│   ├── state.py          # State persistence + run history
│   └── index.py          # Standalone core entry point
│
├── modules/              # Standalone analysis modules
│   ├── backlink_qualifier.py
│   ├── cluster_audit.py
│   ├── gsc_insights.py
│   └── relevance_scorer.py
│
├── prompts/              # Haiku prompt templates for each module
│   ├── backlink_qualifier.md
│   ├── cluster_audit.md
│   ├── gsc_insights.md
│   └── relevance_scorer.md
│
└── premium/              # Optional paid features (PDF reports, email delivery)
    ├── cost_curve.py
    ├── enhanced_reporter.py
    ├── rewrite_agent.py
    ├── pagespeed.py
    ├── structured_data.py
    └── email_reporter.py
```

---

## Contributing

Pull requests are welcome. The full codebase — core audit engine, all four modules, all prompts — is open source.

To contribute:

```bash
git clone https://github.com/dannwaneri/seo-agent
cd seo-agent
pip install -r requirements.txt
playwright install chromium
# make your changes
# run the inline acceptance tests
python main.py  # runs __main__ test block
```

---

## Writing

Articles about building and running this agent:

- [How to Build a Local SEO Audit Agent with Browser Use and Claude API](https://www.freecodecamp.org/news/how-to-build-a-local-seo-audit-agent-with-browser-use-and-claude-api/) — freeCodeCamp
- [I Ran My Own SEO Agent on My Two Domains — It Went from 0.4 to 44 in One Afternoon](https://dev.to/dannwaneri/i-ran-my-own-seo-agent-on-my-two-domains-it-went-from-04-to-44-pass-in-one-afternoon-39an) — dev.to
- [I Was Paying $0.006 per URL for SEO Audits Until I Realized Most Needed $0](https://dev.to/dannwaneri/i-was-paying-0006-per-url-for-seo-audits-until-i-realized-most-needed-0-132j) — dev.to
- [How to Build a Cost-Efficient AI Agent with Tiered Model Routing](https://www.freecodecamp.org/news/how-to-build-a-cost-efficient-ai-agent-with-tiered-model-routing/) — freeCodeCamp
- [I Built a Local AI Agent That Audits My Own Articles — It Flagged Every Single One](https://dev.to/dannwaneri/i-built-a-local-ai-agent-that-audits-my-own-articles-it-flagged-every-single-one-pkh) — dev.to
- [I Gave My SEO Agent a Real Site. It Found Bugs I'd Missed for Weeks.](https://dannwaneri.com) — the co-pilot build with all 4 modules

---

## Author

Daniel Nwaneri — [dannwaneri.com](https://dannwaneri.com) · [DEV.to](https://dev.to/dannwaneri) · [GitHub](https://github.com/dannwaneri)

---

## License

MIT. See [LICENSE](LICENSE).
