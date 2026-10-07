"""A sandbox for `bash` on Linux, with bubblewrap (`--sandbox`).

The command sees the whole filesystem read-only, except the project, /tmp (a fresh empty one)
and the user's cache folder, which it can write. The places that hold credentials
(paths.SECRET_PLACES) are hidden: empty folders, empty files. It runs in its own process
namespace, so everything it starts ends with it. Network stays on unless asked otherwise
(installing packages needs it); with it on, a command could still send project files out.

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
        if not self.network:
            args.append("--unshare-net")
        return [*args, "--", *argv]


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
