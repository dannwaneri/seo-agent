"""
Acceptance tests for Task 1 — fetch_page() new fields.
Run from the project root: python test_task1.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.browser import fetch_page

REQUIRED_FIELDS = [
    "final_url", "status_code", "title", "meta_description",
    "h1s", "canonical", "raw_links", "json_ld_blocks", "raw_html",
]

PASS = "[PASS]"
FAIL = "[FAIL]"


def check_fields(result: dict, label: str) -> bool:
    ok = True
    missing = [f for f in REQUIRED_FIELDS if f not in result]
    if missing:
        print(f"  {FAIL} Missing fields: {missing}")
        ok = False
    else:
        print(f"  {PASS} All required fields present")
    return ok


# ── Test 1: page with JSON-LD ──────────────────────────────────────────────
print("\n=== Test 1: https://dev.to/dannwaneri (expects JSON-LD) ===")
r1 = fetch_page("https://dev.to/dannwaneri")

fields_ok = check_fields(r1, "dev.to")

blocks = r1.get("json_ld_blocks", [])
if blocks:
    print(f"  {PASS} json_ld_blocks populated — {len(blocks)} block(s) found")
    print(f"         First block preview: {blocks[0][:120].strip()}...")
else:
    print(f"  {FAIL} json_ld_blocks is empty — expected JSON-LD on dev.to")

raw_html = r1.get("raw_html", "")
if raw_html:
    print(f"  {PASS} raw_html populated ({len(raw_html):,} chars)")
else:
    print(f"  {FAIL} raw_html is empty")

print(f"  status_code : {r1['status_code']}")
print(f"  final_url   : {r1['final_url']}")
print(f"  title       : {r1['title']}")

# ── Test 2: page without JSON-LD ───────────────────────────────────────────
print("\n=== Test 2: https://httpbin.org/html (expects no JSON-LD) ===")
r2 = fetch_page("https://httpbin.org/html")

check_fields(r2, "httpbin")

blocks2 = r2.get("json_ld_blocks", "MISSING")
if blocks2 == []:
    print(f"  {PASS} json_ld_blocks=[] as expected")
elif blocks2 == "MISSING":
    print(f"  {FAIL} json_ld_blocks key not present")
else:
    print(f"  {FAIL} json_ld_blocks unexpectedly non-empty: {blocks2}")

raw_html2 = r2.get("raw_html", "")
if raw_html2:
    print(f"  {PASS} raw_html populated ({len(raw_html2):,} chars)")
else:
    print(f"  {FAIL} raw_html is empty (httpbin/html is a 200 page)")

print(f"  status_code : {r2['status_code']}")
print(f"  final_url   : {r2['final_url']}")

print("\n=== Done ===\n")
