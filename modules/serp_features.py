"""
SERP Feature Detector — powered by SerpApi

Detects which Google SERP features are present for each target query:
  - AI Overview
  - Featured snippet (answer box)
  - People Also Ask
  - Image pack
  - Video results
  - Local pack
  - Knowledge panel

No browser. No CAPTCHA. One clean API call per query.

Requires a SerpApi key (free tier: 100 searches/month):
  export SERPAPI_KEY="your-key-here"

Usage:
  python main.py serp-features --query "does twitch pay nigerians" [--project NAME]
  python main.py serp-features --queries gsc-export.csv [--project NAME] [--max N]
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


_ENDPOINT = "https://serpapi.com/search.json"
_INTER_QUERY_DELAY = 1  # seconds — SerpApi handles rate limiting on their end


def _get_api_key() -> str:
    key = os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")
    if not key:
        raise EnvironmentError(
            "SERPAPI_KEY is not set.\n"
            "Export it before running:\n"
            "  export SERPAPI_KEY=your-key-here\n"
            "Or add it to your .env file."
        )
    return key


def _check_query(query: str, api_key: str) -> dict:
    result = {"query": query, "features": {}, "opportunities": [], "error": None}

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "hl": "en",
        "gl": "us",
    }

    try:
        response = httpx.get(_ENDPOINT, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get("search_metadata", {}).get("status") != "Success":
            result["error"] = f"SerpApi error: {data.get('error', 'unknown')}"
            return result

        features = {}

        # AI Overview — top-level key or inside related_questions type
        features["ai_overview"] = (
            "ai_overview" in data
            or any(
                r.get("type") == "ai_overview"
                for r in data.get("related_questions", [])
            )
        )

        # Featured snippet — answer_box present and not an AI overview variant
        answer_box = data.get("answer_box") or {}
        features["featured_snippet"] = bool(answer_box) and answer_box.get("type") not in (
            "ai_overview",
            None.__class__,
        )

        # People Also Ask
        features["people_also_ask"] = bool(data.get("related_questions"))

        # Image pack
        features["image_pack"] = bool(data.get("inline_images"))

        # Video results — inline videos or perspectives with video thumbnails
        features["video_results"] = bool(data.get("inline_videos")) or any(
            p.get("video") for p in data.get("perspectives", [])
        )

        # Local pack — map + place listings
        features["local_pack"] = bool(
            (data.get("local_results") or {}).get("places")
        )

        # Knowledge panel
        features["knowledge_panel"] = bool(data.get("knowledge_graph"))

        result["features"] = features
        result["opportunities"] = _opportunities(features)

    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except httpx.RequestError as exc:
        result["error"] = f"Request error: {exc}"
    except Exception as exc:
        result["error"] = str(exc)

    return result


def _opportunities(features: dict) -> list[str]:
    ops = []
    if features.get("ai_overview"):
        ops.append(
            "AI Overview present — be the quotable authority: direct answers, "
            "clear facts, structured headings that are easy to extract"
        )
    if features.get("featured_snippet"):
        ops.append(
            "Featured snippet — open with a concise direct answer (40–60 words) before expanding"
        )
    if features.get("people_also_ask"):
        ops.append(
            "PAA boxes present — add an FAQ section addressing the visible PAA questions directly"
        )
    if features.get("image_pack"):
        ops.append(
            "Image pack showing — add optimized images with keyword-matching alt text and file names"
        )
    if features.get("video_results"):
        ops.append(
            "Video results present — a short explainer video on this topic could rank here"
        )
    if features.get("local_pack"):
        ops.append(
            "Local pack showing — organic intent is local; weigh whether this query is worth targeting"
        )
    if features.get("knowledge_panel"):
        ops.append(
            "Knowledge panel present — entity query; structured data and authoritative content help"
        )
    if not any(features.values()):
        ops.append(
            "Clean organic SERP — no rich features competing; standard on-page optimization applies"
        )
    return ops


def _load_queries_csv(path: str, max_queries: int) -> list[str]:
    from modules.gsc_insights import load_gsc_csv

    rows = load_gsc_csv(path, min_impressions=1)
    rows.sort(key=lambda r: r["impressions"], reverse=True)
    return [r["query"] for r in rows[:max_queries]]


def _write_report(results: list[dict], report_path: Path) -> None:
    _FEATURES = [
        "ai_overview",
        "featured_snippet",
        "people_also_ask",
        "image_pack",
        "video_results",
        "local_pack",
        "knowledge_panel",
    ]
    _LABELS = {
        "ai_overview": "AI Overview",
        "featured_snippet": "Feat. Snippet",
        "people_also_ask": "PAA",
        "image_pack": "Images",
        "video_results": "Video",
        "local_pack": "Local",
        "knowledge_panel": "KP",
    }

    lines = [
        "# SERP Feature Detection Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Queries checked:** {len(results)}",
        f"**Powered by:** SerpApi",
        "",
        "## Feature Matrix",
        "",
        "| Query | " + " | ".join(_LABELS[f] for f in _FEATURES) + " |",
        "|-------|" + "|".join(["---"] * len(_FEATURES)) + "|",
    ]

    for r in results:
        if r.get("error"):
            lines.append(f"| {r['query']} | *Error: {r['error']}* |")
            continue
        cells = ["✓" if r["features"].get(f) else "—" for f in _FEATURES]
        lines.append(f"| {r['query']} | " + " | ".join(cells) + " |")

    lines += ["", "## Opportunities by Query", ""]
    for r in results:
        if r.get("error") or not r.get("opportunities"):
            continue
        lines.append(f"**{r['query']}**")
        for op in r["opportunities"]:
            lines.append(f"- {op}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def run(
    query: str | None = None,
    queries_file: str | None = None,
    project_dir: str | None = None,
    max_queries: int = 20,
) -> list[dict]:
    api_key = _get_api_key()

    if query:
        queries = [query]
    elif queries_file:
        p = Path(queries_file)
        if queries_file.endswith(".csv"):
            queries = _load_queries_csv(queries_file, max_queries)
        else:
            queries = [
                line.strip()
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ][:max_queries]
    else:
        raise ValueError("Provide --query or --queries")

    print(f"[serp] Checking {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} via SerpApi...")

    results = []
    for i, q in enumerate(queries, 1):
        print(f"[serp] [{i}/{len(queries)}] {q}")
        result = _check_query(q, api_key)
        results.append(result)

        if result.get("error"):
            print(f"[serp]   ERROR: {result['error']}")
        else:
            active = [f for f, v in result["features"].items() if v]
            print(f"[serp]   Features: {', '.join(active) if active else 'none'}")

        if i < len(queries):
            time.sleep(_INTER_QUERY_DELAY)

    out_dir = Path(project_dir) if project_dir else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "serp-features.md"
    _write_report(results, report_path)
    print(f"[serp] Done. Report: {report_path}")

    return results
