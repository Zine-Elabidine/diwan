"""Where the file tools may reach: inside the project freely, outside after approval,
credentials never."""

import sys

import pytest

from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.paths import Access, PathPolicy
from diwan.tools import ToolError, default_tools
from helpers import call, say, tool


def policy(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    (project / "src").mkdir(parents=True)
    (home / ".diwan").mkdir(parents=True)
    (home / ".diwan" / "env").write_text("OPENROUTER_API_KEY=secret")
    (home / "notes.txt").write_text("outside")
    return PathPolicy(project, home), project, home


def test_inside_outside_and_secret_places(tmp_path):
    p, _project, home = policy(tmp_path)
    assert p.check("src/a.py") is Access.INSIDE
    assert p.check(str(home / "notes.txt")) is Access.ASK
    assert p.check("../home/notes.txt") is Access.ASK
    assert p.check(str(home / ".diwan" / "env")) is Access.DENIED
    assert p.check(".env") is Access.ASK and p.check("config/.env.local") is Access.ASK


def test_a_symlink_into_a_secret_place_is_refused(tmp_path):
    p, project, home = policy(tmp_path)
    (project / "innocent").symlink_to(home / ".diwan" / "env")
    assert p.check("innocent") is Access.DENIED


def test_the_tools_refuse_secret_places_even_when_called_directly(tmp_path):
    p, _, home = policy(tmp_path)
    try:
        tool("read", p)(path=str(home / ".diwan" / "env"))
        raise AssertionError("read a secret")
    except ToolError as e:
        assert "credentials" in str(e)


def run_read(tmp_path, path, approve):
    p, project, home = policy(tmp_path)
    asked = []
    log = Log.new(cwd=str(project))
    a = Agent(Fake([call("read", path=path(home)), say("ok")]), "m", log, default_tools(),
              "sys", approve=lambda c, s, outside: asked.append(c.name) or approve, paths=p)
    a.turn("go")
    result = next(m for m in log.messages() if m.role == "tool").content[0]
    return asked, result


def test_reading_outside_the_project_asks_first(tmp_path):
    asked, result = run_read(tmp_path, lambda home: str(home / "notes.txt"), approve=False)
    assert asked == ["read"] and result.is_error and "denied" in result.text
    asked, result = run_read(tmp_path / "2", lambda home: str(home / "notes.txt"), approve=True)
    assert asked == ["read"] and not result.is_error and "outside" in result.text


def test_secrets_are_refused_without_asking(tmp_path):
    asked, result = run_read(tmp_path, lambda home: str(home / ".diwan" / "env"), approve=True)
    assert asked == [] and result.is_error and "credentials" in result.text
    assert "OPENROUTER_API_KEY" not in result.text      # the contents never reach the model


def test_reading_inside_the_project_never_asks(tmp_path):
    asked, _result = run_read(tmp_path, lambda home: "src", approve=False)
    assert asked == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_grep_skips_files_the_policy_blocks(tmp_path):
    p, project, home = policy(tmp_path)
    (project / "innocent").symlink_to(home / ".diwan" / "env")
    (project / ".env").write_text("OPENROUTER_API_KEY=also-secret")
    (project / "src" / "a.py").write_text("OPENROUTER_API_KEY = os.environ[...]")
    out = tool("grep", p)(pattern="OPENROUTER")
    assert "src/a.py" in out
    assert "KEY=secret" not in out and "also-secret" not in out
    assert {line.split(":")[0] for line in out.splitlines()} == {"src/a.py"}


def test_always_covers_the_project_only(tmp_path):
    p, project, home = policy(tmp_path)
    asked = []
    log = Log.new(cwd=str(project))
    a = Agent(Fake([call("write", path="in.txt", content="1"),
                    call("write", cid="c2", path=str(home / "out.txt"), content="2"), say("ok")]),
              "m", log, default_tools(), "sys", paths=p,
              approve=lambda c, s, outside: asked.append((c.name, outside)) or True)
    a.turn("go")
    assert asked == [("write", False), ("write", True)]   # the second call says it's outside
