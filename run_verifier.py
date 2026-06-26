"""Run the attestation verifier against Anthropic's Admin API.

Designed to run on a cron schedule (e.g., every 60 seconds). Diffs the
most recent seo-agent fingerprints against Anthropic's billing-side
usage records and writes drift findings to the ledger DB.

This script does NOT touch seo-agent's logic — it only reads what
seo-agent has already recorded and queries the Admin API separately.

Usage:
    ANTHROPIC_ADMIN_API_KEY=sk-ant-admin-... \\
    ATTESTATION_LEDGER_DB=~/.attestation/seo-agent.db \\
    ANTHROPIC_API_KEY_ID=apikey_01... \\
    python run_verifier.py

Cron entry (runs every minute):
    * * * * * cd /path/to/seo-agent && /usr/bin/python3 run_verifier.py >> verifier.log 2>&1
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# Best-effort .env loading so this script works from cron (no shell env).
def _try_load_env() -> None:
    try:
        env_path = Path(__file__).resolve().parent / ".env"
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
        pass


_try_load_env()


# Find production-safe-agent-loop — same resolution as core/attestation_setup.py.
_PSAL_PATH = os.environ.get("PSAL_PATH") or str(
    Path.home() / "production-safe-agent-loop"
)
if Path(_PSAL_PATH).exists() and _PSAL_PATH not in sys.path:
    sys.path.insert(0, _PSAL_PATH)

try:
    from attestation_fingerprint import AttestationFingerprint
except ImportError:
    print(
        f"production-safe-agent-loop not found at {_PSAL_PATH!r}. "
        "Set PSAL_PATH or clone the repo to ~/production-safe-agent-loop.",
        file=sys.stderr,
    )
    sys.exit(1)


class AnthropicAdminUsageClient:
    """Minimal Admin API usage report client (stdlib only — no requests dep).

    Satisfies the UsageRecordsClient Protocol from attestation_fingerprint.
    """
    BASE_URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"

    def __init__(self, admin_api_key: str, timeout_seconds: int = 30) -> None:
        self.admin_api_key = admin_api_key
        self.timeout_seconds = timeout_seconds

    def get_messages_usage_report(
        self,
        *,
        starting_at: str,
        ending_at: Optional[str] = None,
        bucket_width: str = "1m",
        group_by: Optional[list] = None,
        models: Optional[list] = None,
        api_key_ids: Optional[list] = None,
        workspace_ids: Optional[list] = None,
        page: Optional[str] = None,
    ) -> dict:
        params: list = [("starting_at", starting_at), ("bucket_width", bucket_width)]
        if ending_at:
            params.append(("ending_at", ending_at))
        for g in (group_by or []):
            params.append(("group_by[]", g))
        for k in (api_key_ids or []):
            params.append(("api_key_ids[]", k))
        for w in (workspace_ids or []):
            params.append(("workspace_ids[]", w))
        for m in (models or []):
            params.append(("models[]", m))
        if page:
            params.append(("page", page))

        url = f"{self.BASE_URL}?{urlencode(params)}"
        req = Request(url, headers={
            "Authorization": f"Bearer {self.admin_api_key}",
            "anthropic-version": "2023-06-01",
        })
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"Admin API HTTP {e.code}: {body}", file=sys.stderr)
        except URLError as e:
            print(f"Admin API connection error: {e.reason}", file=sys.stderr)
        return {"data": [], "has_more": False, "next_page": None}


class CsvUsageRecordsClient:
    """Reads Anthropic Console's Usage CSV export instead of calling the Admin API.

    For users on plans where Admin API keys aren't available (individual orgs),
    the Console's Analytics > Usage page provides a date-range CSV export.
    Download it, point ATTESTATION_USAGE_CSV at the file, and the verifier
    works the same way — just with daily buckets instead of minute-level.

    Column-name mapping is defensive: tries common variations Anthropic might
    use (snake_case, Title Case, etc.). Run `python run_verifier.py --inspect-csv`
    to see the exact columns in your downloaded file.
    """

    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path

    def get_messages_usage_report(
        self,
        *,
        starting_at: str,
        ending_at: Optional[str] = None,
        bucket_width: str = "1d",
        api_key_ids: Optional[list] = None,
        workspace_ids: Optional[list] = None,
        models: Optional[list] = None,
        **_ignored,
    ) -> dict:
        start_dt = _parse_iso(starting_at)
        end_dt = _parse_iso(ending_at) if ending_at else datetime.now(timezone.utc)

        # Normalize filters to lowercase for case-insensitive matching against
        # the CSV's `api_key` / `workspace` columns (which use display names).
        key_filter = {k.strip().lower() for k in (api_key_ids or []) if k and k.strip()}
        ws_filter = {w.strip().lower() for w in (workspace_ids or []) if w and w.strip()}
        model_filter = {m.strip().lower() for m in (models or []) if m and m.strip()}

        # Group rows by day; each row maps to one (api_key, model) result in that day's bucket.
        daily: dict = {}

        try:
            with open(self.csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_date = _row_date(row)
                    if row_date is None or not (start_dt <= row_date < end_dt):
                        continue

                    # Apply filters (case-insensitive). Empty filter = match all.
                    result = _row_to_result(row)
                    if key_filter and result["api_key_id"].lower() not in key_filter:
                        continue
                    if ws_filter and result["workspace_id"].lower() not in ws_filter:
                        continue
                    if model_filter and result["model"].lower() not in model_filter:
                        continue

                    day_key = row_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                    daily.setdefault(day_key, []).append(result)
        except FileNotFoundError:
            print(f"CSV file not found at {self.csv_path}", file=sys.stderr)
            return {"data": [], "has_more": False, "next_page": None}

        data = []
        for day_start, results in sorted(daily.items()):
            day_end = (_parse_iso(day_start) + timedelta(days=1)).isoformat()
            data.append({"starting_at": day_start, "ending_at": day_end, "results": results})
        return {"data": data, "has_more": False, "next_page": None}


def _parse_iso(s: str) -> datetime:
    """Parse RFC 3339 / ISO 8601 with tz fallback to UTC."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row_date(row: dict) -> Optional[datetime]:
    for col in ("usage_date_utc", "date", "Date", "day", "Day", "timestamp", "Timestamp", "starting_at"):
        if col in row and row[col]:
            try:
                v = row[col].strip()
                # Accept YYYY-MM-DD or full ISO
                if len(v) == 10:
                    return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return _parse_iso(v)
            except (ValueError, AttributeError):
                continue
    return None


