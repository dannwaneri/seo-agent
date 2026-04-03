import json
import os
import re
import sys
from datetime import datetime, timezone

import anthropic

MODEL = "claude-sonnet-4-20250514"

_SYSTEM = """\
You are an SEO auditor. Given a page snapshot, return ONLY a JSON object — \
no prose, no markdown fences, no explanation — that matches this exact schema:

{
  "url": "string",
  "final_url": "string",
  "status_code": 200,
  "title": { "value": "string or null", "length": 0, "status": "PASS or FAIL" },
  "description": { "value": "string or null", "length": 0, "status": "PASS or FAIL" },
  "h1": { "count": 0, "value": "string or null", "status": "PASS or FAIL" },
  "canonical": { "value": "string or null", "status": "PASS or FAIL" },
  "flags": ["array of string descriptions of issues"],
  "human_review": false,
  "audited_at": "ISO 8601 timestamp"
}

PASS/FAIL rules:
- title: FAIL if null/empty OR length > 60 characters
- description: FAIL if null/empty OR length > 160 characters
- h1: FAIL if count == 0 OR count > 1
- canonical: FAIL if null/empty
- flags: one entry per failing field, describing the specific issue
- human_review: set to true if status_code >= 400 or any field is ERROR
- audited_at: current UTC time in ISO 8601 format

Return ONLY the JSON object. No other text.\
"""


def _build_prompt(snapshot: dict) -> str:
    h1s = snapshot.get("h1s") or []
    return (
        f"URL: {snapshot.get('final_url') or snapshot.get('url')}\n"
        f"Status code: {snapshot.get('status_code')}\n"
        f"Title: {snapshot.get('title')!r}\n"
        f"Meta description: {snapshot.get('meta_description')!r}\n"
        f"H1 tags ({len(h1s)} found): {h1s}\n"
        f"Canonical: {snapshot.get('canonical')!r}\n"
    )


def _strip_fences(text: str) -> str:
    """Remove accidental ```json ... ``` or ``` ... ``` wrappers."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _error_result(snapshot: dict, reason: str) -> dict:
    return {
        "url": snapshot.get("final_url") or snapshot.get("url", ""),
        "final_url": snapshot.get("final_url") or snapshot.get("url", ""),
        "status_code": snapshot.get("status_code"),
        "title": {"value": None, "length": 0, "status": "ERROR"},
        "description": {"value": None, "length": 0, "status": "ERROR"},
        "h1": {"count": 0, "value": None, "status": "ERROR"},
        "canonical": {"value": None, "status": "ERROR"},
        "flags": [f"Extractor error: {reason}"],
        "human_review": True,
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }


def extract(snapshot: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Export it in your shell before running this script."
        )

    client = anthropic.Anthropic(api_key=api_key)

    prompt = _build_prompt(snapshot)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        print(f"[extractor] Claude API error: {exc}", file=sys.stderr)
        return _error_result(snapshot, str(exc))

    raw = message.content[0].text
    cleaned = _strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"[extractor] JSON parse error: {exc}\nRaw response:\n{raw}", file=sys.stderr)
        return _error_result(snapshot, f"Invalid JSON from Claude: {exc}")

    return result


# ---------------------------------------------------------------------------
# Manual acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "real"

    if mode == "test-missing":
        snapshot = {
            "final_url": "https://example.com/test",
            "status_code": 200,
            "title": None,
            "meta_description": "A short description.",
            "h1s": ["Hello world"],
            "canonical": None,
        }
    elif mode == "test-fences":
        fenced = '```json\n{"url":"https://x.com","final_url":"https://x.com","status_code":200,"title":{"value":"X","length":1,"status":"PASS"},"description":{"value":"desc","length":4,"status":"PASS"},"h1":{"count":1,"value":"X","status":"PASS"},"canonical":{"value":"https://x.com","status":"PASS"},"flags":[],"human_review":false,"audited_at":"2026-01-01T00:00:00+00:00"}\n```'
        stripped = _strip_fences(fenced)
        result = json.loads(stripped)
        print(json.dumps(result, indent=2))
        sys.exit(0)
    else:
        snapshot = {
            "final_url": "https://dev.to/dannwaneri",
            "status_code": 200,
            "title": "Daniel Nwaneri - DEV Community",
            "meta_description": "Full-stack developer specializing in Cloudflare Workers, MCP (Model Context Protocol), and AI integration.",
            "h1s": ["Daniel Nwaneri"],
            "canonical": "https://dev.to/dannwaneri",
        }

    result = extract(snapshot)
    print(json.dumps(result, indent=2, ensure_ascii=False))
