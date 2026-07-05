"""Attestation instrumentation for seo-agent (opt-in, defensive).

Records a fingerprint after each Claude API call so it can be diffed
against Anthropic's billing-side usage records by run_verifier.py.

Designed to be invisible if not configured: if production-safe-agent-loop
isn't on the path, OR if ATTESTATION_LEDGER_DB is unset, record() becomes
a no-op. seo-agent keeps doing what it does best either way.

Every call is wrapped in defensive try/except — instrumentation can
never break the agent. Errors are silent unless ATTESTATION_DEBUG=1.
"""
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Best-effort .env loading so this module works in any invocation context
# (REPL, smoke tests, cron). seo-agent's main.py already loads .env;
# load_dotenv() doesn't override existing env vars so this is a safe no-op
# when called twice.
def _try_load_env() -> None:
    try:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if not env_path.exists():
            return
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass  # never let .env loading break instrumentation


_try_load_env()


# Find production-safe-agent-loop: env var first, then default location.
_PSAL_PATH = os.environ.get("PSAL_PATH") or str(
    Path.home() / "production-safe-agent-loop"
)
if Path(_PSAL_PATH).exists() and _PSAL_PATH not in sys.path:
    sys.path.insert(0, _PSAL_PATH)

try:
    from attestation_fingerprint import AttestationFingerprint
except Exception:
    AttestationFingerprint = None


_lock = threading.Lock()
_singleton = None
_session_id: Optional[str] = None
_turn_counters: dict = {}
_project_name: str = "default"


def configure_project(project: Optional[str]) -> None:
    """Bind the active project name into the session id. Call once from main.py."""
    global _project_name
    _project_name = project or "default"


def _get_attestation():
    global _singleton
    if _singleton is not None:
        return _singleton
    if AttestationFingerprint is None:
        return None
    ledger_db = os.environ.get("ATTESTATION_LEDGER_DB")
    if not ledger_db:
        return None
    api_key_id = os.environ.get("ANTHROPIC_API_KEY_ID")
    if not api_key_id:
        # Fail loudly, record nothing: fingerprints tagged with a guessed
        # key id would mis-scope every future diff and mimic real drift.
        print(
            "[attestation] ANTHROPIC_API_KEY_ID not set — fingerprint "
            "recording disabled to avoid mis-tagged fingerprints. Set it "
            "to the API key name shown in the Anthropic Console.",
            file=sys.stderr,
        )
        return None
    try:
        Path(ledger_db).parent.mkdir(parents=True, exist_ok=True)

        class _StubUsageClient:
            """Recording path doesn't need a real Admin API client.
            The verifier process injects the real one when it runs."""
            def get_messages_usage_report(self, **kwargs):
                return {"data": [], "has_more": False, "next_page": None}

        _singleton = AttestationFingerprint(
            ledger_db_path=ledger_db,
            usage_client=_StubUsageClient(),
            api_key_id=api_key_id,
            # Blank counts as set for os.environ.get's default arg — treat
            # it as unset too, or an empty ANTHROPIC_WORKSPACE_ID= silently
            # tags every fingerprint with workspace_id="" instead of the
            # fallback, and it'll never match the CSV's "Default".
            workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID") or "default",
        )
    except Exception:
        if os.environ.get("ATTESTATION_DEBUG"):
            import traceback
            traceback.print_exc()
        return None
    return _singleton


def _session_id_for() -> str:
    global _session_id
    if _session_id is None:
        with _lock:
            if _session_id is None:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                _session_id = f"{_project_name}-{ts}-{uuid.uuid4().hex[:8]}"
    return _session_id


def record(message, module_name: str = "unknown", model_fallback: str = "unknown") -> None:
    """Record a fingerprint for a Claude API response.

    Never raises — instrumentation can't break the agent.

    Args:
        message: anthropic.types.Message returned by client.messages.create(...)
        module_name: which seo-agent module made the call (extractor, etc.)
        model_fallback: model_id to record if message.model is missing
    """
    try:
        att = _get_attestation()
        if att is None:
            return
        with _lock:
            turn = _turn_counters.get(module_name, 0) + 1
            _turn_counters[module_name] = turn
        usage = message.usage
        att.record_fingerprint(
            session_id=f"{_session_id_for()}::{module_name}",
            turn_count=turn,
            model_id=getattr(message, "model", None) or model_fallback,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            # Billing totals include cache tokens; record them so the
            # verifier compares like-for-like (finding C2, v2.0 review).
            tokens_cache_read=int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            ),
            tokens_cache_write=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        )
    except Exception:
        if os.environ.get("ATTESTATION_DEBUG"):
            import traceback
            traceback.print_exc()
