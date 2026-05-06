"""
Unified entry point for seo-agent.

Usage:
  python main.py [--project NAME] [--auto] [--tiered] [--rewrite]
  python main.py qualify-backlinks <file> --niche "AI agents python"
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

# Repo root is this file's directory — ensure it's on sys.path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Load .env from repo root if present (no hard dependency on python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    _env_path = os.path.join(REPO_ROOT, ".env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

from core.browser import fetch_page
from core.hitl import add_to_human_review, pause_and_prompt, should_pause
from core.linkchecker import check_links
import core.reporter as _reporter_mod
from core.reporter import write_result, write_summary
import core.state as _state_mod
from core.state import append_run_record, is_audited, load_state, mark_audited

INTER_URL_DELAY = 2


# ---------------------------------------------------------------------------
# Path resolution — works for all users, no premium imports
# ---------------------------------------------------------------------------

def _get_paths(project: str | None) -> dict:
    """Return file paths for input CSV, state, report, and reports dir."""
    if project:
        proj_dir = os.path.join(REPO_ROOT, "projects", project)
        reports_dir = os.path.join(proj_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        input_csv    = os.path.join(proj_dir, "input.csv")
        state_json   = os.path.join(proj_dir, "state.json")
        report_json  = os.path.join(proj_dir, "report.json")
        report_summary = os.path.join(proj_dir, "report-summary.txt")
        # Bootstrap new project files if absent
        if not os.path.exists(input_csv):
            with open(input_csv, "w", encoding="utf-8") as f:
                f.write("url\n")
        if not os.path.exists(state_json):
            with open(state_json, "w", encoding="utf-8") as f:
                json.dump({"audited": [], "pending": [], "needs_human": [], "history": []},
                          f, indent=2)
    else:
        reports_dir  = os.path.join(REPO_ROOT, "reports")
        input_csv    = os.path.join(REPO_ROOT, "input.csv")
        state_json   = os.path.join(REPO_ROOT, "state.json")
        report_json  = os.path.join(REPO_ROOT, "report.json")
        report_summary = os.path.join(REPO_ROOT, "report-summary.txt")

    return {
        "input_csv":      input_csv,
        "state_json":     state_json,
        "report_json":    report_json,
        "report_summary": report_summary,
        "reports_dir":    reports_dir,
    }


def _patch_core_modules(paths: dict) -> None:
    """Redirect core module path constants to project-specific files."""
    _state_mod.STATE_FILE         = paths["state_json"]
    _reporter_mod.REPORT_JSON     = paths["report_json"]
    _reporter_mod.REPORT_SUMMARY  = paths["report_summary"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _read_urls(csv_path: str) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["url"].strip() for row in reader if row["url"].strip()]


def _overall_pass(result: dict) -> bool:
    for field in ("title", "description", "h1", "canonical"):
        if (result.get(field) or {}).get("status") != "PASS":
            return False
    if (result.get("broken_links") or {}).get("status") == "FAIL":
        return False
    return True


def _pause_reason(snapshot: dict) -> str:
    code = snapshot.get("status_code")
    if code is None:
        return "Navigation failed (status_code is None)"
    if code != 200 and code not in (301, 302, 307, 308):
        return f"Unexpected status code: {code}"
    title = snapshot.get("title") or ""
    h1s   = snapshot.get("h1s") or []
    if any(kw in (title + " ".join(h1s)).lower()
           for kw in ("login", "sign in", "access denied", "log in", "signin")):
        return "Page appears to be login-gated"
    return "Manual review required"


# ---------------------------------------------------------------------------
# Core audit loop
# ---------------------------------------------------------------------------

def run_audit(args: argparse.Namespace, paths: dict) -> dict:
    """Run the audit loop. Returns run statistics dict."""
    use_tiered          = args.tiered
    use_rewrite         = args.rewrite
    use_pagespeed       = getattr(args, "pagespeed", False)
    use_structured_data = getattr(args, "structured_data", False)
    email_recipient     = getattr(args, "email", None)
    auto               = args.auto

    # Resolve PageSpeed API key once at startup
    pagespeed_key = None
    if use_pagespeed:
        import logging as _logging
        from config import get_pagespeed_key as _get_pagespeed_key
        pagespeed_key = _get_pagespeed_key()
        if pagespeed_key is None:
            _logging.warning("PAGESPEED_API_KEY not set -- skipping performance checks")
            use_pagespeed = False

    # Pro-only extractor (import only when needed)
    if use_tiered:
        from premium.cost_curve import audit_url as _tiered_audit

    all_urls = _read_urls(paths["input_csv"])
    state    = load_state()
    pending  = [u for u in all_urls if not is_audited(state, u)]
    total    = len(all_urls)

    if not pending:
        print("All URLs have already been audited.")
        write_summary()
        return {"urls_audited": 0, "urls_skipped": 0, "pass_count": 0,
                "fail_count": 0, "results": []}

    print(f"Starting audit: {len(pending)} pending, {total - len(pending)} already done.\n")

    urls_audited = 0
    urls_skipped = 0
    pass_count   = 0
    fail_count   = 0
    results      = []

    try:
        for url in pending:
            current_index = all_urls.index(url) + 1

            # a. Browser fetch + HITL
            while True:
                snapshot = fetch_page(url)

                if should_pause(snapshot):
                    reason = _pause_reason(snapshot)

                    if auto:
                        print(f"[{current_index}/{total}] {url} ->AUTO-SKIPPED ({reason})")
                        add_to_human_review(url)
                        mark_audited(state, url)
                        urls_skipped += 1
                        break

                    action = pause_and_prompt(url, reason)

                    if action == "skip":
                        add_to_human_review(url)
                        mark_audited(state, url)
                        print(f"[{current_index}/{total}] {url} ->SKIPPED (human review)")
                        urls_skipped += 1
                        break

                    elif action == "retry":
                        print(f"  Retrying {url} ...")
                        continue

                    elif action == "quit":
                        print("Quitting. Progress saved.")
                        sys.exit(0)
                else:
                    break

            # Already handled (skipped)
            if url in state.get("audited", []):
                time.sleep(INTER_URL_DELAY)
                continue

            # b. SEO extraction — tiered (pro) or direct Sonnet (free)
            if use_tiered:
                result = _tiered_audit(snapshot, tiered=True)
            else:
                from core.extractor import extract
                result = extract(snapshot)

            # c. Link checking
            raw_links = snapshot.get("raw_links") or []
            final_url = snapshot.get("final_url") or url
            result["broken_links"] = check_links(raw_links, final_url)

            # d. PageSpeed Insights (pro + --pagespeed)
            if use_pagespeed and pagespeed_key:
                from premium.pagespeed import check_pagespeed
                perf = check_pagespeed(url, pagespeed_key)
                if perf is not None:
                    result["performance"] = perf

            # e. Structured data / JSON-LD (pro + --structured-data)
            if use_structured_data:
                from premium.structured_data import extract_json_ld
                result["structured_data"] = extract_json_ld(
                    snapshot.get("json_ld_blocks") or []
                )

            # f. Rewrite suggestions (pro only)
            if use_rewrite:
                voice_sample = None
                if args.voice_sample and os.path.isfile(args.voice_sample):
                    with open(args.voice_sample, encoding="utf-8") as f:
                        voice_sample = f.read()
                from premium.rewrite_agent import generate_rewrites
                result["rewrites"] = generate_rewrites(result, voice_sample=voice_sample)

            # e. Persist result
            write_result(result)
            mark_audited(state, url)
            results.append(result)
            urls_audited += 1

            status = "PASS" if _overall_pass(result) else "FAIL"
            if status == "PASS":
                pass_count += 1
            else:
                fail_count += 1
            print(f"[{current_index}/{total}] {url} ->{status}")

            time.sleep(INTER_URL_DELAY)

    except KeyboardInterrupt:
        print("\nInterrupted. Progress saved -- rerun to continue from where you left off.")
        sys.exit(0)

    # f. Summary
    write_summary()

    # g. Generate PDF if premium module available
    generated_pdf_path = None
    if urls_audited > 0:
        try:
            from premium.enhanced_reporter import generate_pdf
            project_name = args.project or "default"
            pdf_path = os.path.join(paths["reports_dir"], "audit_report.pdf")
            os.makedirs(paths["reports_dir"], exist_ok=True)
            generate_pdf(results, project_name, pdf_path)
            generated_pdf_path = pdf_path
            print(f"PDF report saved to {pdf_path}")
        except (ImportError, Exception) as exc:
            if not isinstance(exc, ImportError):
                print(f"[main] PDF generation failed: {exc}", file=sys.stderr)

    # h. Email report if recipient set
    if email_recipient:
        from premium.email_reporter import send_report_email
        project_name = getattr(args, "project", None) or "default"
        summary_dict = {
            "pass_count":  pass_count,
            "fail_count":  fail_count,
            "needs_human": state.get("needs_human") or [],
        }
        sent = send_report_email(
            email_recipient, project_name, summary_dict, generated_pdf_path
        )
        if sent:
            print(f"Report emailed to {email_recipient}")

    return {
        "urls_audited": urls_audited,
        "urls_skipped": urls_skipped,
        "pass_count":   pass_count,
        "fail_count":   fail_count,
        "results":      results,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_qualify_backlinks(argv: list[str]) -> None:
    """Handle: python main.py qualify-backlinks <file> --niche "..." [--project NAME]"""
    parser = argparse.ArgumentParser(
        prog="main.py qualify-backlinks",
        description="Score a list of URLs for backlink opportunity.",
    )
    parser.add_argument("input_file", help="Path to .txt or .csv file containing URLs")
    parser.add_argument("--niche", required=True, metavar="NICHE",
                        help='Target niche, e.g. "AI agents python"')
    parser.add_argument("--project", metavar="NAME",
                        help="Project name — output goes to projects/NAME/")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    project_dir = None
    if args.project:
        project_dir = os.path.join(REPO_ROOT, "projects", args.project)
        os.makedirs(project_dir, exist_ok=True)

    from modules.backlink_qualifier import run as qualify_run
    qualify_run(
        input_file=args.input_file,
        niche=args.niche,
        project_dir=project_dir,
    )


def _run_cluster_audit(argv: list[str]) -> None:
    """Handle: python main.py cluster-audit --pages file [--project NAME]"""
    parser = argparse.ArgumentParser(
        prog="main.py cluster-audit",
        description="Map site pages into topic clusters and find orphans and missing hubs.",
    )
    parser.add_argument("--pages",   required=True, metavar="FILE",
                        help="Path to .txt or .csv file of site URLs to audit")
    parser.add_argument("--project", metavar="NAME",
                        help="Project name — output goes to projects/NAME/")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    project_dir = None
    if args.project:
        project_dir = os.path.join(REPO_ROOT, "projects", args.project)
        os.makedirs(project_dir, exist_ok=True)

    from modules.cluster_audit import run as cluster_run
    cluster_run(
        pages_file=args.pages,
        project_dir=project_dir,
    )


def _run_relevance_score(argv: list[str]) -> None:
    """Handle: python main.py relevance-score --target URL --pages file [--project NAME]"""
    parser = argparse.ArgumentParser(
        prog="main.py relevance-score",
        description="Score candidate pages as internal link opportunities for a target URL.",
    )
    parser.add_argument("--target",  required=True, metavar="URL",
                        help="The page you want to rank — score links pointing to this")
    parser.add_argument("--pages",   required=True, metavar="FILE",
                        help="Path to .txt or .csv file of candidate URLs to score")
    parser.add_argument("--project", metavar="NAME",
                        help="Project name — output goes to projects/NAME/")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    project_dir = None
    if args.project:
        project_dir = os.path.join(REPO_ROOT, "projects", args.project)
        os.makedirs(project_dir, exist_ok=True)

    from modules.relevance_scorer import run as relevance_run
    relevance_run(
        target_url=args.target,
        pages_file=args.pages,
        project_dir=project_dir,
    )


def _run_gsc_insights(argv: list[str]) -> None:
    """Handle: python main.py gsc-insights <file> [--project NAME] [--min-impressions N]"""
    parser = argparse.ArgumentParser(
        prog="main.py gsc-insights",
        description="Analyse a GSC query export for quick wins, cannibalisation, and gaps.",
    )
    parser.add_argument("gsc_file", help="Path to GSC export CSV")
    parser.add_argument("--project", metavar="NAME",
                        help="Project name — output goes to projects/NAME/")
    parser.add_argument("--min-impressions", type=int, default=50, metavar="N",
                        help="Minimum impressions to include a query (default: 50)")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    project_dir = None
    if args.project:
        project_dir = os.path.join(REPO_ROOT, "projects", args.project)
        os.makedirs(project_dir, exist_ok=True)

    from modules.gsc_insights import run as gsc_run
    gsc_run(
        gsc_file=args.gsc_file,
        project_dir=project_dir,
        min_impressions=args.min_impressions,
    )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "qualify-backlinks":
        _run_qualify_backlinks(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "gsc-insights":
        _run_gsc_insights(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "relevance-score":
        _run_relevance_score(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "cluster-audit":
        _run_cluster_audit(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        description="SEO audit agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project",        metavar="NAME",      help="Project name (uses projects/NAME/)")
    parser.add_argument("--tiered",         action="store_true", help="Use cost-curve tiered routing (Tier1 > Haiku > Sonnet)")
    parser.add_argument("--rewrite",        action="store_true", help="Generate rewrite suggestions")
    parser.add_argument("--auto",           action="store_true", help="Auto-skip URLs requiring human review")
    parser.add_argument("--voice-sample",   metavar="PATH",      help="Path to voice sample file (used with --rewrite)")
    parser.add_argument("--email",          metavar="RECIPIENT", help="Email report to this address after run")
    parser.add_argument("--pagespeed",      action="store_true", help="Enable PageSpeed Insights check per URL")
    parser.add_argument("--structured-data",action="store_true", help="Enable JSON-LD structured data validation")
    args = parser.parse_args()

    if args.voice_sample and not args.rewrite:
        print("ERROR: --voice-sample requires --rewrite", file=sys.stderr)
        sys.exit(1)

    # --- API key check ---
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Resolve paths and patch core modules ---
    paths = _get_paths(args.project)
    _patch_core_modules(paths)

    # --- Run audit ---
    run_id = datetime.now(timezone.utc).isoformat()
    stats  = run_audit(args, paths)

    # --- Append run record to history ---
    record = {
        "run_id":       run_id,
        "urls_audited": stats["urls_audited"],
        "urls_skipped": stats["urls_skipped"],
        "pass_count":   stats["pass_count"],
        "fail_count":   stats["fail_count"],
        "report_path":  paths["report_json"],
    }
    append_run_record(record)

    total_checked = stats["urls_audited"]
    if total_checked > 0:
        print(f"\nAudit complete. {stats['pass_count']}/{total_checked} URLs passed. "
              f"Report saved to {paths['report_json']}")


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _mode = os.environ.get("_SEO_MAIN_MODE", "normal")
    if _mode != "test":
        main()
    else:
        import subprocess
        import tempfile
        import shutil
        from unittest.mock import MagicMock, patch

        failures = []

        def run_test(name, fn):
            try:
                fn()
                print(f"Test {name} PASS")
            except Exception as exc:
                print(f"Test {name} FAIL: {exc}")
                failures.append(name)

        python = sys.executable
        script = __file__

        def _run_proc(argv, env_extra=None):
            env = os.environ.copy()
            env.pop("_SEO_MAIN_MODE", None)
            if env_extra:
                env.update(env_extra)
            r = subprocess.run([python, script] + argv,
                               capture_output=True, text=True, env=env, timeout=10)
            return r.returncode, r.stdout + r.stderr

        # ------------------------------------------------------------------ #
        # Test 1: no flags -> "All URLs have already been audited."            #
        # (All URLs in state.json are audited, so no browser opens.)          #
        # ------------------------------------------------------------------ #
        def test_no_flags_identical_behavior():
            rc, out = _run_proc([])
            assert rc == 0, f"Unexpected exit {rc}: {out}"
            assert "All URLs have already been audited" in out or "Audit complete" in out, \
                f"Unexpected output: {out!r}"
        run_test("1: no flags runs cleanly (identical to core/index.py)", test_no_flags_identical_behavior)

        # ------------------------------------------------------------------ #
        # Test 2: --project acme reads/writes from projects/acme/             #
        # ------------------------------------------------------------------ #
        def test_project_flag():
            proj_dir = os.path.join(REPO_ROOT, "projects", "_test_acme_t2")
            os.makedirs(os.path.join(proj_dir, "reports"), exist_ok=True)
            with open(os.path.join(proj_dir, "input.csv"), "w") as f:
                f.write("url\n")  # header only, no URLs
            with open(os.path.join(proj_dir, "state.json"), "w") as f:
                json.dump({"audited": [], "pending": [], "needs_human": [], "history": []}, f)
            try:
                rc, out = _run_proc(["--project", "_test_acme_t2"])
                assert rc == 0, f"Exit {rc}: {out}"
                with open(os.path.join(proj_dir, "state.json")) as f:
                    state = json.load(f)
                assert "history" in state, "history key missing"
            finally:
                shutil.rmtree(proj_dir, ignore_errors=True)
        run_test("2: --project reads/writes from projects/NAME/", test_project_flag)

        # ------------------------------------------------------------------ #
        # Test 3: --tiered flag accepted, routes through cost curve           #
        # ------------------------------------------------------------------ #
        def test_tiered_routes_cost_curve():
            import core.state as sm
            import core.reporter as rm
            import premium.cost_curve as cc

            proj_dir   = os.path.join(REPO_ROOT, "projects", "_test_tiered_t3")
            os.makedirs(os.path.join(proj_dir, "reports"), exist_ok=True)
            state_path = os.path.join(proj_dir, "state.json")

            test_url = "https://example.com/tiered-test"
            with open(os.path.join(proj_dir, "input.csv"), "w") as f:
                f.write("url\n" + test_url + "\n")
            with open(state_path, "w") as f:
                json.dump({"audited": [], "pending": [], "needs_human": [], "history": []}, f)

            paths = _get_paths("_test_tiered_t3")
            _patch_core_modules(paths)

            fake_snapshot = {
                "url": test_url, "final_url": test_url, "status_code": 200,
                "title": "Good Title Here", "meta_description": "Good description.",
                "h1s": ["Good H1"], "canonical": test_url, "raw_links": [],
            }
            fake_result = {
                "url": test_url, "final_url": test_url, "status_code": 200,
                "title":       {"value": "Good Title Here", "length": 15, "status": "PASS"},
                "description": {"value": "Good description.", "length": 17, "status": "PASS"},
                "h1":          {"count": 1, "value": "Good H1", "status": "PASS"},
                "canonical":   {"value": test_url, "status": "PASS"},
                "flags": [], "method": "deterministic",
            }

            tiered_calls = []
            def fake_tiered(snapshot, tiered=False):
                tiered_calls.append(tiered)
                return fake_result

            try:
                import argparse as _ap
                test_args = _ap.Namespace(
                    project="_test_tiered_t3", tiered=True,
                    rewrite=False, auto=True, voice_sample=None,
                    email=None, pagespeed=False, structured_data=False,
                )
                _main_mod = sys.modules["__main__"]
                orig_fetch_fn  = _main_mod.fetch_page
                orig_audit_url = cc.audit_url
                _main_mod.fetch_page = lambda url: fake_snapshot
                cc.audit_url         = fake_tiered
                try:
                    run_audit(test_args, paths)
                finally:
                    _main_mod.fetch_page = orig_fetch_fn
                    cc.audit_url         = orig_audit_url

                assert len(tiered_calls) == 1, f"Expected 1 tiered call, got {tiered_calls}"
                assert tiered_calls[0] is True, "audit_url not called with tiered=True"
            finally:
                shutil.rmtree(proj_dir, ignore_errors=True)

        run_test("3: --tiered routes through cost curve (no license needed)", test_tiered_routes_cost_curve)

        # ------------------------------------------------------------------ #
        # Test 4: completed run appends history entry                         #
        # ------------------------------------------------------------------ #
        def test_history_appended():
            import core.state as sm
            import core.reporter as rm

            proj_dir   = os.path.join(REPO_ROOT, "projects", "_test_history_t4")
            os.makedirs(os.path.join(proj_dir, "reports"), exist_ok=True)
            csv_path   = os.path.join(proj_dir, "input.csv")
            state_path = os.path.join(proj_dir, "state.json")

            test_url = "https://example.com/already-done"
            with open(csv_path, "w") as f:
                f.write("url\n" + test_url + "\n")
            with open(state_path, "w") as f:
                json.dump({"audited": [test_url], "pending": [], "needs_human": [], "history": []}, f)

            paths = _get_paths("_test_history_t4")
            _patch_core_modules(paths)

            try:
                import argparse as _ap
                test_args = _ap.Namespace(
                    project="_test_history_t4", tiered=False,
                    rewrite=False, auto=True, voice_sample=None,
                    email=None, pagespeed=False, structured_data=False,
                )
                run_id = datetime.now(timezone.utc).isoformat()
                stats  = run_audit(test_args, paths)
                record = {
                    "run_id":       run_id,
                    "urls_audited": stats["urls_audited"],
                    "urls_skipped": stats["urls_skipped"],
                    "pass_count":   stats["pass_count"],
                    "fail_count":   stats["fail_count"],
                    "report_path":  paths["report_json"],
                }
                append_run_record(record)

                with open(state_path) as f:
                    saved = json.load(f)
                assert "history" in saved and len(saved["history"]) >= 1
                entry = saved["history"][-1]
                for key in ("run_id", "urls_audited", "urls_skipped", "pass_count", "fail_count", "report_path"):
                    assert key in entry, f"Missing key {key}"
            finally:
                shutil.rmtree(proj_dir, ignore_errors=True)

        run_test("4: completed run appends history entry with correct keys", test_history_appended)

        print()
        if failures:
            print(f"{len(failures)} test(s) failed: {failures}")
            sys.exit(1)
        else:
            print("All 4 acceptance tests passed.")
