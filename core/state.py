import json
import os

# state.json lives in the project root, one level above core/
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "state.json")

_DEFAULT_STATE = {"audited": [], "pending": [], "needs_human": [], "history": []}


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        save_state(_DEFAULT_STATE.copy())
        return _DEFAULT_STATE.copy()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # Ensure all expected keys exist
    for key in _DEFAULT_STATE:
        state.setdefault(key, [])
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_audited(state: dict, url: str) -> bool:
    return url in state["audited"]


def mark_audited(state: dict, url: str) -> None:
    if url not in state["audited"]:
        state["audited"].append(url)
    # Remove from pending if present
    if url in state["pending"]:
        state["pending"].remove(url)
    save_state(state)


def append_run_record(record: dict) -> None:
    """Append a run summary record to state["history"] and persist to disk."""
    state = load_state()
    state["history"].append(record)
    save_state(state)
