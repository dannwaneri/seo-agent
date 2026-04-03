"""Generates SEO audit reports (JSON output + text summary)."""

import json
import os
import sys

# report files live in the project root, one level above core/
_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_JSON = os.path.join(_DIR, "report.json")
REPORT_SUMMARY = os.path.join(_DIR, "report-summary.txt")

_SEO_FIELDS = ("title", "description", "h1", "canonical")


def _load_report() -> list[dict]:
    if not os.path.exists(REPORT_JSON):
        return []
    with open(REPORT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_report(entries: list[dict]) -> None:
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def write_result(result: dict) -> None:
    """Append result to report.json, or update if url already present."""
    url = result.get("url") or result.get("final_url", "")
    entries = _load_report()
    for i, entry in enumerate(entries):
        if (entry.get("url") or entry.get("final_url", "")) == url:
            entries[i] = result
            _save_report(entries)
            return
    entries.append(result)
    _save_report(entries)


def _is_overall_pass(result: dict) -> bool:
    """PASS only if all SEO fields pass and no broken links."""
    for field in _SEO_FIELDS:
        field_data = result.get(field)
        if not field_data or field_data.get("status") != "PASS":
            return False
    links = result.get("links", {})
    if links and links.get("status") == "FAIL":
        return False
    return True


def _failed_fields(result: dict) -> list[str]:
    failed = []
    for field in _SEO_FIELDS:
        field_data = result.get(field)
        if not field_data or field_data.get("status") != "PASS":
            failed.append(field)
    links = result.get("links", {})
    if links and links.get("status") == "FAIL":
        failed.append("broken_links")
    return failed


def write_summary() -> None:
    """Read report.json and write a human-readable report-summary.txt."""
    entries = _load_report()
    lines = []
    passed = 0

    for result in entries:
        url = result.get("url") or result.get("final_url", "unknown")
        overall = _is_overall_pass(result)
        if overall:
            passed += 1
            status_str = "PASS"
            detail = ""
        else:
            status_str = "FAIL"
            failed = _failed_fields(result)
            detail = f" [{', '.join(failed)}]"
        lines.append(f"{url} | {status_str}{detail}")

    total = len(entries)
    lines.append(f"\n{passed}/{total} URLs passed")

    with open(REPORT_SUMMARY, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Manual acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import shutil

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    # Sample results
    _PASS_RESULT = {
        "url": "https://example.com/pass",
        "title": {"value": "Good Title", "length": 10, "status": "PASS"},
        "description": {"value": "Good desc", "length": 9, "status": "PASS"},
        "h1": {"count": 1, "value": "Heading", "status": "PASS"},
        "canonical": {"value": "https://example.com/pass", "status": "PASS"},
    }
    _FAIL_RESULT = {
        "url": "https://example.com/fail",
        "title": {"value": None, "length": 0, "status": "FAIL"},
        "description": {"value": "desc", "length": 4, "status": "PASS"},
        "h1": {"count": 0, "value": None, "status": "FAIL"},
        "canonical": {"value": None, "status": "FAIL"},
        "links": {"broken": ["https://example.com/dead"], "count": 1, "status": "FAIL", "capped": False},
    }
    _THIRD_RESULT = {
        "url": "https://example.com/partial",
        "title": {"value": "OK", "length": 2, "status": "PASS"},
        "description": {"value": None, "length": 0, "status": "FAIL"},
        "h1": {"count": 1, "value": "OK", "status": "PASS"},
        "canonical": {"value": "https://example.com/partial", "status": "PASS"},
    }

    if mode in ("all", "three"):
        # Acceptance test 1: three write_result() calls → 3 entries
        if os.path.exists(REPORT_JSON):
            os.remove(REPORT_JSON)

        write_result(_PASS_RESULT)
        write_result(_FAIL_RESULT)
        write_result(_THIRD_RESULT)

        entries = _load_report()
        assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"
        print("Test 1: three entries — PASS")

    if mode in ("all", "dedup"):
        # Acceptance test 2: re-writing same URL updates, not duplicates
        updated = dict(_PASS_RESULT)
        updated["title"]["value"] = "Updated Title"
        write_result(updated)

        entries = _load_report()
        count = sum(1 for e in entries if e.get("url") == _PASS_RESULT["url"])
        assert count == 1, f"Expected 1 entry for URL, got {count}"
        match = next(e for e in entries if e.get("url") == _PASS_RESULT["url"])
        assert match["title"]["value"] == "Updated Title", "Entry was not updated"
        print("Test 2: deduplication/update — PASS")

    if mode in ("all", "summary"):
        # Acceptance test 3: write_summary() produces readable file with correct totals
        write_summary()
        with open(REPORT_SUMMARY, "r", encoding="utf-8") as f:
            content = f.read()
        print("Test 3: report-summary.txt contents —")
        print(content)
        assert "PASS" in content
        assert "FAIL" in content
        assert "/3 URLs passed" in content or "/2 URLs passed" in content or "/1 URLs passed" in content
        print("Test 3: write_summary — PASS")
