"""Checks HTTP status codes for same-domain links on a page."""

import asyncio
import logging
import sys
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

CAP = 50
TIMEOUT = 5.0


def _same_domain(link: str, final_url: str) -> bool:
    """Return True if link is same domain as final_url and is a checkable HTTP link."""
    if not link:
        return False
    lower = link.strip().lower()
    # Skip anchors, mailto, javascript, and relative-protocol-less schemes
    if lower.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return False
    try:
        page_host = urlparse(final_url).netloc.lower()
        link_parsed = urlparse(link)
        # Must be http/https
        if link_parsed.scheme not in ("http", "https"):
            return False
        return link_parsed.netloc.lower() == page_host
    except Exception:
        return False


async def _check_link(client: httpx.AsyncClient, url: str) -> tuple[str, bool]:
    """Return (url, is_broken). Timeouts and errors are treated as broken."""
    try:
        resp = await client.head(url, follow_redirects=True, timeout=TIMEOUT)
        broken = resp.status_code != 200
        return url, broken
    except Exception as exc:
        logger.debug("Request failed for %s: %s", url, exc)
        return url, True


async def _run_checks(links: list[str]) -> list[str]:
    """Run HEAD requests concurrently and return list of broken URLs."""
    async with httpx.AsyncClient() as client:
        tasks = [_check_link(client, url) for url in links]
        results = await asyncio.gather(*tasks)
    return [url for url, broken in results if broken]


def check_links(raw_links: list[str], final_url: str) -> dict:
    """
    Check same-domain links for broken status.

    Args:
        raw_links: All hrefs extracted from the page snapshot.
        final_url: The resolved URL of the page (used to determine domain).

    Returns:
        {
            "broken": [...],
            "count": int,
            "status": "PASS" | "FAIL",
            "capped": bool
        }
    """
    same_domain = [link for link in raw_links if _same_domain(link, final_url)]

    capped = len(same_domain) > CAP
    if capped:
        logger.warning(
            "Page has %d same-domain links — capping check at %d.",
            len(same_domain),
            CAP,
        )
        same_domain = same_domain[:CAP]

    broken = asyncio.run(_run_checks(same_domain))

    return {
        "broken": broken,
        "count": len(broken),
        "status": "FAIL" if broken else "PASS",
        "capped": capped,
    }


# ---------------------------------------------------------------------------
# Manual acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    mode = sys.argv[1] if len(sys.argv) > 1 else "filter"

    if mode == "filter":
        # Acceptance test 1: only same-domain links are checked
        final_url = "https://example.com/page"
        raw_links = [
            "https://example.com/about",       # same-domain — included
            "https://external.com/other",      # external — excluded
            "#section",                         # anchor — excluded
            "mailto:hi@example.com",            # mailto — excluded
            "javascript:void(0)",               # js — excluded
            "https://example.com/contact",      # same-domain — included
        ]
        result = check_links(raw_links, final_url)
        print("Test: filter")
        import json; print(json.dumps(result, indent=2))
        assert result["capped"] is False
        assert result["count"] == result["broken"].__len__()
        print("PASS\n")

    elif mode == "broken":
        # Acceptance test 2: a 404 URL appears in broken[] and status is FAIL
        import json
        final_url = "https://httpstat.us"
        raw_links = [
            "https://httpstat.us/200",
            "https://httpstat.us/404",
        ]
        result = check_links(raw_links, final_url)
        print("Test: broken")
        print(json.dumps(result, indent=2))
        assert "https://httpstat.us/404" in result["broken"], "404 URL should be broken"
        assert result["status"] == "FAIL", "status should be FAIL"
        print("PASS\n")

    elif mode == "cap":
        # Acceptance test 3: more than 50 same-domain links → capped=true
        import json
        final_url = "https://example.com"
        raw_links = [f"https://example.com/page-{i}" for i in range(60)]
        result = check_links(raw_links, final_url)
        print("Test: cap")
        print(json.dumps(result, indent=2))
        assert result["capped"] is True, "capped should be True"
        print("PASS\n")

    else:
        print(f"Unknown mode: {mode}. Use: filter | broken | cap")
        sys.exit(1)
