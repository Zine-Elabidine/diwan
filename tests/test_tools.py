"""Each tool's schema (what the model is told) must match its run() (what actually runs)."""

import inspect
from functools import partial

import pytest

from diwan.tools import ToolContext, default_tools


def test_every_schema_matches_its_run_signature():
    for name, t in default_tools().items():
        params = list(inspect.signature(t.run).parameters.values())
        assert params[0].name == "ctx", name
        args = params[1:]
        schema = t.parameters
        assert list(schema["properties"]) == [p.name for p in args], name
        assert schema["required"] == [p.name for p in args if p.default is inspect.Parameter.empty], name
        assert t.definition.name == name and t.description


def test_a_context_resolves_inside_the_project(tmp_path):
    from diwan.paths import PathPolicy

    ctx = ToolContext(PathPolicy(tmp_path))
    assert ctx.cwd == tmp_path.resolve() and ctx.resolve("a/b.txt") == tmp_path.resolve() / "a/b.txt"
    assert not ctx.cancel.cancelled


def test_write_and_edit_refuse_a_file_changed_since_it_was_read(tmp_path):
    from diwan.paths import PathPolicy
    from diwan.tools import ToolError

    tools, ctx = default_tools(), ToolContext(PathPolicy(tmp_path), seen={})
    read, write, edit = (partial(tools[n].run, ctx) for n in ("read", "write", "edit"))
    f = tmp_path / "api.py"
    f.write_text("def login(): ...\n")

    with pytest.raises(ToolError, match="read it before"):
        write(path="api.py", content="")                 # never read: can't overwrite it
    write(path="new.py", content="x = 1\n")              # a new file needs no read
    edit(path="new.py", old="1", new="2")                # nor do the session's own changes

    read(path="api.py")
    f.write_text("def login(): ...\ndef logout(): ...\n")   # the user edits it meanwhile
    with pytest.raises(ToolError, match="changed since you last read it"):
        edit(path="api.py", old="login", new="sign_in")
    with pytest.raises(ToolError, match="changed since you last read it"):
        write(path="api.py", content="def sign_in(): ...\n")
    assert "logout" in f.read_text()                     # nothing was lost

    read(path="api.py")                                  # reading again unblocks it
    edit(path="api.py", old="login", new="sign_in")
    assert f.read_text() == "def sign_in(): ...\ndef logout(): ...\n"


def test_without_a_session_there_is_no_freshness_check(tmp_path):
    from diwan.paths import PathPolicy

    (tmp_path / "f.txt").write_text("a")
    default_tools()["write"].run(ToolContext(PathPolicy(tmp_path)), path="f.txt", content="b")
    assert (tmp_path / "f.txt").read_text() == "b"