def _row_to_result(row: dict) -> dict:
    """Map a CSV row to the shape of one entry in API response 'results'.

    Column names verified against Anthropic's actual Console export
    (Analytics > Usage > Download CSV) — primary names listed first,
    alternates kept for forward-compatibility.
    """
    def _g(*names) -> str:
        for n in names:
            if n in row and row[n]:
                return str(row[n]).strip()
        return ""

    def _i(*names) -> int:
        v = _g(*names)
        try:
            return int(float(v)) if v else 0
        except (ValueError, TypeError):
            return 0

    return {
        "model": _g("model_version", "model", "Model"),
        # CSV uses key NAME, not apikey_01... id. The verifier compares this
        # against ANTHROPIC_API_KEY_ID, which in CSV mode should be the name.
        "api_key_id": _g("api_key", "api_key_id", "API Key", "key"),
        "workspace_id": _g("workspace", "workspace_id", "Workspace"),
        "uncached_input_tokens": _i(
            "usage_input_tokens_no_cache",
            "input_tokens",
            "uncached_input_tokens",
            "Input Tokens",
        ),
        "cache_read_input_tokens": _i(
            "usage_input_tokens_cache_read",
            "cache_read_input_tokens",
            "Cache Read Tokens",
        ),
        "cache_creation": {
            "ephemeral_5m_input_tokens": _i(
                "usage_input_tokens_cache_write_5m",
                "cache_creation_input_tokens",
                "cache_creation_5m_input_tokens",
            ),
            "ephemeral_1h_input_tokens": _i(
                "usage_input_tokens_cache_write_1h",
                "cache_creation_1h_input_tokens",
            ),
        },
        "output_tokens": _i("usage_output_tokens", "output_tokens", "Output Tokens"),
        "server_tool_use": {
            "web_search_requests": _i(
                "web_search_count",
                "web_search_requests",
                "web_searches",
            ),
        },
    }


