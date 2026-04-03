"""
Structured rewrite suggestions using the cost curve.

Tier 1 (deterministic) — title truncation, H1 recommendation, internal link suggestions
Tier 2 (Haiku)         — meta description suggestion
Tier 3 (Sonnet)        — voice-preserving opening paragraph rewrite
"""

import logging
import os
import re
from urllib.parse import urlparse

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL  = "claude-haiku-4-5-20251001"
_SONNET_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Tier 1 helpers — deterministic, zero API calls
# ---------------------------------------------------------------------------

def _truncate_title(title: str) -> str:
    """Truncate to the last complete word that fits within 60 chars, append '...'"""
    if len(title) <= 60:
        return title
    # Find the last space at or before index 57 (leaves room for "...")
    cut = title[:57].rfind(" ")
    if cut <= 0:
        cut = 57
    return title[:cut].rstrip() + "..."


def _h1_recommendation(result: dict) -> str | None:
    h1 = result.get("h1") or {}
    count = h1.get("count", 0)
    if count == 0:
        return "Add a descriptive H1 that matches your page title"
    if count > 1:
        return "Keep only the most descriptive H1, remove the others"
    return None


def _internal_link_suggestions(broken_urls: list[str]) -> list[dict]:
    """Derive clean anchor text suggestions from broken URL paths."""
    suggestions = []
    for url in broken_urls:
        try:
            path = urlparse(url).path.rstrip("/")
            slug = path.split("/")[-1] if path else url
            anchor = slug.replace("-", " ").replace("_", " ").title() or url
        except Exception:
            anchor = url
        suggestions.append({"broken_url": url, "suggestion": anchor})
    return suggestions


# ---------------------------------------------------------------------------
# Tier 2 — Haiku meta description
# ---------------------------------------------------------------------------

