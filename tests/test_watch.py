"""Skills, memories and MCP servers that change during a session reach the model as a reminder
before its next request, at the end of the conversation (the system prompt never changes)."""

import pytest
from tarjuman import Message, Text, ToolCall
from tarjuman.fake import Fake

from diwan import memory, skills
from diwan.agent import Agent
from diwan.log import Log
from diwan.paths import Access, PathPolicy
from diwan.tools import Skill, ToolContext, ToolError, default_tools
from diwan.watch import MAX_LINES, Watch


def skill(folder, name, description):
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\nDo it.\n")


def setup(tmp_path, monkeypatch, replies=3):
    project, home = tmp_path / "p", tmp_path / "home"
    project.mkdir()
    skill(project / ".diwan" / "skills" / "deploy", "deploy", "Ship it.")
    monkeypatch.setattr(skills, "current", skills.find(project, home))
    mem = memory.Memory(tmp_path / "mem", "", native=True)
    mem.folder.mkdir()
    monkeypatch.setattr(memory, "current", mem)
    paths = PathPolicy(project, readable=skills.readable(skills.current))
    watch = Watch(project, mem, paths, home)
    agent = Agent(Fake([Message("assistant", [Text("ok")])] * replies), "m",
                  Log.new(cwd=str(project)), default_tools(), "sys", paths=paths, watch=watch)
    return agent, project, home, mem, paths


def reminders(agent):
    return [m.text for m in agent.log.messages() if m.role == "system" and m.text]


def test_a_skill_added_mid_session_is_told_once_and_can_be_loaded(tmp_path, monkeypatch):
    agent, _, home, _, paths = setup(tmp_path, monkeypatch)
    agent.turn("first")                    # nothing changed yet
    assert reminders(agent) == []
    skill(home / ".claude" / "skills" / "review", "review", "Review a diff.")
    agent.turn("second")
    assert reminders(agent) == ["Skills changed since this session started (load one with "
                                "`skill`):\n+ review: Review a diff."]
    assert [m.role for m in agent.log.messages()[-3:]] == ["user", "system", "assistant"]
    agent.turn("third")
    assert len(reminders(agent)) == 1      # told once
    assert "Do it." in Skill().run(ToolContext(paths), "review")
    assert paths.check(str(home / ".claude" / "skills" / "review" / "SKILL.md"),
                       write=False) is Access.INSIDE


def test_memories_from_elsewhere_are_told_but_not_the_sessions_own(tmp_path, monkeypatch):
    agent, _, _, mem, paths = setup(tmp_path, monkeypatch)
    agent.turn("first")
    memory.Remember().run(ToolContext(paths), name="mine", type="project", description="saved here",
                          text="x")
    agent.watch.own_save()                 # what the agent does after its own `memory` call
    (mem.folder / "user_tabs.md").write_text('---\nname: tabs\ndescription: "Prefers tabs"\n---\nx')
    agent.turn("second")
    [note] = reminders(agent)
    assert "+ tabs: Prefers tabs (user_tabs.md)" in note and "mine" not in note
    (mem.folder / "user_tabs.md").unlink()
    agent.turn("third")
    assert reminders(agent)[-1].endswith("- user_tabs.md (removed)")


def test_after_a_summary_the_whole_difference_is_told_again(tmp_path, monkeypatch):
    agent, _, home, _, _ = setup(tmp_path, monkeypatch, 4)
    agent.turn("first")
    skill(home / ".diwan" / "skills" / "lint", "lint", "Run the linter.")
    agent.turn("second")
    agent.watch.forget()                   # what a logged summary does
    agent.turn("third")
    assert [r.count("+ lint") for r in reminders(agent)] == [1, 1]


def test_a_long_change_is_cut(tmp_path, monkeypatch):
    agent, _, _, mem, _ = setup(tmp_path, monkeypatch)
    agent.turn("first")
    for i in range(MAX_LINES + 5):
        (mem.folder / f"project_m{i:02}.md").write_text(f"---\nname: m{i}\n---\n")
    agent.turn("second")
    [note] = reminders(agent)
    assert note.endswith("... and 5 more") and note.count("\n+ ") == MAX_LINES


