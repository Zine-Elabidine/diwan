"""Each tool's schema (what the model is told) must match its run() (what actually runs)."""

import inspect

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
