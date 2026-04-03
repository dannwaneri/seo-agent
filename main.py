"""
Unified entry point for seo-agent.

Free users:  python main.py [--project NAME] [--auto]
Pro users:   python main.py --pro [--project NAME] [--tiered] [--rewrite] [--auto]
                             [--voice-sample PATH]
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

from config import check_license, is_pro
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
    """
    Run the audit loop. Returns run statistics dict.

    Handles both free and pro paths based on args.
    """
    pro                = is_pro()
    use_tiered         = pro and args.tiered
    use_rewrite        = pro and args.rewrite
    use_pagespeed      = pro and getattr(args, "pagespeed", False)
    use_structured_data = pro and getattr(args, "structured_data", False)
    email_recipient    = getattr(args, "email", None) if pro else None
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

    # g. Pro: generate PDF
    generated_pdf_path = None
    if pro and (urls_audited > 0):
        from premium.enhanced_reporter import generate_pdf
        project_name = args.project or "default"
        pdf_path = os.path.join(paths["reports_dir"], "audit_report.pdf")
        os.makedirs(paths["reports_dir"], exist_ok=True)
        try:
            generate_pdf(results, project_name, pdf_path)
            generated_pdf_path = pdf_path
            print(f"PDF report saved to {pdf_path}")
        except Exception as exc:
            print(f"[main] PDF generation failed: {exc}", file=sys.stderr)

    # h. Pro: email report
    if pro and email_recipient:
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

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEO audit agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project",      metavar="NAME", help="Project name (uses projects/NAME/)")
    parser.add_argument("--pro",          action="store_true", help="Enable premium features")
    parser.add_argument("--tiered",       action="store_true", help="Use cost-curve routing [requires --pro]")
    parser.add_argument("--rewrite",      action="store_true", help="Generate rewrite suggestions [requires --pro]")
    parser.add_argument("--auto",            action="store_true", help="Auto-skip URLs requiring human review")
    parser.add_argument("--voice-sample",    metavar="PATH",      help="Path to voice sample file [requires --rewrite]")
    parser.add_argument("--email",           metavar="RECIPIENT", help="Email report to this address after run [requires --pro]")
    parser.add_argument("--pagespeed",       action="store_true", help="Enable PageSpeed Insights check per URL [requires --pro]")
    parser.add_argument("--structured-data", action="store_true", help="Enable JSON-LD structured data validation [requires --pro]")
    args = parser.parse_args()

    # --- Flag dependency checks ---
    if args.tiered and not args.pro:
        print("ERROR: --tiered requires --pro", file=sys.stderr)
        sys.exit(1)
    if args.rewrite and not args.pro:
        print("ERROR: --rewrite requires --pro", file=sys.stderr)
        sys.exit(1)
    if args.voice_sample and not args.rewrite:
        print("ERROR: --voice-sample requires --rewrite", file=sys.stderr)
        sys.exit(1)
    if args.pagespeed and not args.pro:
        print("This feature requires --pro", file=sys.stderr)
        sys.exit(1)
    if args.structured_data and not args.pro:
        print("This feature requires --pro", file=sys.stderr)
        sys.exit(1)
    if args.email and not args.pro:
        print("This feature requires --pro", file=sys.stderr)
        sys.exit(1)

    # --- License check (exits if --pro without key) ---
    check_license()

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

        def _run_proc(argv, env_extra=None, strip_license=True):
            env = os.environ.copy()
            env.pop("_SEO_MAIN_MODE", None)   # subprocesses call main(), not tests
            if strip_license:
                env.pop("SEO_AGENT_LICENSE", None)
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
        # Test 3: --pro without SEO_AGENT_LICENSE -> exit 1 + error           #
        # ------------------------------------------------------------------ #
        def test_pro_without_license():
            rc, out = _run_proc(["--pro"])
            assert rc == 1, f"Expected exit 1, got {rc}"
            assert "ERROR" in out and "SEO_AGENT_LICENSE" in out, \
                f"Expected license error, got: {out!r}"
        run_test("3: --pro without license -> exit 1 + clear error", test_pro_without_license)

        # ------------------------------------------------------------------ #
        # Test 4: --tiered without --pro -> exit 1 + "requires --pro"         #
        # ------------------------------------------------------------------ #
        def test_tiered_without_pro():
            rc, out = _run_proc(["--tiered"])
            assert rc == 1, f"Expected exit 1, got {rc}"
            assert "requires --pro" in out.lower() or "--pro" in out, \
                f"Expected --pro message, got: {out!r}"
        run_test("4: --tiered without --pro -> exit 1 + requires --pro", test_tiered_without_pro)

        # ------------------------------------------------------------------ #
        # Test 5: after completed run, state.json history[] has new entry     #
        # ------------------------------------------------------------------ #
        def test_history_appended():
            import core.state as sm
            import core.reporter as rm

            proj_dir = os.path.join(REPO_ROOT, "projects", "_test_history_t5")
            os.makedirs(os.path.join(proj_dir, "reports"), exist_ok=True)
            csv_path   = os.path.join(proj_dir, "input.csv")
            state_path = os.path.join(proj_dir, "state.json")

            test_url = "https://example.com/already-done"
            with open(csv_path, "w") as f:
                f.write(f"url\n{test_url}\n")
            with open(state_path, "w") as f:
                json.dump({"audited": [test_url], "pending": [], "needs_human": [], "history": []}, f)

            orig_state_file   = sm.STATE_FILE
            orig_report_json  = rm.REPORT_JSON
            orig_report_sum   = rm.REPORT_SUMMARY
            sm.STATE_FILE     = state_path
            rm.REPORT_JSON    = os.path.join(proj_dir, "report.json")
            rm.REPORT_SUMMARY = os.path.join(proj_dir, "report-summary.txt")

            try:
                import argparse as _ap
                test_args = _ap.Namespace(
                    project="_test_history_t5", pro=False, tiered=False,
                    rewrite=False, auto=True, voice_sample=None,
                )
                paths = _get_paths("_test_history_t5")
                _patch_core_modules(paths)

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
                assert "history" in saved and len(saved["history"]) >= 1, \
                    f"Expected history entry, got: {saved.get('history')}"
                entry = saved["history"][-1]
                for key in ("run_id", "urls_audited", "urls_skipped", "pass_count", "fail_count", "report_path"):
                    assert key in entry, f"Missing key {key} in history entry"
            finally:
                sm.STATE_FILE     = orig_state_file
                rm.REPORT_JSON    = orig_report_json
                rm.REPORT_SUMMARY = orig_report_sum
                shutil.rmtree(proj_dir, ignore_errors=True)

        run_test("5: completed run appends history entry with correct keys", test_history_appended)

        # ------------------------------------------------------------------ #
        # Test 6: --pro --tiered routes through cost curve                    #
        # ------------------------------------------------------------------ #
        def test_pro_tiered_routes_cost_curve():
            import core.state as sm
            import core.reporter as rm
            import premium.cost_curve as cc

            proj_dir   = os.path.join(REPO_ROOT, "projects", "_test_tiered_t6")
            os.makedirs(os.path.join(proj_dir, "reports"), exist_ok=True)
            csv_path   = os.path.join(proj_dir, "input.csv")
            state_path = os.path.join(proj_dir, "state.json")

            test_url = "https://example.com/tiered-test"
            with open(csv_path, "w") as f:
                f.write(f"url\n{test_url}\n")
            with open(state_path, "w") as f:
                json.dump({"audited": [], "pending": [], "needs_human": [], "history": []}, f)

            orig_state   = sm.STATE_FILE
            orig_rjson   = rm.REPORT_JSON
            orig_rsum    = rm.REPORT_SUMMARY
            paths = _get_paths("_test_tiered_t6")
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
                    project="_test_tiered_t6", pro=True, tiered=True,
                    rewrite=False, auto=True, voice_sample=None,
                )
                # Patch fetch_page in __main__ globals (where run_audit closes over it)
                _main_mod = sys.modules["__main__"]
                orig_fetch_fn  = _main_mod.fetch_page
                orig_audit_url = cc.audit_url

                _main_mod.fetch_page = lambda url: fake_snapshot
                cc.audit_url         = fake_tiered

                import config as _cfg
                orig_is_pro = _cfg.is_pro
                _cfg.is_pro = lambda: True
                orig_main_is_pro = _main_mod.is_pro
                _main_mod.is_pro = lambda: True

                try:
                    stats = run_audit(test_args, paths)
                finally:
                    _main_mod.fetch_page = orig_fetch_fn
                    cc.audit_url         = orig_audit_url
                    _cfg.is_pro          = orig_is_pro
                    _main_mod.is_pro     = orig_main_is_pro

                assert len(tiered_calls) == 1, f"Expected 1 tiered call, got {tiered_calls}"
                assert tiered_calls[0] is True, "audit_url not called with tiered=True"
            finally:
                sm.STATE_FILE     = orig_state
                rm.REPORT_JSON    = orig_rjson
                rm.REPORT_SUMMARY = orig_rsum
                shutil.rmtree(proj_dir, ignore_errors=True)

        run_test("6: --pro --tiered routes through cost curve (audit_url called with tiered=True)", test_pro_tiered_routes_cost_curve)

        print()
        if failures:
            print(f"{len(failures)} test(s) failed: {failures}")
            sys.exit(1)
        else:
            print("All 6 acceptance tests passed.")
