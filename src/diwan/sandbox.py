"""A sandbox for `bash` on Linux, with bubblewrap (`--sandbox`).

The command sees the whole filesystem read-only, except the project, /tmp (a fresh empty one)
and the user's cache folder, which it can write. The places that hold credentials
(paths.SECRET_PLACES) are hidden: empty folders, empty files. It runs in its own process
namespace, so everything it starts ends with it. In the project's git folder, what git runs
by itself stays read-only: hooks and config (which can name programs: core.hooksPath,
core.fsmonitor, filters). Otherwise a command could plant code that runs later, outside the
sandbox, the next time the user runs git. Commits, branches and the rest still work.
Network stays on unless asked otherwise (installing packages needs it); with it on, a command
could still send project files out.

In exchange, sandboxed commands run without asking: approval was the only guard on bash."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import SECRET_PLACES


@dataclass(frozen=True)
class Sandbox:
    project: Path
    home: Path
    network: bool = True
    fresh_tmp: bool = True    # an empty /tmp; False keeps the real one, read-only (tests)

    def wrap(self, argv: list[str]) -> list[str]:
        """`argv` run inside the sandbox."""
        args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                *(["--tmpfs", "/tmp"] if self.fresh_tmp else []),
                "--unshare-pid", "--die-with-parent", "--new-session"]
        cache = self.home / ".cache"
        if cache.is_dir():
            args += ["--bind", str(cache), str(cache)]
        for rel in SECRET_PLACES:
            p = self.home / rel
            if p.is_dir():
                args += ["--tmpfs", str(p)]
            elif p.exists():
                args += ["--ro-bind", "/dev/null", str(p)]
        # after the hiding, so a project inside a hidden place still works (Diwan's tests)
        args += ["--bind", str(self.project), str(self.project), "--chdir", str(self.project)]
        for p in _git_protected(self.project):
            args += ["--ro-bind", str(p), str(p)]
        if not self.network:
            args.append("--unshare-net")
        return [*args, "--", *argv]


def _git_protected(project: Path) -> list[Path]:
    """The files git may run code from, in the project's repository and its submodules'. A
    missing hooks folder is created (empty, as git makes it) so it can't be planted."""
    git = project / ".git"
    if not git.is_dir():      # no repository, or a worktree whose git folder is elsewhere
        return []             # (outside the project: read-only already)
    out: list[Path] = []
    for repo in [git, *(c.parent for c in (git / "modules").rglob("config"))]:
        hooks = repo / "hooks"
        hooks.mkdir(exist_ok=True)
        out.append(hooks)
        if (repo / "config").is_file():
            out.append(repo / "config")
    hooks_path = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=project,
                                capture_output=True, text=True, check=False).stdout.strip()
    if hooks_path:            # hooks kept in the project itself (.githooks/...)
        p = (project / Path(hooks_path).expanduser()).resolve()
        if p.is_dir() and p.is_relative_to(project):
            out.append(p)
    return out


# the sandbox this process uses (cli.py, --sandbox), or None
current: Sandbox | None = None


def available() -> str | None:
    """None if `--sandbox` can work here, else why not."""
    if not sys.platform.startswith("linux"):
        return "the sandbox needs Linux (bubblewrap)"
    if shutil.which("bwrap") is None:
        return "the sandbox needs bubblewrap: install the `bubblewrap` package"
    try:
        r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                            "--unshare-pid", "true"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"bubblewrap failed to start: {e}"
    if r.returncode != 0:
        return f"bubblewrap can't run here: {r.stderr.decode(errors='replace').strip()[:200]}"
    return None
