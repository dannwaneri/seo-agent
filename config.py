import os
import sys

_LICENSE_ENV = "SEO_AGENT_LICENSE"


def _pro_flag() -> bool:
    return "--pro" in sys.argv


def get_license() -> str | None:
    value = os.environ.get(_LICENSE_ENV)
    return value if value else None


def is_pro() -> bool:
    return _pro_flag() and get_license() is not None


def check_license() -> None:
    if _pro_flag() and get_license() is None:
        print(
            "ERROR: --pro requires a valid license key.\n"
            f"Set the {_LICENSE_ENV} environment variable and try again.\n"
            "  export SEO_AGENT_LICENSE=your-license-key"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import subprocess

    _mode = sys.argv[1] if len(sys.argv) > 1 else ""

    # Sub-process entry points (invoked by the test harness below)
    if _mode == "_test_is_pro":
        print(f"is_pro={is_pro()}")
        sys.exit(0)
    elif _mode == "_test_check_license":
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

    # Test 1: no --pro flag → is_pro() False
    rc, out = run(["_test_is_pro"])
    assert rc == 0 and "is_pro=False" in out, f"Test 1 failed: {out}"
    print("Test 1 PASS: no --pro -> is_pro() False")

    # Test 2: --pro, no license → is_pro() False
    rc, out = run(["--pro", "_test_is_pro"])
    assert rc == 0 and "is_pro=False" in out, f"Test 2 failed: {out}"
    print("Test 2 PASS: --pro without license -> is_pro() False")

    # Test 3: --pro + license set → is_pro() True
    rc, out = run(["--pro", "_test_is_pro"], env_extra={_LICENSE_ENV: "test-key"})
    assert rc == 0 and "is_pro=True" in out, f"Test 3 failed: {out}"
    print("Test 3 PASS: --pro with license -> is_pro() True")

    # Test 4: --pro, no license → check_license() exits 1 with clear message
    rc, out = run(["--pro", "_test_check_license"])
    assert rc == 1, f"Test 4 failed: expected exit 1, got {rc}"
    assert "ERROR" in out and _LICENSE_ENV in out, f"Test 4 failed: bad message: {out}"
    print("Test 4 PASS: check_license() exits 1 with readable error")

    print("\nAll acceptance tests passed.")
