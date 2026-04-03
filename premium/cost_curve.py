"""
Three-tier routing logic based on Pascal's cost curve recommendation.

Tier 1 — deterministic, zero API calls
Tier 2 — Haiku, for ambiguous/borderline cases
Tier 3 — Sonnet via core.extractor, for full semantic analysis
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_REDIRECT_CODES = {301, 302, 307, 308}

_HAIKU_SYSTEM = """\
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
  "needs_tier3": false,
  "audited_at": "ISO 8601 timestamp"
}

PASS/FAIL rules:
- title: FAIL if null/empty OR length > 60 characters
- description: FAIL if null/empty OR length > 160 characters
- h1: FAIL if count == 0 OR count > 1
- canonical: FAIL if null/empty
- flags: one entry per failing field, describing the specific issue
- human_review: set to true if status_code >= 400 or any field is ERROR
- needs_tier3: set to true ONLY if you detect semantic ambiguity that requires \
deeper analysis (e.g. title or description is present but content seems misleading, \
stuffed with keywords, or duplicated across fields). Set false for clear PASS/FAIL cases.
- audited_at: current UTC time in ISO 8601 format

Return ONLY the JSON object. No other text.\
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _base_result(snapshot: dict) -> dict:
    """Skeleton result populated with snapshot metadata."""
    return {
        "url": snapshot.get("final_url") or snapshot.get("url", ""),
        "final_url": snapshot.get("final_url") or snapshot.get("url", ""),
        "status_code": snapshot.get("status_code"),
        "flags": [],
        "human_review": False,
        "audited_at": _now(),
    }


# ---------------------------------------------------------------------------
# Tier 1 — deterministic, zero API calls
# ---------------------------------------------------------------------------

def tier1_check(snapshot: dict) -> dict:
    """Pure-Python SEO audit. No API calls."""
    result = _base_result(snapshot)
    flags = []

    # Title
    title_val = snapshot.get("title") or ""
    title_len = len(title_val)
    if not title_val:
        t_status = "FAIL"
        flags.append("Title is missing")
    elif title_len > 60:
        t_status = "FAIL"
        flags.append(f"Title is too long ({title_len} chars, max 60)")
    else:
        t_status = "PASS"
    result["title"] = {"value": title_val or None, "length": title_len, "status": t_status}

    # Description
    desc_val = snapshot.get("meta_description") or ""
    desc_len = len(desc_val)
    if not desc_val:
        d_status = "FAIL"
        flags.append("Meta description is missing")
    elif desc_len > 160:
        d_status = "FAIL"
        flags.append(f"Meta description is too long ({desc_len} chars, max 160)")
    else:
        d_status = "PASS"
    result["description"] = {"value": desc_val or None, "length": desc_len, "status": d_status}

    # H1
    h1s = snapshot.get("h1s") or []
    h1_count = len(h1s)
    h1_val = h1s[0] if h1s else None
    if h1_count == 0:
        h1_status = "FAIL"
        flags.append("No H1 tag found")
    elif h1_count > 1:
        h1_status = "FAIL"
        flags.append(f"Multiple H1 tags found ({h1_count})")
    else:
        h1_status = "PASS"
    result["h1"] = {"count": h1_count, "value": h1_val, "status": h1_status}

    # Canonical
    canonical_val = snapshot.get("canonical") or ""
    if not canonical_val:
        c_status = "FAIL"
        flags.append("Canonical tag is missing")
    else:
        c_status = "PASS"
    result["canonical"] = {"value": canonical_val or None, "status": c_status}

    result["flags"] = flags
    result["human_review"] = bool(
        (snapshot.get("status_code") or 0) >= 400 or
        any(f.get("status") == "ERROR" for f in [
            result["title"], result["description"], result["h1"], result["canonical"]
        ])
    )
    result["method"] = "deterministic"
    return result


# ---------------------------------------------------------------------------
# Tier 2 — Haiku for ambiguous cases
# ---------------------------------------------------------------------------

