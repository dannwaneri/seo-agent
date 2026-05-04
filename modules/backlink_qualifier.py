"""
Backlink Opportunity Qualifier

Scores a list of URLs for backlink worthiness using:
  - Real-browser fetch via core.browser
  - Claude Haiku scoring (niche_relevance, traffic_quality, spam_score)
  - Deterministic overall_score computed in Python

Tiers:
  Insert Worthy  >= 80
  Good           >= 60
  Review         >= 40
  Avoid          <  40

Usage:
  python main.py qualify-backlinks urls.txt --niche "AI agents python"
  python main.py qualify-backlinks urls.csv --niche "SEO automation"
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

_WEIGHTS = {
    "niche_relevance": 0.50,
    "traffic_quality": 0.30,
    "spam_penalty":    0.20,
}

_TIERS = [
    (80, "Insert Worthy"),
    (60, "Good"),
    (40, "Review"),
    (0,  "Avoid"),
]


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    here = Path(__file__).parent.parent
    prompt_file = here / "prompts" / "backlink_qualifier.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Prompt file not found: {prompt_file}")


# ---------------------------------------------------------------------------
# URL loading
# ---------------------------------------------------------------------------

def load_urls(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    urls = []
    if p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            url_col = next(
                (h for h in headers if h.lower() in ("url", "urls", "link", "links")),
                headers[0] if headers else None,
            )
            if url_col:
                for row in reader:
                    u = row.get(url_col, "").strip()
                    if u:
                        urls.append(u)
            else:
                f.seek(0)
                for row in csv.reader(f):
                    if row and row[0].strip():
                        urls.append(row[0].strip())
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if u and not u.startswith("#"):
                urls.append(u)

    return urls


# ---------------------------------------------------------------------------
# Browser fetch
# ---------------------------------------------------------------------------

def _fetch_snapshot(url: str) -> dict:
    try:
        repo_root = str(Path(__file__).parent.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from core.browser import fetch_page
        return fetch_page(url)
    except Exception as exc:
        return {
            "url": url,
            "final_url": url,
            "status_code": None,
            "title": None,
            "meta_description": None,
            "h1s": [],
            "raw_text": "",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Claude Haiku scoring
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_user_prompt(snapshot: dict, niche: str) -> str:
    title = snapshot.get("title") or "N/A"
    description = snapshot.get("meta_description") or "N/A"
    h1s = snapshot.get("h1s") or []
    raw_text = (snapshot.get("raw_text") or "")[:1500]
    return (
        f"Target niche: {niche}\n\n"
        f"URL: {snapshot.get('final_url') or snapshot.get('url')}\n"
        f"Title: {title}\n"
        f"Meta description: {description}\n"
        f"H1 tags: {h1s}\n"
        f"Page text excerpt:\n{raw_text}\n"
    )


def _haiku_score(snapshot: dict, niche: str, system_prompt: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Export it before running."
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(snapshot, niche)

    try:
        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=512,
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
# Overall score computation (deterministic)
# ---------------------------------------------------------------------------

def _compute_overall(scores: dict) -> float:
    nr = float(scores.get("niche_relevance", 0))
    tq = float(scores.get("traffic_quality", 0))
    sp = float(scores.get("spam_score", 0))
    overall = (
        nr * _WEIGHTS["niche_relevance"]
        + tq * _WEIGHTS["traffic_quality"]
        - sp * _WEIGHTS["spam_penalty"]
    )
    return round(max(0.0, min(100.0, overall)), 1)


def _tier_label(overall: float) -> str:
    for threshold, label in _TIERS:
        if overall >= threshold:
            return label
    return "Avoid"


# ---------------------------------------------------------------------------
# State (resumable)
# ---------------------------------------------------------------------------

def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(results: list[dict], report_path: Path, niche: str) -> None:
    lines = [
        "# Backlink Opportunity Report",
        "",
        f"**Niche:** {niche}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Total URLs scored:** {len(results)}",
        "",
    ]

    tier_groups: dict[str, list[dict]] = {
        "Insert Worthy": [],
        "Good": [],
        "Review": [],
        "Avoid": [],
    }
    for r in results:
        tier_groups.setdefault(r.get("tier", "Avoid"), []).append(r)

    for tier in ("Insert Worthy", "Good", "Review", "Avoid"):
        group = tier_groups[tier]
        if not group:
            continue
        lines.append(f"## {tier} ({len(group)})")
        lines.append("")
        lines.append("| URL | Overall | Niche Rel | Traffic Q | Spam |")
        lines.append("|-----|---------|-----------|-----------|------|")
        for r in sorted(group, key=lambda x: x.get("overall_score") or 0, reverse=True):
            sc = r.get("scores", {})
            lines.append(
                f"| {r['url']} "
                f"| {r.get('overall_score', 'ERR')} "
                f"| {sc.get('niche_relevance', 'ERR')} "
                f"| {sc.get('traffic_quality', 'ERR')} "
                f"| {sc.get('spam_score', 'ERR')} |"
            )
        lines.append("")

        if tier == "Insert Worthy":
            lines.append("### Reasoning")
            for r in sorted(group, key=lambda x: x.get("overall_score") or 0, reverse=True):
                reas = r.get("scores", {}).get("reasoning", {})
                lines.append(f"**{r['url']}**")
                if reas:
                    lines.append(f"- Niche: {reas.get('niche_relevance', '')}")
                    lines.append(f"- Traffic: {reas.get('traffic_quality', '')}")
                    lines.append(f"- Spam: {reas.get('spam_score', '')}")
                lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    input_file: str,
    niche: str,
    project_dir: str | None = None,
) -> list[dict]:
    """
    Score all URLs in input_file against niche.

    Returns list of result dicts. Also writes backlink-state.json and
    backlink-report.md to project_dir (defaults to same dir as input_file).
    """
    urls = load_urls(input_file)
    if not urls:
        print("[backlink] No URLs found in input file.")
        return []

    out_dir = Path(project_dir) if project_dir else Path(input_file).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / "backlink-state.json"
    report_path = out_dir / "backlink-report.md"

    state = _load_state(state_path)
    system_prompt = _load_system_prompt()

    results = []
    total = len(urls)

    for i, url in enumerate(urls, 1):
        if url in state:
            print(f"[{i}/{total}] SKIP (cached): {url}")
            results.append(state[url])
            continue

        print(f"[{i}/{total}] Fetching: {url}")
        snapshot = _fetch_snapshot(url)

        if snapshot.get("error"):
            result = {
                "url": url,
                "overall_score": None,
                "tier": "Avoid",
                "scores": {},
                "error": snapshot["error"],
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
            state[url] = result
            _save_state(state_path, state)
            results.append(result)
            print(f"  ERROR: {snapshot['error']}")
            continue

        print(f"  Scoring with Haiku...")
        scores = _haiku_score(snapshot, niche, system_prompt)

        if scores.get("error"):
            result = {
                "url": url,
                "overall_score": None,
                "tier": "Avoid",
                "scores": {},
                "error": scores["error"],
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            overall = _compute_overall(scores)
            tier = _tier_label(overall)
            result = {
                "url": url,
                "overall_score": overall,
                "tier": tier,
                "scores": scores,
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
            print(f"  -> {tier} (overall: {overall})")

        state[url] = result
        _save_state(state_path, state)
        results.append(result)

    _write_report(results, report_path, niche)
    print(f"\n[backlink] Done. Report: {report_path}")

    counts = {t: len([r for r in results if r.get("tier") == t])
              for t in ("Insert Worthy", "Good", "Review", "Avoid")}
    print(
        f"[backlink] Insert Worthy: {counts['Insert Worthy']}  "
        f"Good: {counts['Good']}  "
        f"Review: {counts['Review']}  "
        f"Avoid: {counts['Avoid']}"
    )

    return results


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    _failures = []

    def _chk(cond, msg):
        if not cond:
            raise AssertionError(msg)

    # -- Test 1: load_urls from txt -------------------------------------------
    def test_load_txt():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("https://example.com\n# comment\nhttps://another.com\n")
            fname = f.name
        try:
            urls = load_urls(fname)
            _chk(
                urls == ["https://example.com", "https://another.com"],
                f"Expected 2 URLs, got {urls}",
            )
            print("Test 1 PASS: load_urls reads txt, skips comments")
        finally:
            os.unlink(fname)

    # -- Test 2: load_urls from csv -------------------------------------------
    def test_load_csv():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["url", "notes"])
            writer.writerow(["https://example.com", "first"])
            writer.writerow(["https://another.com", "second"])
            fname = f.name
        try:
            urls = load_urls(fname)
            _chk(
                urls == ["https://example.com", "https://another.com"],
                f"Expected 2 URLs from CSV, got {urls}",
            )
            print("Test 2 PASS: load_urls reads CSV with url column")
        finally:
            os.unlink(fname)

    # -- Test 3: _compute_overall determinism ---------------------------------
    def test_compute_overall():
        scores = {"niche_relevance": 80, "traffic_quality": 70, "spam_score": 10}
        # 80*0.5 + 70*0.3 - 10*0.2 = 40 + 21 - 2 = 59
        overall = _compute_overall(scores)
        _chk(overall == 59.0, f"Expected 59.0, got {overall}")
        print("Test 3 PASS: _compute_overall is deterministic")

    # -- Test 4: _tier_label boundaries ---------------------------------------
    def test_tier_labels():
        _chk(_tier_label(80) == "Insert Worthy", "80 should be Insert Worthy")
        _chk(_tier_label(60) == "Good", "60 should be Good")
        _chk(_tier_label(40) == "Review", "40 should be Review")
        _chk(_tier_label(39) == "Avoid", "39 should be Avoid")
        _chk(_tier_label(0)  == "Avoid", "0 should be Avoid")
        print("Test 4 PASS: tier boundaries are correct")

    # -- Test 5: _strip_fences removes json fences ----------------------------
    def test_strip_fences():
        raw = '```json\n{"a":1}\n```'
        _chk(_strip_fences(raw) == '{"a":1}', "Fences not stripped")
        print("Test 5 PASS: _strip_fences removes json code fences")

    for test_fn in [
        test_load_txt,
        test_load_csv,
        test_compute_overall,
        test_tier_labels,
        test_strip_fences,
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
