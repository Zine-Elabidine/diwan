import os
import subprocess
import time

import pytest

from diwan.tools import ToolError
from helpers import tool


@pytest.fixture
def project(tmp_path):
    files = {
        "app.py": "def login(user):\n    return check(user)\n",
        "src/auth.py": "import os\n\ndef check(user):\n    # TODO: real check\n    return True\n",
        "src/deep/util.py": "LOGIN_URL = '/login'\n",
        "README.md": "Login with your account.\n",
        "node_modules/lib/index.js": "function login() {}\n",
        "logo.png": "\x89PNG\0\0login",
    }
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def tools(root):
    return tool("grep", root), tool("glob", root)


def test_glob_matches_at_any_depth_and_skips_heavy_folders(project):
    _, glob = tools(project)
    assert set(glob("*.py").splitlines()) == {"app.py", "src/auth.py", "src/deep/util.py"}
    assert glob("src/**/*.py").splitlines() == sorted(glob("src/**/*.py").splitlines(),
                                                      key=lambda p: -os.path.getmtime(project / p))
    assert glob("*.js") == "No files match '*.js' under ."   # node_modules is skipped


def test_glob_lists_recently_changed_first(project):
    _, glob = tools(project)
    time.sleep(0.01)
    (project / "src/auth.py").write_text("changed\n")
    assert glob("*.py").splitlines()[0] == "src/auth.py"


def test_grep_reports_path_and_line_and_skips_binaries_and_heavy_folders(project):
    grep, _ = tools(project)
    out = grep("login").splitlines()
    assert "app.py:1: def login(user):" in out
    assert "src/deep/util.py:1: LOGIN_URL = '/login'" in out
    assert not any("node_modules" in line or "logo.png" in line for line in out)
    assert "README.md:1: Login with your account." in grep("login", ignore_case=True)


def test_grep_glob_path_and_context(project):
    grep, _ = tools(project)
    assert grep("login", glob="*.md", ignore_case=True) == "README.md:1: Login with your account."
    assert grep("check", path="src").startswith("src/auth.py:3: def check(user):")
    ctx = grep("TODO", path="src/auth.py", context=1).splitlines()
    assert ctx == ["src/auth.py-3- def check(user):", "src/auth.py:4:     # TODO: real check",
                   "src/auth.py-5-     return True"]


def test_grep_errors_and_no_match(project):
    grep, _ = tools(project)
    with pytest.raises(ToolError):
        grep("(unclosed")
    with pytest.raises(ToolError):
        grep("x", path="missing")
    assert grep("nothing-like-this") == "No matches for 'nothing-like-this'"


def test_in_a_git_repo_gitignore_decides(project):
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode:
        pytest.skip("no git")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    (project / ".gitignore").write_text("src/deep/\n")
    grep, glob = tools(project)
    assert "src/deep/util.py" not in glob("*.py")
    assert "util.py" not in grep("LOGIN")


def test_grep_stops_at_the_limit(project):
    (project / "big.txt").write_text("hit\n" * 500)
    grep, _ = tools(project)
    out = grep("hit", glob="big.txt").splitlines()
    assert len(out) == 101 and out[-1].startswith("[stopped at 100 matches")
