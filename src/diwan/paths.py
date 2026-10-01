"""Where the file tools may reach.

Inside the project: free. Outside it: the user approves first, even for reading. Places that
hold credentials: refused, approval or not. `.env` files ask even inside the project.

This guards the file tools only. `bash` can still read anything the user can; its approval is
the only guard there, and a sandbox for commands is a later step."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

# relative to the home folder: never read or written by a tool
SECRET_PLACES = (".diwan", ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker",
                 ".config/gh", ".config/gcloud", ".netrc", ".git-credentials", ".curlrc",
                 ".npmrc", ".pypirc")


class Access(Enum):
    INSIDE = "inside"      # in the project: no question
    ASK = "ask"            # outside the project, or a .env file: the user approves first
    DENIED = "denied"      # holds credentials: refused


class PathPolicy:
    def __init__(self, project: Path, home: Path | None = None):
        self.project = project.resolve()
        home = (home or Path.home()).resolve()
        self.secrets = [home / p for p in SECRET_PLACES]

    def resolve(self, path: str) -> Path:
        """The real location (symlinks followed), relative paths taken from the project."""
        p = Path(path).expanduser()
        return (p if p.is_absolute() else self.project / p).resolve()

    def check(self, path: str) -> Access:
        real = self.resolve(path)
        if self.secret(real):
            return Access.DENIED
        if env_file(real):
            return Access.ASK
        return Access.INSIDE if real.is_relative_to(self.project) else Access.ASK

    def secret(self, real: Path) -> bool:
        """A resolved path inside a place that holds credentials."""
        return any(real == s or real.is_relative_to(s) for s in self.secrets)

    def searchable(self, path: Path) -> bool:
        """Whether a search (grep, glob) may look at this file: never a secret place, and grep
        never reads a .env file without the user asking for that file by name."""
        real = path.resolve()
        return not self.secret(real) and not env_file(real)

    def why(self, path: str) -> str:
        return f"{path} holds credentials; tools never read or write it"


def env_file(p: Path) -> bool:
    return p.name == ".env" or p.name.startswith(".env.")
