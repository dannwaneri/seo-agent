"""
Relevance Scorer

Given a target URL and a list of candidate URLs from the same site, scores
each candidate as an internal linking opportunity for the target page.

Scores (all via Claude Haiku):
  topical_alignment  — how well the candidate's topic supports the target
  anchor_opportunity — how naturally a link can be placed
  link_equity        — how much ranking value the link would pass

Overall score (deterministic):
  topical_alignment * 0.50 + anchor_opportunity * 0.30 + link_equity * 0.20

Tiers:
  Strong Link   >= 75
  Good Link     >= 55
  Weak Link     >= 35
  Skip          <  35

Usage:
  python main.py relevance-score --target URL --pages urls.txt [--project NAME]
  python main.py relevance-score --target URL --pages urls.csv [--project NAME]
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
    "topical_alignment":  0.50,
    "anchor_opportunity": 0.30,
    "link_equity":        0.20,
}

_TIERS = [
    (75, "Strong Link"),
    (55, "Good Link"),
    (35, "Weak Link"),
    (0,  "Skip"),
]


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    here = Path(__file__).parent.parent
    prompt_file = here / "prompts" / "relevance_scorer.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Prompt file not found: {prompt_file}")


# ---------------------------------------------------------------------------
# URL loading (reuses same pattern as backlink_qualifier)
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
# Scoring helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _snapshot_summary(snapshot: dict, max_text: int = 800) -> str:
    title = snapshot.get("title") or "N/A"
    description = snapshot.get("meta_description") or "N/A"
    h1s = snapshot.get("h1s") or []
    raw_text = (snapshot.get("raw_text") or "")[:max_text]
    return (
        f"URL: {snapshot.get('final_url') or snapshot.get('url')}\n"
        f"Title: {title}\n"
        f"Meta description: {description}\n"
        f"H1: {h1s}\n"
        f"Content excerpt:\n{raw_text}"
    )


def _build_user_prompt(target_snapshot: dict, candidate_snapshot: dict) -> str:
    return (
        f"TARGET PAGE (the page you want to rank higher):\n"
        f"{_snapshot_summary(target_snapshot)}\n\n"
        f"CANDIDATE PAGE (potential internal link source):\n"
        f"{_snapshot_summary(candidate_snapshot)}\n"
    )


def _haiku_score(
    target_snapshot: dict,
    candidate_snapshot: dict,
    system_prompt: str,
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(target_snapshot, candidate_snapshot)

    try:
        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as exc:
        return {"error": f"Haiku API error: {exc}"}

    try:
        from core.attestation_setup import record as _record_fingerprint
        _record_fingerprint(message, module_name="relevance_scorer", model_fallback=_HAIKU_MODEL)
    except Exception:
        pass  # instrumentation can never break the agent

    raw = message.content[0].text
    cleaned = _strip_fences(raw)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return {"error": f"JSON parse error: {exc}", "raw": raw}


# ---------------------------------------------------------------------------
# Overall score (deterministic)
# ---------------------------------------------------------------------------

def _compute_overall(scores: dict) -> float:
    ta = float(scores.get("topical_alignment", 0))
    ao = float(scores.get("anchor_opportunity", 0))
    le = float(scores.get("link_equity", 0))
    overall = (
        ta * _WEIGHTS["topical_alignment"]
        + ao * _WEIGHTS["anchor_opportunity"]
        + le * _WEIGHTS["link_equity"]
    )
    return round(max(0.0, min(100.0, overall)), 1)


def _tier_label(overall: float) -> str:
    for threshold, label in _TIERS:
        if overall >= threshold:
            return label
    return "Skip"


# ---------------------------------------------------------------------------
# Already-linked detection (deterministic)
# ---------------------------------------------------------------------------

def _already_links_to(candidate_snapshot: dict, target_url: str) -> bool:
    raw_links = candidate_snapshot.get("raw_links") or []
    target_path = target_url.rstrip("/").split("//")[-1].split("/", 1)[-1] if "//" in target_url else target_url
    for link in raw_links:
        if target_url in link or (target_path and target_path in link):
            return True
    return False


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
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(
    results: list[dict],
    report_path: Path,
    target_url: str,
    target_title: str,
) -> None:
    lines = [
        "# Internal Link Relevance Report",
        "",
        f"**Target page:** {target_url}  ",
        f"**Target title:** {target_title}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Candidates scored:** {len(results)}",
        "",
    ]

    tier_groups: dict[str, list[dict]] = {
        "Strong Link": [],
        "Good Link": [],
        "Weak Link": [],
        "Skip": [],
    }
    for r in results:
        tier_groups.setdefault(r.get("tier", "Skip"), []).append(r)

    for tier in ("Strong Link", "Good Link", "Weak Link", "Skip"):
        group = tier_groups[tier]
        if not group:
            continue
        lines.append(f"## {tier} ({len(group)})")
        lines.append("")
        lines.append("| URL | Overall | Topical | Anchor | Equity | Already Linked |")
        lines.append("|-----|---------|---------|--------|--------|----------------|")
        for r in sorted(group, key=lambda x: x.get("overall_score") or 0, reverse=True):
            sc = r.get("scores", {})
            linked = "Yes" if r.get("already_linked") else "No"
            lines.append(
                f"| {r['candidate_url']} "
                f"| {r.get('overall_score', 'ERR')} "
                f"| {sc.get('topical_alignment', 'ERR')} "
                f"| {sc.get('anchor_opportunity', 'ERR')} "
                f"| {sc.get('link_equity', 'ERR')} "
                f"| {linked} |"
            )
        lines.append("")

        if tier in ("Strong Link", "Good Link"):
            lines.append("### Link Placement Suggestions")
            lines.append("")
            for r in sorted(group, key=lambda x: x.get("overall_score") or 0, reverse=True):
                if r.get("already_linked"):
                    continue
                sc = r.get("scores", {})
                anchor = sc.get("suggested_anchor", "")
                context = sc.get("suggested_context", "")
                reas = sc.get("reasoning", {})
                lines.append(f"**{r['candidate_url']}** (overall: {r.get('overall_score')})")
                if anchor:
                    lines.append(f"- Anchor text: *\"{anchor}\"*")
                if context:
                    lines.append(f"- Placement: {context}")
                if reas:
                    lines.append(f"- Why: {reas.get('topical_alignment', '')}")
                lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    target_url: str,
    pages_file: str,
    project_dir: str | None = None,
) -> list[dict]:
    """
    Score all URLs in pages_file as internal link opportunities for target_url.

    Returns list of result dicts. Writes relevance-state.json and
    relevance-report.md to project_dir (defaults to dir of pages_file).
    """
    candidates = load_urls(pages_file)
    # Remove target from candidates if present
    candidates = [u for u in candidates if u.rstrip("/") != target_url.rstrip("/")]

    if not candidates:
        print("[relevance] No candidate URLs found.")
        return []

    out_dir = Path(project_dir) if project_dir else Path(pages_file).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / "relevance-state.json"
    report_path = out_dir / "relevance-report.md"

    state = _load_state(state_path)
    system_prompt = _load_system_prompt()

    print(f"[relevance] Target: {target_url}")
    print(f"[relevance] Fetching target page...")
    target_snapshot = _fetch_snapshot(target_url)

    if target_snapshot.get("error"):
        print(f"[relevance] ERROR fetching target: {target_snapshot['error']}")
        return []

    target_title = target_snapshot.get("title") or target_url
    cache_key = f"{target_url}||{{}}"

    results = []
    total = len(candidates)

    for i, candidate_url in enumerate(candidates, 1):
        cache_key = f"{target_url}||{candidate_url}"

        if cache_key in state:
            print(f"[{i}/{total}] SKIP (cached): {candidate_url}")
            results.append(state[cache_key])
            continue

        print(f"[{i}/{total}] Fetching: {candidate_url}")
        candidate_snapshot = _fetch_snapshot(candidate_url)

        if candidate_snapshot.get("error"):
            result = {
                "candidate_url": candidate_url,
                "target_url": target_url,
                "overall_score": None,
                "tier": "Skip",
                "scores": {},
                "already_linked": False,
                "error": candidate_snapshot["error"],
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
            state[cache_key] = result
            _save_state(state_path, state)
            results.append(result)
            print(f"  ERROR: {candidate_snapshot['error']}")
            continue

        already_linked = _already_links_to(candidate_snapshot, target_url)
        print(f"  Scoring with Haiku...")
        scores = _haiku_score(target_snapshot, candidate_snapshot, system_prompt)

        if scores.get("error"):
            result = {
                "candidate_url": candidate_url,
                "target_url": target_url,
                "overall_score": None,
                "tier": "Skip",
                "scores": {},
                "already_linked": already_linked,
                "error": scores["error"],
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            overall = _compute_overall(scores)
            tier = _tier_label(overall)
            result = {
                "candidate_url": candidate_url,
                "target_url": target_url,
                "overall_score": overall,
                "tier": tier,
                "scores": scores,
                "already_linked": already_linked,
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
            linked_note = " [already linked]" if already_linked else ""
            print(f"  -> {tier} (overall: {overall}){linked_note}")

        state[cache_key] = result
        _save_state(state_path, state)
        results.append(result)

    _write_report(results, report_path, target_url, target_title)
    print(f"\n[relevance] Done. Report: {report_path}")

    counts = {t: len([r for r in results if r.get("tier") == t])
              for t in ("Strong Link", "Good Link", "Weak Link", "Skip")}
    print(
        f"[relevance] Strong: {counts['Strong Link']}  "
        f"Good: {counts['Good Link']}  "
        f"Weak: {counts['Weak Link']}  "
        f"Skip: {counts['Skip']}"
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

    # -- Test 1: _compute_overall is deterministic ----------------------------
    def test_compute_overall():
        scores = {"topical_alignment": 80, "anchor_opportunity": 60, "link_equity": 70}
        # 80*0.5 + 60*0.3 + 70*0.2 = 40 + 18 + 14 = 72
        overall = _compute_overall(scores)
        _chk(overall == 72.0, f"Expected 72.0, got {overall}")
        print("Test 1 PASS: _compute_overall is deterministic")

    # -- Test 2: _tier_label boundaries ---------------------------------------
    def test_tier_labels():
        _chk(_tier_label(75) == "Strong Link", "75 should be Strong Link")
        _chk(_tier_label(55) == "Good Link",   "55 should be Good Link")
        _chk(_tier_label(35) == "Weak Link",   "35 should be Weak Link")
        _chk(_tier_label(34) == "Skip",        "34 should be Skip")
        _chk(_tier_label(0)  == "Skip",        "0 should be Skip")
        print("Test 2 PASS: tier boundaries are correct")

    # -- Test 3: load_urls from txt -------------------------------------------
    def test_load_txt():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("https://example.com/a\n# skip\nhttps://example.com/b\n")
            fname = f.name
        try:
            urls = load_urls(fname)
            _chk(urls == ["https://example.com/a", "https://example.com/b"],
                 f"Expected 2 URLs, got {urls}")
            print("Test 3 PASS: load_urls reads txt, skips comments")
        finally:
            os.unlink(fname)

    # -- Test 4: target URL excluded from candidates --------------------------
    def test_target_excluded():
        target = "https://example.com/target"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(f"{target}\nhttps://example.com/other\n")
            fname = f.name
        try:
            urls = load_urls(fname)
            candidates = [u for u in urls if u.rstrip("/") != target.rstrip("/")]
            _chk(
                candidates == ["https://example.com/other"],
                f"Target not excluded: {candidates}",
            )
            print("Test 4 PASS: target URL excluded from candidates")
        finally:
            os.unlink(fname)

    # -- Test 5: _already_links_to detects existing links ---------------------
    def test_already_links_to():
        snapshot_with_link = {
            "raw_links": [
                "https://example.com/target",
                "https://example.com/other",
            ]
        }
        snapshot_without = {"raw_links": ["https://example.com/other"]}
        _chk(
            _already_links_to(snapshot_with_link, "https://example.com/target"),
            "Should detect existing link",
        )
        _chk(
            not _already_links_to(snapshot_without, "https://example.com/target"),
            "Should not detect link that isn't there",
        )
        print("Test 5 PASS: _already_links_to detects existing links correctly")

    for test_fn in [
        test_compute_overall,
        test_tier_labels,
        test_load_txt,
        test_target_excluded,
        test_already_links_to,
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