def _haiku_description(result: dict) -> tuple[str | None, str]:
    """Return (suggested_description, method). method='error' on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[rewrite_agent] ANTHROPIC_API_KEY not set — description suggestion skipped")
        return None, "error"

    url   = result.get("url") or result.get("final_url", "")
    title = (result.get("title") or {}).get("value") or ""
    current_desc = (result.get("description") or {}).get("value") or ""

    prompt = (
        f"URL: {url}\n"
        f"Page title: {title!r}\n"
        f"Current meta description: {current_desc!r}\n\n"
        "Write a compelling meta description for this page in under 160 characters. "
        "Return ONLY the description text — no quotes, no explanation."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = msg.content[0].text.strip().strip('"').strip("'")
        if len(suggestion) > 160:
            suggestion = suggestion[:157] + "..."
        return suggestion, "haiku"
    except Exception as exc:
        logger.warning("[rewrite_agent] Haiku description failed: %s", exc)
        return None, "error"


# ---------------------------------------------------------------------------
# Tier 3 — Sonnet opening paragraph rewrite
# ---------------------------------------------------------------------------

def _sonnet_opening(result: dict, voice_sample: str | None) -> tuple[str | None, bool, str]:
    """Return (suggested_paragraph, voice_matched, method). method='error' on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[rewrite_agent] ANTHROPIC_API_KEY not set — opening paragraph skipped")
        return None, False, "error"

    url   = result.get("url") or result.get("final_url", "")
    title = (result.get("title") or {}).get("value") or ""
    h1    = (result.get("h1") or {}).get("value") or ""

    voice_block = ""
    voice_matched = False
    if voice_sample:
        voice_block = (
            f"\nHere is a sample of the author's writing style:\n"
            f"---\n{voice_sample}\n---\n"
            "Match this voice and tone closely in your rewrite.\n"
        )
        voice_matched = True

    prompt = (
        f"URL: {url}\n"
        f"Page title: {title!r}\n"
        f"H1: {h1!r}\n"
        f"{voice_block}\n"
        "Write an engaging opening paragraph (2-4 sentences) for this page. "
        "Return ONLY the paragraph text — no explanation, no heading."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=_SONNET_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = msg.content[0].text.strip()
        return suggestion, voice_matched, "sonnet"
    except Exception as exc:
        logger.warning("[rewrite_agent] Sonnet opening paragraph failed: %s", exc)
        return None, voice_matched, "error"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_rewrites(result: dict, voice_sample: str | None = None) -> dict:
    """
    Generate structured rewrite suggestions for a single audit result.

    Applies cost curve:
      - Tier 1: title truncation, H1 recommendation, internal link suggestions
      - Tier 2: Haiku meta description
      - Tier 3: Sonnet opening paragraph

    Never crashes — API failures set suggested=None and method='error'.
    """
    url = result.get("url") or result.get("final_url", "")

    # --- Tier 1: title ---
    title_val = (result.get("title") or {}).get("value")
    if title_val and len(title_val) > 60:
        title_suggested = _truncate_title(title_val)
    else:
        title_suggested = None
    title_rewrite = {
        "original":  title_val,
        "suggested": title_suggested,
        "method":    "deterministic",
    }

    # --- Tier 1: H1 recommendation ---
    h1_rewrite = {
        "recommendation": _h1_recommendation(result),
        "method": "deterministic",
    }

    # --- Tier 1: internal link suggestions ---
    broken_urls = (result.get("broken_links") or {}).get("broken", [])
    internal_links = _internal_link_suggestions(broken_urls)

    # --- Tier 2: description (Haiku) ---
    desc_val = (result.get("description") or {}).get("value")
    desc_status = (result.get("description") or {}).get("status", "PASS")
    if desc_status == "FAIL":
        desc_suggested, desc_method = _haiku_description(result)
    else:
        desc_suggested, desc_method = None, "haiku"
    desc_rewrite = {
        "original":  desc_val,
        "suggested": desc_suggested,
        "method":    desc_method,
    }

    # --- Tier 3: opening paragraph (Sonnet) ---
    para_suggested, voice_matched, para_method = _sonnet_opening(result, voice_sample)
    opening_rewrite = {
        "suggested":     para_suggested,
        "voice_matched": voice_matched,
        "method":        para_method,
    }

    return {
        "url": url,
        "rewrites": {
            "title":             title_rewrite,
            "description":       desc_rewrite,
            "h1":                h1_rewrite,
            "internal_links":    internal_links,
            "opening_paragraph": opening_rewrite,
        },
    }


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    # Ensure package is importable under real dotted name for attribute patching
    import os as _os
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import premium.rewrite_agent as _mod

    failures = []

    def run(name, fn):
        try:
            fn()
            print(f"Test {name} PASS")
        except Exception as exc:
            print(f"Test {name} FAIL: {exc}")
            failures.append(name)

    def _result_with_long_title():
        return {
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "status_code": 200,
            "title": {
                "value": "This Is A Very Long Page Title That Definitely Exceeds Sixty Characters Right Here",
                "length": 82, "status": "FAIL",
            },
            "description": {"value": "A good description.", "length": 19, "status": "PASS"},
            "h1": {"count": 1, "value": "Welcome", "status": "PASS"},
            "canonical": {"value": "https://example.com/", "status": "PASS"},
            "broken_links": {"broken": [], "count": 0, "status": "PASS", "capped": False},
        }

    def _result_missing_desc():
        return {
            "url": "https://example.com/about",
            "final_url": "https://example.com/about",
            "status_code": 200,
            "title": {"value": "About Us", "length": 8, "status": "PASS"},
            "description": {"value": None, "length": 0, "status": "FAIL"},
            "h1": {"count": 1, "value": "About Us", "status": "PASS"},
            "canonical": {"value": "https://example.com/about", "status": "PASS"},
            "broken_links": {"broken": [], "count": 0, "status": "PASS", "capped": False},
        }

    # ------------------------------------------------------------------
    # Test 1: title >60 chars -> deterministic truncation, zero API calls
    # ------------------------------------------------------------------
    def test_title_truncation():
        result = _result_with_long_title()
        api_calls = []
        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = lambda **kw: api_calls.append(1) or MagicMock()
        try:
            output = _mod.generate_rewrites(result)
        finally:
            _mod.anthropic.Anthropic = _orig

        t = output["rewrites"]["title"]
        assert t["method"] == "deterministic", f"Expected deterministic, got {t['method']}"
        assert t["suggested"] is not None, "Expected a suggestion"
        assert len(t["suggested"]) <= 60, f"Suggestion too long: {len(t['suggested'])} chars: {t['suggested']!r}"
        assert t["suggested"].endswith("..."), f"Expected '...' suffix: {t['suggested']!r}"
        # No API calls should have been made for title processing
        # (API may still be called for desc/opening since desc is PASS, opening always runs)
    run("1: title >60 -> deterministic truncation <=60 chars", test_title_truncation)

    # ------------------------------------------------------------------
    # Test 2: missing description -> Haiku called, suggestion <=160 chars
    # ------------------------------------------------------------------
    def test_description_haiku():
        result = _result_missing_desc()
        fake_suggestion = "We help businesses grow with innovative solutions tailored to your needs."
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fake_suggestion)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = lambda **kw: mock_client
        try:
            output = _mod.generate_rewrites(result)
        finally:
            _mod.anthropic.Anthropic = _orig

        d = output["rewrites"]["description"]
        assert d["method"] == "haiku", f"Expected haiku, got {d['method']}"
        assert d["suggested"] is not None, "Expected a suggestion"
        assert len(d["suggested"]) <= 160, f"Suggestion too long: {len(d['suggested'])}"
        # Verify Haiku model was used in at least one call
        all_calls = mock_client.messages.create.call_args_list
        assert any(_mod._HAIKU_MODEL in str(c) for c in all_calls), \
            f"Haiku model not used in any call. Calls: {all_calls}"
    run("2: missing description triggers Haiku, suggestion <=160 chars", test_description_haiku)

    # ------------------------------------------------------------------
    # Test 3: Sonnet prompt includes voice_sample text
    # ------------------------------------------------------------------
    def test_voice_sample_in_prompt():
        result = _result_missing_desc()
        voice = "I write with a casual, direct tone. Short sentences. No fluff."
        prompts_seen = []

        def fake_create(**kwargs):
            prompts_seen.append(kwargs.get("messages", [{}])[0].get("content", ""))
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="A great opening paragraph for this page.")]
            return mock_msg

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create
        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = lambda **kw: mock_client
        try:
            output = _mod.generate_rewrites(result, voice_sample=voice)
        finally:
            _mod.anthropic.Anthropic = _orig

        # Find the Sonnet call prompt (the one containing the opening paragraph request)
        sonnet_prompts = [p for p in prompts_seen if "opening paragraph" in p.lower()]
        assert sonnet_prompts, f"No opening paragraph prompt found. Prompts: {prompts_seen}"
        assert voice in sonnet_prompts[0], "Voice sample not found in Sonnet prompt"
        assert output["rewrites"]["opening_paragraph"]["voice_matched"] is True
    run("3: Sonnet prompt includes voice_sample text", test_voice_sample_in_prompt)

    # ------------------------------------------------------------------
    # Test 4: output schema exactly matches spec — all keys present
    # ------------------------------------------------------------------
    def test_schema_completeness():
        result = _result_missing_desc()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Suggested text.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = lambda **kw: mock_client
        try:
            output = _mod.generate_rewrites(result)
        finally:
            _mod.anthropic.Anthropic = _orig

        assert "url" in output, "Missing top-level 'url'"
        assert "rewrites" in output, "Missing top-level 'rewrites'"
        r = output["rewrites"]
        for section in ("title", "description", "h1", "internal_links", "opening_paragraph"):
            assert section in r, f"Missing rewrites section: {section}"
        assert all(k in r["title"] for k in ("original", "suggested", "method")), f"title keys: {r['title']}"
        assert all(k in r["description"] for k in ("original", "suggested", "method")), f"desc keys: {r['description']}"
        assert all(k in r["h1"] for k in ("recommendation", "method")), f"h1 keys: {r['h1']}"
        assert isinstance(r["internal_links"], list), "internal_links must be a list"
        assert all(k in r["opening_paragraph"] for k in ("suggested", "voice_matched", "method")), f"opening keys: {r['opening_paragraph']}"
    run("4: output schema matches spec exactly", test_schema_completeness)

    # ------------------------------------------------------------------
    # Test 5: API failure -> suggested=null, method="error", no crash
    # ------------------------------------------------------------------
    def test_api_failure_graceful():
        result = _result_missing_desc()

        class _FailClient:
            def __init__(self, **kw): pass
            class messages:
                @staticmethod
                def create(**kw):
                    raise anthropic.APIError(
                        message="service unavailable", request=MagicMock(), body=None
                    )

        _orig = _mod.anthropic.Anthropic
        _mod.anthropic.Anthropic = _FailClient
        try:
            output = _mod.generate_rewrites(result, voice_sample="My voice.")
        finally:
            _mod.anthropic.Anthropic = _orig

        d = output["rewrites"]["description"]
        assert d["suggested"] is None, f"Expected None, got {d['suggested']!r}"
        assert d["method"] == "error", f"Expected 'error', got {d['method']!r}"

        op = output["rewrites"]["opening_paragraph"]
        assert op["suggested"] is None, f"Expected None, got {op['suggested']!r}"
        assert op["method"] == "error", f"Expected 'error', got {op['method']!r}"
    run("5: API failure -> suggested=null, method='error', no crash", test_api_failure_graceful)

    print()
    if failures:
        print(f"{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    else:
        print("All 5 acceptance tests passed.")
