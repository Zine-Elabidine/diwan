"""Running commands: bash (Git Bash or PowerShell on Windows), stopped with everything it
started when the user interrupts or the timeout passes."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolError, clip, schema

def find_shell() -> tuple[list[str], str]:
    """The command prefix to run a shell command, and the shell's name for the model.
    Windows: Git Bash if installed (never WSL's System32 bash), else PowerShell."""
    if os.name != "nt":
        bash = shutil.which("bash")
        return ([bash], "bash") if bash else (["/bin/sh"], "sh")
    candidates = []
    git = shutil.which("git")
    if git:  # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Programs")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file():
            return [str(c)], "bash (Git Bash on Windows)"
    ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return [ps, "-NoProfile", "-NonInteractive"], "PowerShell"


def _kill_tree(p: subprocess.Popen[str]) -> None:
    """Stop a command and everything it started: SIGTERM to its process group, a short grace
    for cleanup, then SIGKILL to whatever is left (Codex and Gemini CLI do the same).
    Windows: taskkill /T /F on the tree."""
    if os.name == "nt":
        if p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           check=False)
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        p.wait(0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)  # children can outlive the shell
    except (ProcessLookupError, PermissionError):
        pass


def _drain(p: subprocess.Popen[str]) -> tuple[str, str]:
    """What a stopped command printed. Gives up after 2 s: something that escaped the group
    may still hold the pipes open."""
    try:
        return p.communicate(timeout=2)
    except subprocess.TimeoutExpired as e:
        return _decode(e.stdout), _decode(e.stderr)


def _decode(b: bytes | str | None) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")


class Bash(Tool):
    name = "bash"
    parameters = schema({
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "seconds (default 120)"}},
        ["command"])

    def __init__(self) -> None:
        self.shell, self.shell_name = find_shell()
        self.description = (f"Run a shell command in the project directory with {self.shell_name}. "
                            "Use it to run tests, builds, git and programs; use grep, glob and read to "
                            "search and read files.")

    def run(self, ctx: ToolContext, command: str, timeout: int = 120) -> str:
        flag = "-Command" if self.shell_name == "PowerShell" else "-c"
        start = time.monotonic()
        # its own process group, so stopping it stops everything it started; no stdin, so a
        # command that waits for input fails instead of hanging (or reading the user's keys)
        group: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                                 if os.name == "nt" else {"start_new_session": True})
        p = subprocess.Popen([*self.shell, flag, command], cwd=ctx.cwd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", **group)
        stopped = None
        try:
            while True:
                try:
                    out, err = p.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if ctx.cancel.cancelled:
                        stopped = f"aborted by user after {time.monotonic() - start:.1f}s"
                    elif time.monotonic() - start > timeout:
                        stopped = f"timed out after {timeout}s"
                    if stopped:
                        _kill_tree(p)
                        out, err = _drain(p)
                        break
        except BaseException:  # Ctrl+C in plain mode, or anything else: never leave it running
            _kill_tree(p)
            raise
        out = (out + (("\n" if out and err else "") + err if err else "")).rstrip()
        if stopped:
            raise ToolError(clip(f"{stopped}\n{out}" if out else f"{stopped} (no output)"))
        return clip(f"{out}\n[exit code {p.returncode}]" if p.returncode else out or "(no output)")