def test_a_rewind_tells_again_only_what_left_with_the_old_branch(tmp_path, monkeypatch):
    from diwan.models import Ref, Router
    from diwan.session import Session
    agent, project, home, _, _ = setup(tmp_path, monkeypatch, 7)
    agent.record_model = lambda: None      # this fake provider isn't a real model switch
    session = Session(lambda log, ref: agent, agent.log, Ref("openrouter", "m"),
                      Router("openrouter"), project)
    agent.turn("one")
    agent.turn("two")
    skill(home / ".diwan" / "skills" / "lint", "lint", "Run the linter.")
    agent.turn("three")                    # the reminder goes right after "three"
    agent.turn("four")
    assert session.rewind(1) == "four"     # back to before "four": the reminder stays
    agent.turn("four, again")
    assert [r.count("+ lint") for r in reminders(agent)] == [1]   # not told twice
    assert session.rewind(2) == "three"    # back to before "three": the reminder is gone
    assert reminders(agent) == []
    agent.turn("three, again")
    assert [r.count("+ lint") for r in reminders(agent)] == [1]   # told again


# --- MCP servers that come and go -----------------------------------------------------------

def mcp_session(tmp_path, monkeypatch, script):
    import sys
    from pathlib import Path

    from diwan import mcp
    from diwan.tools import ToolSearch
    server = str(Path(__file__).parent / "fixtures" / "mcp_server.py")
    config = mcp.config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    c = mcp.connect({}, config)                      # no mcp.json at the start
    monkeypatch.setattr(mcp, "current", c)
    tools = {**default_tools(), "tool_search": ToolSearch()}
    agent = Agent(Fake(script), "m", Log.new(cwd=str(tmp_path)), tools, "sys",
                  approve=lambda c, t, o: True,
                  watch=Watch(tmp_path, None, tools=tools, home=tmp_path / "home"))

    def write(servers):
        import json
        import os
        config.write_text(json.dumps({"mcpServers": servers}))
        os.utime(config, (0, config.stat().st_mtime + 1))   # a new time, even within a second
        c.reload_if_changed()
        c.wait()

    return agent, c, write, {"command": sys.executable, "args": [server]}


def test_a_server_added_mid_session_comes_deferred_and_is_told(tmp_path, monkeypatch):
    import json
    search = json.dumps({"query": "select:mcp__test__echo"})
    script = [Message("assistant", [Text("ok")]),
              Message("assistant", [ToolCall("c1", "tool_search", search)]),
              Message("assistant", [ToolCall("c2", "mcp__test__echo", json.dumps({"text": "hi"}))]),
              Message("assistant", [Text("done")])]
    agent, c, write, spec = mcp_session(tmp_path, monkeypatch, script)
    try:
        agent.turn("first")
        before = [d.name for d in agent.definitions()]
        write({"test": spec, "broken": {"command": "/no/such/program"}})
        assert [d.name for d in agent.definitions()] == before   # the tools sent: unchanged
        agent.turn("use echo")
        [note] = reminders(agent)
        assert "+ test\n  echo, fail, slow" in note and "✗ broken failed: can't start" in note
        defs = agent.definitions()
        assert [d.name for d in defs][:len(before)] == before            # added at the end,
        assert all(d.deferred for d in defs[len(before):])               # hidden till loaded
        assert "echo: hi" in agent.log.messages()[-2].content[0].content[0].text
    finally:
        c.close()


def test_a_removed_server_is_told_and_its_loaded_tools_go(tmp_path, monkeypatch):
    agent, c, write, spec = mcp_session(tmp_path, monkeypatch, [Message("assistant", [Text("ok")])] * 2)
    try:
        write({"test": spec})
        agent.turn("first")
        agent._load(["mcp__test__echo"])
        assert "mcp__test__echo" in [d.name for d in agent.definitions()]
        write({})
        agent.turn("second")
        assert reminders(agent)[-1].endswith("- test (removed)")
        assert "mcp__test__echo" not in [d.name for d in agent.definitions()]
        assert not any(n.startswith("mcp__") for n in agent.tools)
    finally:
        c.close()


def test_a_server_that_stopped_starts_again_on_its_next_call(tmp_path, monkeypatch):
    _, c, write, spec = mcp_session(tmp_path, monkeypatch, [])
    try:
        write({"test": spec})
        echo = c.tools["mcp__test__echo"]
        echo.server._transport.proc.kill()
        echo.server._transport.proc.wait()
        assert echo.run(ToolContext(PathPolicy(tmp_path)), text="back") == "echo: back"
    finally:
        c.close()


