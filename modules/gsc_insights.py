"""
GSC Insights Module

Analyses Google Search Console query data (exported CSV) and returns:
  - Quick wins: positions 4-20 with high impressions, low CTR
  - Cannibalisation risks: multiple URLs competing for same query
  - Cluster gaps: topic clusters implied by data but missing a hub page

Input CSV must have columns (case-insensitive):
  query, clicks, impressions, ctr, position
  (standard GSC export format)

Usage:
  python main.py gsc-insights <file> [--project NAME] [--min-impressions N]
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MIN_IMPRESSIONS = 50
_QUICK_WIN_MIN_POS = 4
_QUICK_WIN_MAX_POS = 20
_QUICK_WIN_MAX_CTR = 0.05


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    here = Path(__file__).parent.parent
    prompt_file = here / "prompts" / "gsc_insights.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Prompt file not found: {prompt_file}")


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

_COL_ALIASES = {
    "query":       ("query", "queries", "keyword", "keywords", "search term", "search query", "top queries"),
    "clicks":      ("clicks", "click"),
    "impressions": ("impressions", "impression"),
    "ctr":         ("ctr", "click through rate", "click-through rate"),
    "position":    ("position", "avg. position", "average position", "avg position", "rank"),
}


def _resolve_col(headers: list[str], field: str) -> str | None:
    aliases = _COL_ALIASES.get(field, (field,))
    for h in headers:
        if h.lower().strip() in aliases:
            return h
    return None


def load_gsc_csv(path: str, min_impressions: int = _DEFAULT_MIN_IMPRESSIONS) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"GSC file not found: {path}")

    rows = []
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])

        col_query       = _resolve_col(headers, "query")
        col_clicks      = _resolve_col(headers, "clicks")
        col_impressions = _resolve_col(headers, "impressions")
        col_ctr         = _resolve_col(headers, "ctr")
        col_position    = _resolve_col(headers, "position")

        if not col_query:
            raise ValueError(
                f"Could not find a 'query' column in {path}. "
                f"Found columns: {headers}"
            )

        for row in reader:
            query = row.get(col_query, "").strip()
            if not query:
                continue

            def _float(col):
                if not col:
                    return 0.0
                val = row.get(col, "0").strip().rstrip("%")
                try:
                    return float(val)
                except ValueError:
                    return 0.0

            impressions = _float(col_impressions)
            if impressions < min_impressions:
                continue

            ctr_raw = _float(col_ctr)
            ctr = ctr_raw / 100 if ctr_raw > 1 else ctr_raw

            rows.append({
                "query":       query,
                "clicks":      int(_float(col_clicks)),
                "impressions": int(impressions),
                "ctr":         round(ctr, 4),
                "position":    round(_float(col_position), 1),
            })

    return rows


# ---------------------------------------------------------------------------
# Pre-filter: flag quick wins deterministically before sending to Claude
# ---------------------------------------------------------------------------

def _flag_quick_wins(rows: list[dict]) -> list[dict]:
    return [
        r for r in rows
        if _QUICK_WIN_MIN_POS <= r["position"] <= _QUICK_WIN_MAX_POS
        and r["ctr"] < _QUICK_WIN_MAX_CTR
        and r["impressions"] >= _DEFAULT_MIN_IMPRESSIONS
    ]


# ---------------------------------------------------------------------------
# Claude Haiku analysis
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_user_prompt(rows: list[dict], quick_wins: list[dict]) -> str:
    top_rows = sorted(rows, key=lambda x: x["impressions"], reverse=True)[:50]
    rows_text = "\n".join(
        f"  {r['query']} | impressions:{r['impressions']} clicks:{r['clicks']} "
        f"ctr:{r['ctr']:.1%} pos:{r['position']}"
        for r in top_rows
    )
    qw_text = "\n".join(
        f"  {r['query']} | pos:{r['position']} impressions:{r['impressions']} ctr:{r['ctr']:.1%}"
        for r in quick_wins[:20]
    ) or "  (none detected)"

    return (
        f"GSC query data ({len(rows)} rows total, showing top {len(top_rows)} by impressions):\n"
        f"{rows_text}\n\n"
        f"Pre-flagged quick wins (pos 4-20, impressions >= {_DEFAULT_MIN_IMPRESSIONS}, CTR < 5%):\n"
        f"{qw_text}\n"
    )


def _haiku_analyse(rows: list[dict], quick_wins: list[dict], system_prompt: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Export it before running."
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(rows, quick_wins)

    try:
        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as exc:
        return {"error": f"Haiku API error: {exc}"}

    raw = message.content[0].text
    cleaned = _strip_fences(raw)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return {"error": f"JSON parse error: {exc}", "raw": raw}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(insights: dict, report_path: Path, source_file: str, row_count: int) -> None:
    lines = [
        "# GSC Insights Report",
        "",
        f"**Source:** {source_file}  ",
        f"**Queries analysed:** {row_count}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    summary = insights.get("summary", "")
    if summary:
        lines += ["## Summary", "", summary, ""]

    quick_wins = insights.get("quick_wins", [])
    if quick_wins:
        lines += [f"## Quick Wins ({len(quick_wins)})", ""]
        lines += ["| Query | Position | Impressions | CTR | Action |"]
        lines += ["|-------|----------|-------------|-----|--------|"]
        for w in quick_wins:
            lines.append(
                f"| {w.get('query','')} "
                f"| {w.get('current_position','')} "
                f"| {w.get('impressions','')} "
                f"| {w.get('ctr','')} "
                f"| {w.get('recommendation','')} |"
            )
        lines.append("")
        lines += ["### Expected Impact", ""]
        for w in quick_wins:
            impact = w.get("expected_impact", "")
            if impact:
                lines.append(f"- **{w.get('query','')}**: {impact}")
        lines.append("")

    risks = insights.get("cannibalisation_risks", [])
    if risks:
        lines += [f"## Cannibalisation Risks ({len(risks)})", ""]
        for r in risks:
            lines.append(f"**Query:** {r.get('query','')}")
            urls = r.get("affected_urls", [])
            if urls:
                lines.append(f"**URLs:** {', '.join(urls)}")
            lines.append(f"**Fix:** {r.get('recommendation','')}")
            lines.append("")

    gaps = insights.get("cluster_gaps", [])
    if gaps:
        lines += [f"## Cluster Gaps ({len(gaps)})", ""]
        for g in gaps:
            lines.append(f"**Topic:** {g.get('topic','')}")
            lines.append(f"**Evidence:** {g.get('evidence','')}")
            lines.append(f"**Create:** {g.get('recommended_content','')}")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    gsc_file: str,
    project_dir: str | None = None,
    min_impressions: int = _DEFAULT_MIN_IMPRESSIONS,
) -> dict:
    """
    Analyse a GSC export CSV and write insights report.

    Returns the insights dict. Writes gsc-insights.md to project_dir
    (defaults to same dir as gsc_file).
    """
    print(f"[gsc] Loading: {gsc_file}")
    rows = load_gsc_csv(gsc_file, min_impressions=min_impressions)

    if not rows:
        print(f"[gsc] No rows found with impressions >= {min_impressions}. "
              "Lower --min-impressions or check the CSV format.")
        return {}

    print(f"[gsc] {len(rows)} queries loaded (min impressions: {min_impressions})")

    quick_wins = _flag_quick_wins(rows)
    print(f"[gsc] {len(quick_wins)} quick win candidates (pos 4-20, CTR < 5%)")

    out_dir = Path(project_dir) if project_dir else Path(gsc_file).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "gsc-insights.md"

    system_prompt = _load_system_prompt()

    print("[gsc] Analysing with Haiku...")
    insights = _haiku_analyse(rows, quick_wins, system_prompt)

    if insights.get("error"):
        print(f"[gsc] ERROR: {insights['error']}", file=sys.stderr)
        return insights

    _write_report(insights, report_path, gsc_file, len(rows))
    print(f"[gsc] Done. Report: {report_path}")

    qw = len(insights.get("quick_wins", []))
    risks = len(insights.get("cannibalisation_risks", []))
    gaps = len(insights.get("cluster_gaps", []))
    print(f"[gsc] Quick wins: {qw}  Cannibalisation risks: {risks}  Cluster gaps: {gaps}")

    return insights


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    _failures = []

    def _chk(cond, msg):
        if not cond:
            raise AssertionError(msg)

    # -- Test 1: load_gsc_csv standard GSC export format ----------------------
    def test_load_standard_format():
        data = (
            "query,clicks,impressions,ctr,position\n"
            "seo agent python,50,500,0.10,3.2\n"
            "ai seo tool,5,300,0.017,8.5\n"
            "free seo audit,2,80,0.025,14.1\n"
            "tiny query,1,10,0.10,5.0\n"  # below min_impressions=50
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(data)
            fname = f.name
        try:
            rows = load_gsc_csv(fname, min_impressions=50)
            _chk(len(rows) == 3, f"Expected 3 rows (1 filtered), got {len(rows)}")
            _chk(rows[0]["query"] == "seo agent python", f"Wrong first query: {rows[0]}")
            _chk(rows[1]["ctr"] == 0.017, f"CTR already decimal, should stay: {rows[1]['ctr']}")
            print("Test 1 PASS: load_gsc_csv reads standard format, filters by impressions")
        finally:
            os.unlink(fname)

    # -- Test 2: load_gsc_csv handles % CTR values ----------------------------
    def test_load_percent_ctr():
        data = "query,clicks,impressions,ctr,position\nseo tool,10,200,5.00%,12.0\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(data)
            fname = f.name
        try:
            rows = load_gsc_csv(fname, min_impressions=50)
            _chk(len(rows) == 1, f"Expected 1 row, got {len(rows)}")
            _chk(rows[0]["ctr"] == 0.05, f"Expected 0.05, got {rows[0]['ctr']}")
            print("Test 2 PASS: load_gsc_csv converts percentage CTR to decimal")
        finally:
            os.unlink(fname)

    # -- Test 3: _flag_quick_wins filters correctly ---------------------------
    def test_flag_quick_wins():
        rows = [
            {"query": "win",     "impressions": 200, "ctr": 0.02, "position": 8.0},
            {"query": "too low", "impressions": 200, "ctr": 0.02, "position": 2.0},
            {"query": "too high","impressions": 200, "ctr": 0.02, "position": 25.0},
            {"query": "high ctr","impressions": 200, "ctr": 0.10, "position": 8.0},
            {"query": "low imp", "impressions": 30,  "ctr": 0.02, "position": 8.0},
        ]
        wins = _flag_quick_wins(rows)
        _chk(len(wins) == 1 and wins[0]["query"] == "win",
             f"Expected only 'win', got {[r['query'] for r in wins]}")
        print("Test 3 PASS: _flag_quick_wins applies position/CTR/impressions filters correctly")

    # -- Test 4: _strip_fences -------------------------------------------------
    def test_strip_fences():
        raw = '```json\n{"quick_wins":[]}\n```'
        _chk(_strip_fences(raw) == '{"quick_wins":[]}', "Fences not stripped")
        print("Test 4 PASS: _strip_fences removes json fences")

    # -- Test 5: load_gsc_csv raises on missing query column ------------------
    def test_missing_query_col():
        data = "keyword,clicks,impressions,ctr,position\nseo,10,200,5%,8\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(data)
            fname = f.name
        try:
            # "keyword" is an alias for "query" in _COL_ALIASES
            rows = load_gsc_csv(fname, min_impressions=50)
            _chk(len(rows) == 1, f"Expected 1 row via 'keyword' alias, got {len(rows)}")
            print("Test 5 PASS: load_gsc_csv resolves 'keyword' column alias")
        finally:
            os.unlink(fname)

    for test_fn in [
        test_load_standard_format,
        test_load_percent_ctr,
        test_flag_quick_wins,
        test_strip_fences,
        test_missing_query_col,
    ]:
        try:
            test_fn()
        except Exception as exc:
            print(f"FAIL {test_fn.__name__}: {exc}")
            _failures.append(test_fn.__name__)

    print()
    if _failures:
        print(f"{len(_failures)} test(s) failed: {_failures}")
        sys.exit(1)
    else:
        print("All 5 acceptance tests passed.")