def _inspect_csv(csv_path: Optional[str]) -> int:
    if not csv_path:
        print("ATTESTATION_USAGE_CSV not set", file=sys.stderr)
        return 1
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            print(f"CSV columns in {csv_path}:")
            for col in header:
                print(f"  - {col}")
            # Sample first row for context
            try:
                first = next(reader)
                print("\nFirst row sample:")
                for col, val in zip(header, first):
                    print(f"  {col}: {val}")
            except StopIteration:
                print("\n(CSV has only the header row; export a wider date range)")
        return 0
    except FileNotFoundError:
        print(f"CSV file not found at {csv_path}", file=sys.stderr)
        return 1


def main() -> int:
    if "--inspect-csv" in sys.argv:
        return _inspect_csv(os.environ.get("ATTESTATION_USAGE_CSV"))

    csv_path = os.environ.get("ATTESTATION_USAGE_CSV")
    admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")

    if csv_path:
        usage_client = CsvUsageRecordsClient(csv_path)
        mode = f"CSV mode (file: {csv_path})"
    elif admin_key:
        usage_client = AnthropicAdminUsageClient(admin_key)
        mode = "Admin API mode"
    else:
        print(
            "Neither ATTESTATION_USAGE_CSV nor ANTHROPIC_ADMIN_API_KEY is set. "
            "Set one to run the verifier.",
            file=sys.stderr,
        )
        return 1

    ledger_db = os.environ.get(
        "ATTESTATION_LEDGER_DB",
        str(Path.home() / ".attestation" / "seo-agent.db"),
    )
    if not Path(ledger_db).exists():
        print(
            f"Ledger DB not found at {ledger_db}. "
            "Has seo-agent recorded any fingerprints yet?",
            file=sys.stderr,
        )
        return 1

    af = AttestationFingerprint(
        ledger_db_path=ledger_db,
        usage_client=usage_client,
        api_key_id=os.environ.get("ANTHROPIC_API_KEY_ID", "seo-agent-default"),
        workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID", "default"),
    )

    if csv_path:
        # CSV mode: diff across a multi-day window
        days = int(os.environ.get("ATTESTATION_LOOKBACK_DAYS", "7"))
        ending_at = datetime.now(timezone.utc)
        starting_at = ending_at - timedelta(days=days)
        print(f"[{mode}] diffing {days}-day window: {starting_at.date()} -> {ending_at.date()}")
        results = af.diff_window(starting_at, ending_at)
    else:
        # Live API mode: minute-level recent buckets
        print(f"[{mode}] diffing recent buckets (15min lookback, 6min lag buffer)")
        results = af.diff_recent_buckets(lookback_minutes=15, lag_buffer_minutes=6)

    drift_count = sum(1 for r in results if r.drift_findings)
    alert_count = sum(1 for r in results if r.severity == "alert")

    print(
        f"verified {len(results)} bucket(s): {drift_count} with drift, "
        f"{alert_count} alert(s)"
    )

    for r in results:
        if r.drift_findings:
            print(f"  {r.bucket_start} model={r.model_id} severity={r.severity}")
            for f in r.drift_findings:
                print(f"    [{f.drift_class}] {f.details}")

    return 0 if alert_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
