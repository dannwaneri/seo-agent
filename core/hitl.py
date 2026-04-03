"""Human-in-the-loop: pauses execution and prompts for manual review."""

import sys

from .state import load_state, save_state

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
