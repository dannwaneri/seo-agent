"""
Cluster Audit

Maps all pages on a site into topic clusters, identifies orphan pages,
missing hub pages, and cross-cluster link opportunities.

Process:
  1. Fetch all pages via core.browser (resumable)
  2. Build internal link graph deterministically
  3. Send page summaries to Claude Haiku for clustering
  4. Output cluster map + orphan list + missing hub suggestions

Usage:
  python main.py cluster-audit --pages urls.txt [--project NAME]
  python main.py cluster-audit --pages urls.csv [--project NAME]
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import anthropic

_HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    here = Path(__file__).parent.parent
    prompt_file = here / "prompts" / "cluster_audit.md"
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
            "raw_links": [],
            "raw_text": "",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Internal link graph (deterministic)
# ---------------------------------------------------------------------------

def _same_domain(url_a: str, url_b: str) -> bool:
    try:
        return urlparse(url_a).netloc == urlparse(url_b).netloc
    except Exception:
        return False


def _normalise(url: str) -> str:
    return url.rstrip("/").lower()


def build_link_graph(snapshots: list[dict]) -> dict[str, set[str]]:
    """
    Returns {source_url: {linked_url, ...}} for internal links only.
    Only counts links between URLs in the snapshots list.
    """
    known = {_normalise(s.get("final_url") or s["url"]) for s in snapshots}
    graph: dict[str, set[str]] = {}

    for snap in snapshots:
        src = _normalise(snap.get("final_url") or snap["url"])
        outbound: set[str] = set()
        for link in snap.get("raw_links") or []:
            norm = _normalise(link)
            if norm in known and norm != src:
                outbound.add(norm)
        graph[src] = outbound

    return graph


def _incoming_counts(graph: dict[str, set[str]]) -> dict[str, int]:
    counts: dict[str, int] = {url: 0 for url in graph}
    for src, targets in graph.items():
        for t in targets:
            if t in counts:
                counts[t] += 1
    return counts


# ---------------------------------------------------------------------------
# Claude Haiku clustering
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_user_prompt(snapshots: list[dict], incoming: dict[str, int]) -> str:
    lines = ["Pages on this site:\n"]
    for snap in snapshots:
        url = snap.get("final_url") or snap["url"]
        title = snap.get("title") or "N/A"
        desc = snap.get("meta_description") or "N/A"
        h1s = snap.get("h1s") or []
        inc = incoming.get(_normalise(url), 0)
        lines.append(
            f"URL: {url}\n"
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"H1: {h1s}\n"
            f"Incoming internal links: {inc}\n"
        )
    return "\n".join(lines)


def _haiku_cluster(snapshots: list[dict], incoming: dict[str, int], system_prompt: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(snapshots, incoming)

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
# State (resumable fetch cache)
# ---------------------------------------------------------------------------

def _load_fetch_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_fetch_cache(cache_path: Path, cache: dict) -> None:
    cache_path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(
    audit: dict,
    report_path: Path,
    graph: dict[str, set[str]],
    incoming: dict[str, int],
    total_pages: int,
) -> None:
    lines = [
        "# Cluster Audit Report",
        "",
        f"**Pages audited:** {total_pages}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    summary = audit.get("summary", "")
    if summary:
        lines += ["## Summary", "", summary, ""]

    clusters = audit.get("clusters", [])
    if clusters:
        lines += [f"## Topic Clusters ({len(clusters)})", ""]
        for c in clusters:
            hub_note = " ⚠️ hub missing" if c.get("hub_missing") else ""
            lines.append(f"### {c.get('name', 'Unnamed')}{hub_note}")
            lines.append(f"**Topic:** {c.get('topic', '')}  ")
            hub_url = c.get("hub_url")
            if hub_url:
                lines.append(f"**Hub:** {hub_url}  ")
            lines.append("")

            spokes = c.get("spokes", [])
            if spokes:
                lines.append("| URL | Role | Gap |")
                lines.append("|-----|------|-----|")
                for s in spokes:
                    gap = s.get("gap") or ""
                    lines.append(
                        f"| {s.get('url','')} | {s.get('role','')} | {gap} |"
                    )
            lines.append("")

    missing = audit.get("missing_hubs", [])
    if missing:
        lines += [f"## Missing Hub Pages ({len(missing)})", ""]
        for m in missing:
            lines.append(f"**Topic:** {m.get('topic','')}")
            lines.append(f"**Create:** `{m.get('suggested_slug','')}` — {m.get('suggested_title','')}")
            lines.append(f"**Evidence:** {m.get('evidence','')}")
            spokes = m.get("spokes_that_need_it", [])
            if spokes:
                lines.append(f"**Link from:** {', '.join(spokes)}")
            lines.append("")

    cross = audit.get("cross_cluster_links", [])
    if cross:
        lines += [f"## Cross-Cluster Link Opportunities ({len(cross)})", ""]
        lines.append("| From | To | Why |")
        lines.append("|------|----|-----|")
        for lk in cross:
            lines.append(
                f"| {lk.get('from_url','')} | {lk.get('to_url','')} | {lk.get('reason','')} |"
            )
        lines.append("")

    # Orphan pages (deterministic: 0 incoming internal links)
    orphans = [url for url, count in incoming.items() if count == 0]
    if orphans:
        lines += [f"## Orphan Pages ({len(orphans)})", "",
                  "These pages have no internal links pointing to them:", ""]
        for o in orphans:
            lines.append(f"- {o}")
        lines.append("")

    # Link graph summary
    lines += ["## Internal Link Graph", ""]
    lines.append("| Page | Outbound | Incoming |")
    lines.append("|------|----------|----------|")
    for url in sorted(graph.keys()):
        out = len(graph[url])
        inc = incoming.get(url, 0)
        lines.append(f"| {url} | {out} | {inc} |")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    pages_file: str,
    project_dir: str | None = None,
) -> dict:
    """
    Audit all URLs in pages_file as a content cluster map.

    Returns the audit dict. Writes cluster-fetch-cache.json and
    cluster-audit.md to project_dir (defaults to dir of pages_file).
    """
    urls = load_urls(pages_file)
    if not urls:
        print("[cluster] No URLs found in input file.")
        return {}

    out_dir = Path(project_dir) if project_dir else Path(pages_file).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path  = out_dir / "cluster-fetch-cache.json"
    report_path = out_dir / "cluster-audit.md"

    fetch_cache = _load_fetch_cache(cache_path)
    system_prompt = _load_system_prompt()

    print(f"[cluster] {len(urls)} URLs to audit")

    snapshots = []
    total = len(urls)

    for i, url in enumerate(urls, 1):
        if url in fetch_cache:
            print(f"[{i}/{total}] SKIP (cached): {url}")
            snapshots.append(fetch_cache[url])
            continue

        print(f"[{i}/{total}] Fetching: {url}")
        snap = _fetch_snapshot(url)
        fetch_cache[url] = snap
        _save_fetch_cache(cache_path, fetch_cache)
        snapshots.append(snap)

    # Filter out errored pages
    good_snapshots = [s for s in snapshots if not s.get("error")]
    errored = [s for s in snapshots if s.get("error")]
    if errored:
        print(f"[cluster] {len(errored)} page(s) failed to fetch — excluded from analysis")

    if not good_snapshots:
        print("[cluster] No pages fetched successfully.")
        return {}

    print(f"[cluster] Building internal link graph...")
    graph = build_link_graph(good_snapshots)
    incoming = _incoming_counts(graph)

    orphans = [url for url, count in incoming.items() if count == 0]
    print(f"[cluster] {len(orphans)} orphan page(s) (0 incoming internal links)")

    print(f"[cluster] Clustering with Haiku...")
    audit = _haiku_cluster(good_snapshots, incoming, system_prompt)

    if audit.get("error"):
        print(f"[cluster] ERROR: {audit['error']}", file=sys.stderr)
        return audit

    _write_report(audit, report_path, graph, incoming, len(good_snapshots))
    print(f"\n[cluster] Done. Report: {report_path}")

    n_clusters = len(audit.get("clusters", []))
    n_missing  = len(audit.get("missing_hubs", []))
    n_cross    = len(audit.get("cross_cluster_links", []))
    print(
        f"[cluster] Clusters: {n_clusters}  "
        f"Missing hubs: {n_missing}  "
        f"Cross-cluster opportunities: {n_cross}  "
        f"Orphans: {len(orphans)}"
    )

    return audit


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
            f.write("https://example.com/\n# skip\nhttps://example.com/about\n")
            fname = f.name
        try:
            urls = load_urls(fname)
            _chk(urls == ["https://example.com/", "https://example.com/about"],
                 f"Expected 2 URLs, got {urls}")
            print("Test 1 PASS: load_urls reads txt, skips comments")
        finally:
            os.unlink(fname)

    # -- Test 2: build_link_graph detects internal links ----------------------
    def test_build_link_graph():
        snapshots = [
            {"url": "https://example.com/", "final_url": "https://example.com/",
             "raw_links": ["https://example.com/about", "https://external.com/"]},
            {"url": "https://example.com/about", "final_url": "https://example.com/about",
             "raw_links": ["https://example.com/"]},
        ]
        graph = build_link_graph(snapshots)
        _chk("https://example.com/about" in graph["https://example.com"],
             "Homepage should link to about")
        _chk("https://external.com" not in str(graph),
             "External links should be excluded")
        print("Test 2 PASS: build_link_graph detects internal links, ignores external")

    # -- Test 3: _incoming_counts is correct ----------------------------------
    def test_incoming_counts():
        graph = {
            "https://example.com":       {"https://example.com/about"},
            "https://example.com/about": {"https://example.com"},
            "https://example.com/orphan": set(),
        }
        counts = _incoming_counts(graph)
        _chk(counts["https://example.com"] == 1, "Homepage should have 1 incoming")
        _chk(counts["https://example.com/about"] == 1, "About should have 1 incoming")
        _chk(counts["https://example.com/orphan"] == 0, "Orphan should have 0 incoming")
        print("Test 3 PASS: _incoming_counts correct including orphan detection")

    # -- Test 4: _normalise strips trailing slashes ----------------------------
    def test_normalise():
        _chk(_normalise("https://example.com/") == "https://example.com",
             "Trailing slash not stripped")
        _chk(_normalise("HTTPS://EXAMPLE.COM/Page") == "https://example.com/page",
             "Not lowercased")
        print("Test 4 PASS: _normalise strips slashes and lowercases")

    # -- Test 5: _strip_fences -------------------------------------------------
    def test_strip_fences():
        raw = '```json\n{"clusters":[]}\n```'
        _chk(_strip_fences(raw) == '{"clusters":[]}', "Fences not stripped")
        print("Test 5 PASS: _strip_fences removes json fences")

    for test_fn in [
        test_load_txt,
        test_build_link_graph,
        test_incoming_counts,
        test_normalise,
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
