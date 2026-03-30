import csv
import os
import sys
import time

from browser import fetch_page
from extractor import extract
from hitl import add_to_human_review, pause_and_prompt, should_pause
from linkchecker import check_links
from reporter import write_result, write_summary
from state import is_audited, load_state, mark_audited

INPUT_CSV = os.path.join(os.path.dirname(__file__), "input.csv")
INTER_URL_DELAY = 2  # seconds between URLs


def read_urls(csv_path: str) -> list[str]:
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


def main() -> None:
    auto = "--auto" in sys.argv

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it in your shell before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    all_urls = read_urls(INPUT_CSV)
    state = load_state()
    pending = [u for u in all_urls if not is_audited(state, u)]
    total = len(all_urls)

    if not pending:
        print("All URLs have already been audited.")
        write_summary()
        return

    audited_count = total - len(pending)
    print(f"Starting audit: {len(pending)} pending, {audited_count} already done.\n")

    try:
        for url in pending:
            current_index = all_urls.index(url) + 1

            # --- a. Browser fetch (with retry support) ---
            snapshot = None
            while True:
                snapshot = fetch_page(url)

                # --- b. HITL check ---
                if should_pause(snapshot):
                    reason = _pause_reason(snapshot)

                    if auto:
                        print(f"[{current_index}/{total}] {url} ->AUTO-SKIPPED ({reason})")
                        add_to_human_review(url)
                        mark_audited(state, url)
                        break

                    action = pause_and_prompt(url, reason)

                    if action == "skip":
                        add_to_human_review(url)
                        mark_audited(state, url)
                        print(f"[{current_index}/{total}] {url} ->SKIPPED (human review)")
                        break

                    elif action == "retry":
                        print(f"  Retrying {url} ...")
                        continue  # re-fetch

                    elif action == "quit":
                        print("Quitting. Progress saved.")
                        sys.exit(0)
                else:
                    break  # snapshot is clean, proceed

            # skip sentinel — already marked audited above
            if (state.get("audited") and url in state["audited"]):
                time.sleep(INTER_URL_DELAY)
                continue

            # --- c. SEO extraction ---
            result = extract(snapshot)

            # --- d. Link checking ---
            raw_links = snapshot.get("raw_links") or []
            final_url = snapshot.get("final_url") or url
            links_result = check_links(raw_links, final_url)

            # --- e. Merge broken links into result ---
            result["broken_links"] = links_result

            # --- f. Persist result immediately ---
            write_result(result)

            # --- g. Mark audited ---
            mark_audited(state, url)

            # --- h. Progress line ---
            status = "PASS" if _overall_pass(result) else "FAIL"
            print(f"[{current_index}/{total}] {url} ->{status}")

            # --- i. Polite delay ---
            time.sleep(INTER_URL_DELAY)

    except KeyboardInterrupt:
        print("\nInterrupted. Progress saved — rerun to continue from where you left off.")
        sys.exit(0)

    # --- Final summary ---
    write_summary()

    entries = _count_passed()
    print(f"\nAudit complete. {entries['passed']}/{entries['total']} URLs passed. "
          f"Report saved to report.json")


def _pause_reason(snapshot: dict) -> str:
    code = snapshot.get("status_code")
    if code is None:
        return "Navigation failed (status_code is None)"
    if code != 200 and code not in (301, 302, 307, 308):
        return f"Unexpected status code: {code}"
    title = snapshot.get("title") or ""
    h1s = snapshot.get("h1s") or []
    if any(kw in (title + " ".join(h1s)).lower() for kw in ("login", "sign in", "access denied", "log in", "signin")):
        return "Page appears to be login-gated"
    return "Manual review required"


def _count_passed() -> dict:
    """Read report.json and count pass/total for the final summary line."""
    import json
    report_path = os.path.join(os.path.dirname(__file__), "report.json")
    if not os.path.exists(report_path):
        return {"passed": 0, "total": 0}
    with open(report_path, encoding="utf-8") as f:
        entries = json.load(f)
    passed = sum(1 for e in entries if _overall_pass(e))
    return {"passed": passed, "total": len(entries)}


if __name__ == "__main__":
    main()
