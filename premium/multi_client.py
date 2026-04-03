"""Multi-client project folder management."""

import json
import os
from pathlib import Path

# Repo root is two levels up from this file (premium/multi_client.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROJECTS_DIR = _REPO_ROOT / "projects"

_DEFAULT_STATE = {"audited": [], "pending": [], "needs_human": [], "history": []}
_CSV_HEADER = "url\n"


def resolve_project(project_name: str) -> dict:
    """
    Return absolute paths for a project's input.csv, state.json, and reports/.

    Creates the folder structure if it does not exist.
    Never modifies files in an already-existing project folder.
    """
    project_dir = _PROJECTS_DIR / project_name
    input_csv = project_dir / "input.csv"
    state_json = project_dir / "state.json"
    reports_dir = project_dir / "reports"

    if not project_dir.exists():
        project_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(exist_ok=True)

        input_csv.write_text(_CSV_HEADER, encoding="utf-8")

        state_json.write_text(
            json.dumps(_DEFAULT_STATE, indent=2),
            encoding="utf-8",
        )

    return {
        "input_csv": str(input_csv),
        "state_json": str(state_json),
        "reports_dir": str(reports_dir),
    }


def list_projects() -> list[str]:
    """Return names of all existing project folders under projects/."""
    if not _PROJECTS_DIR.exists():
        return []
    return sorted(
        entry.name
        for entry in _PROJECTS_DIR.iterdir()
        if entry.is_dir()
    )


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import shutil
    import sys
    import tempfile

    # Make the package importable under its real dotted name
    _repo = str(Path(__file__).resolve().parent.parent)
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    import premium.multi_client as _mod  # re-import under real name

    failures = []

    # Point to a temp directory so tests are isolated from real projects/
    _orig_projects_dir = _mod._PROJECTS_DIR

    def _reset_projects_dir():
        _mod._PROJECTS_DIR = _orig_projects_dir

    def run(name, fn):
        try:
            fn()
            print(f"Test {name} PASS")
        except Exception as exc:
            print(f"Test {name} FAIL: {exc}")
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # ------------------------------------------------------------------ #
        # Test 1: existing project returns correct paths without modifying    #
        # ------------------------------------------------------------------ #
        def test_existing_project():
            proj_dir = base / "projects1" / "acme"
            proj_dir.mkdir(parents=True)
            (proj_dir / "reports").mkdir()
            sentinel = "url\nhttps://already-here.com\n"
            (proj_dir / "input.csv").write_text(sentinel, encoding="utf-8")
            (proj_dir / "state.json").write_text('{"custom": true}', encoding="utf-8")

            _mod._PROJECTS_DIR = base / "projects1"
            paths = _mod.resolve_project("acme")
            _reset_projects_dir()

            assert paths["input_csv"] == str(proj_dir / "input.csv")
            assert paths["state_json"] == str(proj_dir / "state.json")
            assert paths["reports_dir"] == str(proj_dir / "reports")
            # Existing files must not be touched
            assert (proj_dir / "input.csv").read_text(encoding="utf-8") == sentinel
            assert json.loads((proj_dir / "state.json").read_text())["custom"] is True

        run("1: existing project returns correct paths, no file modification", test_existing_project)

        # ------------------------------------------------------------------ #
        # Test 2: new project creates folder structure                        #
        # ------------------------------------------------------------------ #
        def test_new_project_creates_structure():
            _mod._PROJECTS_DIR = base / "projects2"

            paths = _mod.resolve_project("newclient")
            _reset_projects_dir()

            proj_dir = base / "projects2" / "newclient"
            assert proj_dir.exists(), "Project dir not created"
            assert (proj_dir / "reports").is_dir(), "reports/ not created"
            assert (proj_dir / "input.csv").exists(), "input.csv not created"
            assert (proj_dir / "state.json").exists(), "state.json not created"
            assert paths["input_csv"] == str(proj_dir / "input.csv")
            assert paths["state_json"] == str(proj_dir / "state.json")
            assert paths["reports_dir"] == str(proj_dir / "reports")

        run("2: new project creates folder structure and returns correct paths", test_new_project_creates_structure)

        # ------------------------------------------------------------------ #
        # Test 3: new project file contents                                   #
        # ------------------------------------------------------------------ #
        def test_new_project_file_contents():
            _mod._PROJECTS_DIR = base / "projects3"
            _mod.resolve_project("beta")
            _reset_projects_dir()

            proj_dir = base / "projects3" / "beta"
            csv_content = (proj_dir / "input.csv").read_text(encoding="utf-8")
            assert csv_content == "url\n", f"Unexpected CSV: {csv_content!r}"

            state = json.loads((proj_dir / "state.json").read_text(encoding="utf-8"))
            for key in ("audited", "pending", "needs_human", "history"):
                assert key in state and state[key] == [], f"Missing/wrong key: {key}"

            assert list((proj_dir / "reports").iterdir()) == [], "reports/ should be empty"

        run("3: new project has correct file contents", test_new_project_file_contents)

        # ------------------------------------------------------------------ #
        # Test 4: two projects have separate paths and no shared state        #
        # ------------------------------------------------------------------ #
        def test_two_projects_separate():
            _mod._PROJECTS_DIR = base / "projects4"
            paths_a = _mod.resolve_project("a")
            paths_b = _mod.resolve_project("b")
            _reset_projects_dir()

            for key in ("input_csv", "state_json", "reports_dir"):
                assert paths_a[key] != paths_b[key], f"Shared path for {key}"
            assert "projects4/a" in paths_a["input_csv"].replace("\\", "/")
            assert "projects4/b" in paths_b["input_csv"].replace("\\", "/")

        run("4: two projects have separate paths", test_two_projects_separate)

        # ------------------------------------------------------------------ #
        # Test 5: list_projects returns correct names                         #
        # ------------------------------------------------------------------ #
        def test_list_projects():
            _mod._PROJECTS_DIR = base / "projects5"
            _mod.resolve_project("alpha")
            _mod.resolve_project("beta")
            result = _mod.list_projects()
            _reset_projects_dir()

            assert sorted(result) == ["alpha", "beta"], f"Got: {result}"

        run("5: list_projects returns correct project names", test_list_projects)

    print()
    if failures:
        print(f"{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    else:
        print("All 5 acceptance tests passed.")