def _needs_tier2(snapshot: dict) -> bool:
    """Return True when borderline conditions warrant a Haiku call."""
    title_val = snapshot.get("title") or ""
    desc_val = snapshot.get("meta_description") or ""
    code = snapshot.get("status_code")

    if title_val and len(title_val) < 10:
        return True
    if desc_val and len(desc_val) < 50:
        return True
    if code in _REDIRECT_CODES:
        return True
    return False


def tier2_check(snapshot: dict, _fallback: dict | None = None) -> dict:
    """Haiku-powered audit for ambiguous cases. Falls back to _fallback on error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[tier2] ANTHROPIC_API_KEY not set — falling back to Tier 1 result")
        return _fallback if _fallback is not None else tier1_check(snapshot)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(snapshot)

    try:
        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=1024,
            system=_HAIKU_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        logger.warning("[tier2] Haiku API error: %s — falling back to Tier 1 result", exc)
        return _fallback if _fallback is not None else tier1_check(snapshot)

    raw = message.content[0].text
    cleaned = _strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("[tier2] JSON parse error: %s — falling back to Tier 1 result", exc)
        return _fallback if _fallback is not None else tier1_check(snapshot)

    result["method"] = "haiku"
    return result


# ---------------------------------------------------------------------------
# Tier 3 — Sonnet via core.extractor
# ---------------------------------------------------------------------------

def tier3_check(snapshot: dict, _fallback: dict | None = None) -> dict:
    """Full Sonnet audit. Falls back to _fallback on error."""
    # Import here to keep premium/ decoupled at module load time
    try:
        from core.extractor import extract
    except ImportError:
        # Support running from inside seo-agent/ root
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.extractor import extract

    try:
        result = extract(snapshot)
    except Exception as exc:
        logger.warning("[tier3] Sonnet error: %s — falling back to Tier 2 result", exc)
        return _fallback if _fallback is not None else tier1_check(snapshot)

    result["method"] = "sonnet"
    return result


# ---------------------------------------------------------------------------
# Routing entry point
# ---------------------------------------------------------------------------

def audit_url(snapshot: dict, tiered: bool = False) -> dict:
    """
    Route snapshot through the appropriate tier.

    tiered=False ->direct Sonnet call (core.extractor.extract)
    tiered=True  ->Tier 1 ->Tier 2 (if ambiguous) ->Tier 3 (if needs_tier3)
    """
    if not tiered:
        return tier3_check(snapshot)

    # Tier 1
    t1 = tier1_check(snapshot)
    all_pass = all(
        t1.get(f, {}).get("status") == "PASS"
        for f in ("title", "description", "h1", "canonical")
    )

    if all_pass and not _needs_tier2(snapshot):
        return t1

    # Tier 2
    t2 = tier2_check(snapshot, _fallback=t1)

    if t2.get("needs_tier3"):
        return tier3_check(snapshot, _fallback=t2)

    return t2


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys
    import types as _types
    from unittest.mock import MagicMock

    # Ensure the package is importable under its real dotted name so that
    # attribute-level monkey-patching works regardless of run mode.
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

    import premium.cost_curve as _mod  # re-import under real name

    logging.basicConfig(level=logging.WARNING)
    _failures = []

    def _clean_snapshot():
        return {
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "status_code": 200,
            "title": "Example Domain — A Good Page",
            "meta_description": "This is a well-written meta description under one hundred and sixty characters.",
            "h1s": ["Welcome to Example"],
            "canonical": "https://example.com/",
        }

    # -- Test 1: clean page ->Tier 1, method=deterministic, zero API calls --------
    def test_clean_returns_tier1():
        snap = _clean_snapshot()
        _orig = _mod.anthropic.Anthropic
        calls = []
        _mod.anthropic.Anthropic = lambda **kw: calls.append(1) or MagicMock()
        try:
            result = _mod.audit_url(snap, tiered=True)
        finally:
            _mod.anthropic.Anthropic = _orig
        assert result["method"] == "deterministic", f"Expected deterministic, got {result['method']}"
        assert len(calls) == 0, "API was called but should not have been"
        print("Test 1 PASS: clean page returns Tier 1 result, zero API calls")

    # -- Test 2: title >60 ->FAIL from Tier 1, no API call -----------------------
    def test_long_title_fails_tier1():
        snap = _clean_snapshot()
        snap["title"] = "A" * 61
        result = _mod.tier1_check(snap)
        assert result["title"]["status"] == "FAIL", "Expected title FAIL"
        assert result["method"] == "deterministic"
        print("Test 2 PASS: title >60 chars ->FAIL from Tier 1, no API call")

    # -- Test 3: short title ->escalates to Tier 2 --------------------------------
    def test_short_title_escalates_to_tier2():
        snap = _clean_snapshot()
        snap["title"] = "Hi"  # len=2, triggers _needs_tier2
        fake_json = json.dumps({
            "url": snap["url"], "final_url": snap["final_url"],
            "status_code": 200,
            "title": {"value": "Hi", "length": 2, "status": "PASS"},
            "description": {"value": "desc", "length": 4, "status": "PASS"},
            "h1": {"count": 1, "value": "Welcome to Example", "status": "PASS"},
            "canonical": {"value": snap["canonical"], "status": "PASS"},
            "flags": [], "human_review": False, "needs_tier3": False,
            "audited_at": "2026-01-01T00:00:00+00:00",
        })
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fake_json)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = lambda **kw: mock_client
        try:
            result = _mod.audit_url(snap, tiered=True)
        finally:
            _mod.anthropic.Anthropic = _orig
        assert result["method"] == "haiku", f"Expected haiku, got {result['method']}"
        assert mock_client.messages.create.call_count == 1, "Expected exactly one Haiku call"
        print("Test 3 PASS: short title escalates to Tier 2 (Haiku)")

    # -- Test 4: tiered=False ->delegates to tier3_check directly -----------------
    def test_tiered_false_calls_sonnet():
        snap = _clean_snapshot()
        calls = []
        mock_result = {"method": "sonnet", "title": {"status": "PASS"}}
        _orig = _mod.tier3_check
        _mod.tier3_check = lambda s, **kw: calls.append(s) or mock_result
        try:
            result = _mod.audit_url(snap, tiered=False)
        finally:
            _mod.tier3_check = _orig
        assert len(calls) == 1 and calls[0] is snap, "tier3_check not called with snapshot"
        assert result["method"] == "sonnet"
        print("Test 4 PASS: tiered=False delegates to tier3_check (Sonnet)")

    # -- Test 5: Haiku API failure ->fallback to Tier 1 result with warning -------
    def test_haiku_failure_falls_back():
        import io
        snap = _clean_snapshot()
        snap["title"] = "Hi"
        t1_result = _mod.tier1_check(snap)

        class _FakeClient:
            def __init__(self, **kw): pass
            class messages:
                @staticmethod
                def create(**kw):
                    raise anthropic.APIError(
                        message="rate limit", request=MagicMock(), body=None
                    )

        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = _FakeClient

        # Capture warnings via a temporary handler on the named logger
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.WARNING)
        mod_logger = logging.getLogger("premium.cost_curve")
        mod_logger.addHandler(handler)
        mod_logger.setLevel(logging.WARNING)
        try:
            result = _mod.tier2_check(snap, _fallback=t1_result)
        finally:
            _mod.anthropic.Anthropic = _orig
            mod_logger.removeHandler(handler)

        output = log_stream.getvalue()
        assert result["method"] == "deterministic", f"Expected fallback deterministic, got {result['method']}"
        assert "Haiku API error" in output, f"No warning logged. Output: {output!r}"
        print("Test 5 PASS: Haiku failure falls back to Tier 1 with warning")

    for test_fn in [
        test_clean_returns_tier1,
        test_long_title_fails_tier1,
        test_short_title_escalates_to_tier2,
        test_tiered_false_calls_sonnet,
        test_haiku_failure_falls_back,
    ]:
        try:
            test_fn()
        except Exception as exc:
            print(f"FAIL {test_fn.__name__}: {exc}")
            _failures.append(test_fn.__name__)

    print()
    if _failures:
        print(f"{len(_failures)} test(s) failed: {_failures}")
        _sys.exit(1)
    else:
        print("All 5 acceptance tests passed.")
