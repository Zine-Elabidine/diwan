"""Memory through Telepathy: the index loaded at the start, files read without asking, new
memories saved into the right bundle, `tp session end` at the end. `tp` is faked here."""

import json
import os
import stat

import pytest

from diwan import memory
from diwan.paths import Access, PathPolicy
from diwan.tools import ToolContext, ToolError, default_tools


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A session folder with two bundle links (as Telepathy builds it), and a fake `tp`."""
    bundles = tmp_path / "store"
    for b in ("personal", "research"):
        (bundles / b).mkdir(parents=True)
    (bundles / "research" / "project_goal.md").write_text("---\nname: goal\n---\nShip v1.\n")
    folder = tmp_path / "sessions" / "dir--proj"
    folder.mkdir(parents=True)
    for b in ("personal", "research"):
        os.symlink(bundles / b, folder / b)
    (folder / "MEMORY.md").write_text("## research\n- [Goal](research/project_goal.md) — ship v1\n")
    calls = tmp_path / "calls.txt"
    tp = tmp_path / "bin" / "tp"
    tp.parent.mkdir()
    out = {"folder": str(folder), "index": (folder / "MEMORY.md").read_text(),
           "bundles": ["personal", "research"], "write": "research", "personal": "personal"}
    tp.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n"
                  f"[ \"$2\" = start ] && printf '%s\\n' '{json.dumps(out)}'\nexit 0\n")
    tp.chmod(tp.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tp.parent}{os.pathsep}{os.environ['PATH']}")
    m, message = memory.start(tmp_path / "proj")
    assert m is not None and message == ""
    monkeypatch.setattr(memory, "current", m)
    return m, bundles, calls


def test_the_index_goes_into_the_prompt_once(store):
    m, _, _ = store
    text = memory.prompt_section(m)
    assert "[Goal](research/project_goal.md)" in text and str(m.folder) in text


def test_memory_files_are_read_without_asking_but_not_written(store, tmp_path):
    m, _, _ = store
    policy = PathPolicy(tmp_path / "proj", readable=m.readable())
    goal = str(m.folder / "research" / "project_goal.md")
    assert policy.check(goal, write=False) is Access.INSIDE
    assert policy.check(goal, write=True) is Access.ASK
    assert policy.check(str(tmp_path / "elsewhere.txt"), write=False) is Access.ASK
    read = default_tools()["read"]
    assert "Ship v1." in read.run(ToolContext(policy), path=goal)


def test_saving_picks_the_bundle_by_type(store, tmp_path):
    _, bundles, _ = store
    tool, ctx = memory.Remember(), ToolContext(PathPolicy(tmp_path))
    assert tool.run(ctx, name="likes-tabs", type="user", description="Prefers tabs",
                    text="Tabs, width 4.") == "Saved personal/user_likes-tabs.md"
    saved = (bundles / "personal" / "user_likes-tabs.md").read_text()
    assert saved.startswith('---\nname: likes-tabs\ndescription: "Prefers tabs"\n'
                            'metadata:\n  type: user\n---')
    assert tool.run(ctx, name="db", type="project", description="DB", text="Postgres 17") \
        == "Saved research/project_db.md"
    assert tool.run(ctx, name="db", type="project", description="DB", text="Postgres 18") \
        == "Updated research/project_db.md"
    with pytest.raises(ToolError):
        tool.run(ctx, name="Not A Slug", type="user", description="x", text="y")


def test_the_end_hands_back_to_telepathy(store, tmp_path):
    _, _, calls = store
    memory.end(tmp_path / "proj")
    assert calls.read_text().splitlines() == [f"session start --cwd {tmp_path / 'proj'}",
                                              f"session end --cwd {tmp_path / 'proj'}"]


def test_no_tp_means_no_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert memory.start(tmp_path) == (None, "")
