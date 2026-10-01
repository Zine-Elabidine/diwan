import json

from tarjuman import ToolCall

from diwan.present import preview, turn_mark


def call(name, **args):
    return ToolCall("c1", name, json.dumps(args))


def test_preview_shows_what_a_change_does():
    edit = preview(call("edit", path="a.py", old="x = 1\ny = 2", new="x = 3"), limit=8)
    assert edit == [("- x = 1", "red"), ("- y = 2", "red"), ("+ x = 3", "green")]
    write = preview(call("write", path="b.py", content="\n".join(f"line {i}" for i in range(10))),
                    limit=3)
    assert write[:3] == [("+ line 0", "green"), ("+ line 1", "green"), ("+ line 2", "green")]
    assert write[3] == ("… 7 more lines", "dim")
    assert preview(call("bash", command="ls"), limit=8) == []
    assert preview(ToolCall("c1", "write", "{not json"), limit=8) == []


def test_turn_mark():
    assert turn_mark("done") == ("✓", "green")
    assert turn_mark("max_steps") == ("■ stopped: max_steps", "red")
