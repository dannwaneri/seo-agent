"""
LLM Visibility Checker

Checks whether your domain appears in Claude's responses to your target queries.
Tells you: is your content visible in AI-generated answers?

Claude's knowledge comes from training data. If you publish clear, authoritative
content on a topic, Claude may cite or reference your domain when answering
related questions. This module measures that visibility.

Usage:
  python main.py llm-visibility --domain dannwaneri.com --queries queries.txt [--project NAME]
  python main.py llm-visibility --domain naija-vpn.com --queries gsc-export.csv [--project NAME]
"""

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic


_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = (
    "You are a knowledgeable assistant. Answer the user's question directly and "
    "helpfully. If you know of specific websites, tools, articles, or resources "
    "that are genuinely relevant and useful for the topic, mention them by name "
    "and include their URL when you know it."
)


def _query_claude(query: str, domain: str, client: anthropic.Anthropic) -> dict:
    result = {
        "query": query,
        "mentioned": False,
        "mention_context": None,
        "response_excerpt": None,
        "error": None,
    }

    try:
        message = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        response_text = message.content[0].text
        result["response_excerpt"] = (
            response_text[:400] + "..." if len(response_text) > 400 else response_text
        )

        domain_clean = re.sub(r"https?://", "", domain).rstrip("/")
        match = re.search(
            r".{0,120}" + re.escape(domain_clean) + r".{0,120}",
            response_text,
            re.IGNORECASE,
        )
        if match:
            result["mentioned"] = True
            result["mention_context"] = f"...{match.group(0)}..."

    except anthropic.APIError as exc:
        result["error"] = f"API error: {exc}"

    return result


def _load_queries(queries_input: str, max_queries: int) -> list[str]:
    p = Path(queries_input)
    if not p.exists():
        raise FileNotFoundError(f"Queries file not found: {queries_input}")

    if queries_input.endswith(".csv"):
        from modules.gsc_insights import load_gsc_csv

        rows = load_gsc_csv(queries_input, min_impressions=1)
        rows.sort(key=lambda r: r["impressions"], reverse=True)
        return [r["query"] for r in rows[:max_queries]]

    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ][:max_queries]


def _write_report(results: list[dict], domain: str, report_path: Path) -> None:
    total = len(results)
    mentioned_results = [r for r in results if r.get("mentioned")]
    error_results = [r for r in results if r.get("error")]
    not_mentioned = [
        r for r in results if not r.get("mentioned") and not r.get("error")
    ]

    score_pct = round(len(mentioned_results) / total * 100) if total else 0

    lines = [
        "# LLM Visibility Report",
        "",
        f"**Domain:** {domain}  ",
        f"**Queries checked:** {total}  ",
        f"**Visibility score:** {len(mentioned_results)}/{total} ({score_pct}%)  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if mentioned_results:
        lines += [f"## Mentioned ({len(mentioned_results)})", ""]
        for r in mentioned_results:
            lines.append(f"**Query:** {r['query']}")
            if r.get("mention_context"):
                lines.append(f"**Context:** `{r['mention_context']}`")
            lines.append("")

    if not_mentioned:
        lines += [f"## Not Mentioned ({len(not_mentioned)})", ""]
        lines.append(
            "You rank for these queries but Claude doesn't cite you — content gaps worth addressing."
        )
        lines.append("")
        for r in not_mentioned:
            lines.append(f"- {r['query']}")
        lines.append("")

    if error_results:
        lines += ["## Errors", ""]
        for r in error_results:
            lines.append(f"- **{r['query']}**: {r['error']}")
        lines.append("")

    lines += [
        "## What To Do",
        "",
        "**If score is 0%:** Claude's training data doesn't include enough of your content yet.",
        "Publish direct-answer articles for each query, get external links, and give it time.",
        "",
        "**If score is 1–40%:** You have some presence. Focus on the 'Not Mentioned' queries —",
        "those are gaps where a new or improved article could get you cited.",
        "",
        "**If score is 40%+:** Strong LLM visibility. Protect it by keeping content up to date",
        "and expanding into adjacent queries.",
        "",
        "> Note: This checker uses Claude Haiku. Claude's knowledge has a training cutoff —",
        "> recently published content may not appear yet. Re-run quarterly.",
    ]

    report_path.write_text("\n".join(lines), encoding="utf-8")


def run(
    domain: str,
    queries_input: str,
    project_dir: str | None = None,
    max_queries: int = 20,
) -> list[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    queries = _load_queries(queries_input, max_queries)

    print(f"[llm-vis] Checking {len(queries)} queries for '{domain}'...")

    results = []
    for i, query in enumerate(queries, 1):
        print(f"[llm-vis] [{i}/{len(queries)}] {query}")
        result = _query_claude(query, domain, client)
        results.append(result)

        if result.get("error"):
            print(f"[llm-vis]   ERROR: {result['error']}")
        elif result["mentioned"]:
            print(f"[llm-vis]   MENTIONED")
        else:
            print(f"[llm-vis]   not mentioned")

        time.sleep(0.5)

    out_dir = Path(project_dir) if project_dir else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "llm-visibility.md"
    _write_report(results, domain, report_path)

    score = round(len([r for r in results if r.get("mentioned")]) / len(results) * 100) if results else 0
    print(f"[llm-vis] Done. Score: {score}%. Report: {report_path}")

    return results
