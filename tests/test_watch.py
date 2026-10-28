"""Skills and memories that change during a session reach the model as a reminder at the start
of the next turn, at the end of the conversation (the system prompt never changes)."""

from tarjuman import Message, Text
from tarjuman.fake import Fake

from diwan import memory, skills
from diwan.agent import Agent
from diwan.log import Log
from diwan.paths import Access, PathPolicy
from diwan.tools import Skill, ToolContext, default_tools
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
    return [m.text for m in agent.log.messages() if m.role == "system"]


def test_a_skill_added_mid_session_is_told_once_and_can_be_loaded(tmp_path, monkeypatch):
    agent, _, home, _, paths = setup(tmp_path, monkeypatch)
    skill(home / ".claude" / "skills" / "review", "review", "Review a diff.")
    agent.turn("first")                    # never before the first message
    assert reminders(agent) == []
    agent.turn("second")
    assert reminders(agent) == ["Skills changed since this session started (load one with "
                                "`skill`):\n+ review: Review a diff."]
    assert agent.log.messages()[-3].role == "system"   # right before the user's message
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
    agent, project, home, _, _ = setup(tmp_path, monkeypatch, 6)
    agent.record_model = lambda: None      # this fake provider isn't a real model switch
    session = Session(lambda log, ref: agent, agent.log, Ref("openrouter", "m"),
                      Router("openrouter"), project)
    agent.turn("one")
    agent.turn("two")
    skill(home / ".diwan" / "skills" / "lint", "lint", "Run the linter.")
    agent.turn("three")                    # the reminder goes right before "three"
    assert session.rewind(1) == "three"    # back to before "three": the reminder stays
    agent.turn("three, again")
    assert [r.count("+ lint") for r in reminders(agent)] == [1]   # not told twice
    assert session.rewind(2) == "two"      # back to before "two": the reminder is gone
    assert reminders(agent) == []
    agent.turn("two, again")
    assert [r.count("+ lint") for r in reminders(agent)] == [1]   # told again
