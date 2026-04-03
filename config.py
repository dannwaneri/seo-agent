import hashlib
import os
import re
import sys

_LICENSE_ENV = "SEO_AGENT_LICENSE"
_KEY_RE = re.compile(r'^SEO-([A-Z0-9]{8})-([A-Z0-9]{4})-([A-Z0-9]{8})$')


def _validate_license_key(key: str) -> bool:
    if not key:
        return False
    m = _KEY_RE.match(key)
    if not m:
        return False
    part1, part2, checksum = m.group(1), m.group(2), m.group(3)
    payload = f"SEO-{part1}-{part2}"
    expected = hashlib.sha256(
        (payload + "seo-agent-2026").encode()
    ).hexdigest()[:8].upper()
    return checksum == expected


def _pro_flag() -> bool:
    return "--pro" in sys.argv


def get_license() -> str | None:
    value = os.environ.get(_LICENSE_ENV)
    return value if value else None


def is_pro() -> bool:
    if not _pro_flag():
        return False
    key = get_license()
    return key is not None and _validate_license_key(key)


def check_license() -> None:
    if _pro_flag() and not is_pro():
        print(
            "ERROR: Invalid or missing SEO_AGENT_LICENSE key.\n"
            "Purchase a license at github.com/dannwaneri/seo-agent"
        )
        sys.exit(1)


def get_smtp_config() -> dict | None:
    keys = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
    values = {k: os.environ.get(k) for k in keys}
    if any(v is None or v == "" for v in values.values()):
        return None
    return {
        "host":     values["SMTP_HOST"],
        "port":     int(values["SMTP_PORT"]),
        "user":     values["SMTP_USER"],
        "password": values["SMTP_PASSWORD"],
        "from":     values["SMTP_FROM"],
    }


def get_pagespeed_key() -> str | None:
    return os.environ.get("PAGESPEED_API_KEY") or None


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import subprocess

    _argv_set = set(sys.argv[1:])

    # Sub-process entry points (invoked by the test harness below)
    if "_test_is_pro" in _argv_set:
        print(f"is_pro={is_pro()}")
        sys.exit(0)
    elif "_test_check_license" in _argv_set:
        check_license()
        print("no exit")  # should not reach here
        sys.exit(0)

    # Test harness (run with no args: python config.py)
    python = sys.executable
    script = __file__

    def run(args: list[str], env_extra: dict | None = None) -> tuple[int, str]:
        env = os.environ.copy()
        env.pop(_LICENSE_ENV, None)
        if env_extra:
            env.update(env_extra)
        result = subprocess.run(
            [python, script] + args,
            capture_output=True, text=True, env=env,
        )
        return result.returncode, result.stdout + result.stderr

    # Build a valid key at test-time using the same hashing the validator uses
    import hashlib as _hl
    _p1, _p2 = "TESTKEY1", "TST1"
    _payload = f"SEO-{_p1}-{_p2}"
    _cs = _hl.sha256((_payload + "seo-agent-2026").encode()).hexdigest()[:8].upper()
    _valid_key = f"{_payload}-{_cs}"

    # Test 1: no --pro flag → is_pro() False
    rc, out = run(["_test_is_pro"])
    assert rc == 0 and "is_pro=False" in out, f"Test 1 failed: {out}"
    print("Test 1 PASS: no --pro -> is_pro() False")

    # Test 2: --pro, no license → is_pro() False
    rc, out = run(["--pro", "_test_is_pro"])
    assert rc == 0 and "is_pro=False" in out, f"Test 2 failed: {out}"
    print("Test 2 PASS: --pro without license -> is_pro() False")

    # Test 2b: --pro + invalid string → is_pro() False
    rc, out = run(["--pro", "_test_is_pro"], env_extra={_LICENSE_ENV: "test123"})
    assert rc == 0 and "is_pro=False" in out, f"Test 2b failed: {out}"
    print("Test 2b PASS: --pro with invalid key -> is_pro() False")

    # Test 2c: --pro + right format but wrong checksum → is_pro() False
    rc, out = run(["--pro", "_test_is_pro"], env_extra={_LICENSE_ENV: "SEO-TESTKEY1-TST1-DEADBEEF"})
    assert rc == 0 and "is_pro=False" in out, f"Test 2c failed: {out}"
    print("Test 2c PASS: --pro with bad checksum -> is_pro() False")

    # Test 3: --pro + valid checksummed key → is_pro() True
    rc, out = run(["--pro", "_test_is_pro"], env_extra={_LICENSE_ENV: _valid_key})
    assert rc == 0 and "is_pro=True" in out, f"Test 3 failed: {out}"
    print("Test 3 PASS: --pro with valid key -> is_pro() True")

    # Test 4: --pro, no license → check_license() exits 1 with updated message
    rc, out = run(["--pro", "_test_check_license"])
    assert rc == 1, f"Test 4 failed: expected exit 1, got {rc}"
    assert "ERROR" in out and "github.com/dannwaneri/seo-agent" in out, \
        f"Test 4 failed: bad message: {out}"
    print("Test 4 PASS: check_license() exits 1 with GitHub repo reference")

    print("\nAll acceptance tests passed.")
