"""Human-in-the-loop: pauses execution and prompts for manual review."""

import sys

from state import load_state, save_state

_REDIRECT_CODES = {301, 302, 307, 308}
_LOGIN_KEYWORDS = {"login", "sign in", "access denied", "log in", "signin"}


def _contains_login_keyword(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _LOGIN_KEYWORDS)


def should_pause(snapshot: dict) -> bool:
    """Return True if the page needs human review."""
    code = snapshot.get("status_code")

    # Navigation failed entirely
    if code is None:
        return True

    # Non-200, non-redirect status
    if code != 200 and code not in _REDIRECT_CODES:
        return True

    # Login-gated detection
    title = snapshot.get("title") or ""
    if _contains_login_keyword(title):
        return True

    for h1 in snapshot.get("h1s") or []:
        if _contains_login_keyword(h1):
            return True

    return False


def pause_and_prompt(url: str, reason: str) -> str:
    """
    Print the URL and reason, then prompt the user for action.

    Returns one of: "skip", "retry", "quit"
    """
    print(f"\n[HUMAN REVIEW REQUIRED]")
    print(f"  URL   : {url}")
    print(f"  Reason: {reason}")
    print()

    while True:
        raw = input("  Action — s=skip  r=retry  q=quit: ").strip().lower()
        if raw == "s":
            return "skip"
        if raw == "r":
            return "retry"
        if raw == "q":
            return "quit"
        print("  Invalid input. Please enter s, r, or q.")


def add_to_human_review(url: str) -> None:
    """Add url to needs_human[] in state.json if not already present."""
    state = load_state()
    if url not in state["needs_human"]:
        state["needs_human"].append(url)
        save_state(state)


# ---------------------------------------------------------------------------
# Manual acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    mode = sys.argv[1] if len(sys.argv) > 1 else "pause"

    if mode == "pause":
        # Acceptance test 1: should_pause() logic
        clean = {"status_code": 200, "title": "Home", "h1s": ["Welcome"]}
        broken = {"status_code": 404, "title": "Not Found", "h1s": []}
        none_code = {"status_code": None, "title": "", "h1s": []}
        login_title = {"status_code": 200, "title": "Login to continue", "h1s": []}
        login_h1 = {"status_code": 200, "title": "Portal", "h1s": ["Sign in to your account"]}
        redirect = {"status_code": 301, "title": "", "h1s": []}

        assert should_pause(clean) is False, "clean 200 should not pause"
        assert should_pause(broken) is True, "404 should pause"
        assert should_pause(none_code) is True, "None status should pause"
        assert should_pause(login_title) is True, "login title should pause"
        assert should_pause(login_h1) is True, "login h1 should pause"
        assert should_pause(redirect) is False, "redirect should not pause"
        print("Test: should_pause — PASS")

    elif mode == "prompt":
        # Acceptance test 2: interactive prompt (manual — run this mode by hand)
        result = pause_and_prompt("https://example.com/secret", "Status code 403")
        print(f"Returned: {result!r}")

    elif mode == "state":
        # Acceptance test 3: add_to_human_review() persists to state.json
        url = "https://example.com/review-me"
        add_to_human_review(url)
        add_to_human_review(url)  # duplicate — should not be added twice

        from state import load_state
        state = load_state()
        count = state["needs_human"].count(url)
        assert count == 1, f"Expected 1 entry, got {count}"
        print(f"Test: add_to_human_review — PASS (needs_human: {state['needs_human']})")

    else:
        print(f"Unknown mode: {mode}. Use: pause | prompt | state")
        sys.exit(1)