def test_a_call_cut_off_by_the_server_stopping_is_not_sent_again(tmp_path, monkeypatch):
    from diwan import mcp
    _, c, write, spec = mcp_session(tmp_path, monkeypatch, [])
    try:
        write({"test": spec})
        echo = c.tools["mcp__test__echo"]
        sent = []
        real = echo.server.request

        def request(method, params=None, *a, **k):    # the server dies while handling the call
            if method == "tools/call":
                sent.append(params)
                echo.server._transport.proc.kill()
                echo.server._transport.proc.wait()
                raise mcp.MCPError("connection closed")
            return real(method, params, *a, **k)

        monkeypatch.setattr(echo.server, "request", request)
        with pytest.raises(ToolError, match="may or may not have run"):
            echo.run(ToolContext(PathPolicy(tmp_path)), text="once")
        assert len(sent) == 1                          # not sent twice
        assert not echo.server.stopped()               # but the server is back for the next one
    finally:
        c.close()


def test_a_removed_server_is_not_started_again(tmp_path, monkeypatch):
    _, c, write, spec = mcp_session(tmp_path, monkeypatch, [])
    try:
        write({"test": spec})
        echo = c.tools["mcp__test__echo"]
        write({})                                      # removed (and stopped) mid-session
        with pytest.raises(ToolError, match=r"removed from mcp\.json"):
            echo.run(ToolContext(PathPolicy(tmp_path)), text="hi")
        assert echo.server.stopped()
    finally:
        c.close()


# --- files the model read, changed on disk -------------------------------------------------

def files_session(tmp_path, script):
    import json
    (tmp_path / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    script = [Message("assistant", [ToolCall("r", "read", json.dumps({"path": "app.py"}))]), *script]
    tools = default_tools()
    agent = Agent(Fake(script), "m", Log.new(cwd=str(tmp_path)), tools, "sys",
                  paths=PathPolicy(tmp_path), approve=lambda c, t, o: True,
                  watch=Watch(tmp_path, None, home=tmp_path / "home"))
    return agent


def test_a_file_changed_by_the_user_is_told_with_its_lines(tmp_path):
    agent = files_session(tmp_path, [Message("assistant", [Text("ok")])] * 3)
    agent.turn("read it")
    (tmp_path / "app.py").write_text("a = 1\nb = 20\nc = 3\n")
    agent.turn("go on")
    [note] = reminders(agent)
    assert note.startswith("Files you read changed on disk")
    assert "app.py (+1 -1):" in note and "    2 - b = 2" in note and "    2 + b = 20" in note
    agent.turn("and again")
    assert len(reminders(agent)) == 1      # told once
    with pytest.raises(ToolError, match="changed since you last read it"):   # still read first
        default_tools()["edit"].run(ToolContext(PathPolicy(tmp_path), seen=agent.seen),
                                    path="app.py", old="b = 20", new="b = 3")


def test_its_own_edits_are_not_told_but_its_own_commands_are(tmp_path):
    import json
    script = [Message("assistant", [ToolCall("e", "edit", json.dumps(
                  {"path": "app.py", "old": "a = 1", "new": "a = 10"}))]),
              Message("assistant", [ToolCall("b", "bash", json.dumps(
                  {"command": "sed -i 's/c = 3/c = 30/' app.py"}))]),
              Message("assistant", [Text("done")])]
    agent = files_session(tmp_path, script)
    agent.turn("change it")
    [note] = reminders(agent)                 # after the sed, before the last step
    assert "    3 + c = 30" in note and "+ a = 10" not in note   # its edit: known, not told
    assert [m.role for m in agent.log.messages()[-3:]] == ["tool", "system", "assistant"]


def test_a_deleted_file_is_told(tmp_path):
    agent = files_session(tmp_path, [Message("assistant", [Text("ok")])] * 2)
    agent.turn("read it")
    (tmp_path / "app.py").unlink()
    agent.turn("go on")
    assert reminders(agent)[0].endswith("app.py: deleted")


def test_few_tools_at_the_start_are_shown_many_are_deferred(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from diwan import mcp
    spec = {"command": sys.executable, "args": [str(Path(__file__).parent / "fixtures" / "mcp_server.py")]}
    c = mcp.connect({"test": spec})
    try:
        assert not any(t.deferred for t in c.tools.values())   # three small tools: shown
        assert mcp.prompt_section(c) == ""                      # nothing to list
    finally:
        c.close()
    monkeypatch.setattr(mcp, "DEFER_ABOVE", 0)
    c = mcp.connect({"test": spec})
    try:
        assert all(t.deferred for t in c.tools.values())
        assert "- test\n  echo, fail, slow" in mcp.prompt_section(c)
    finally:
        c.close()
