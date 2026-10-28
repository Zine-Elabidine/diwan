"""Deferred tools and tool_search: left out of requests until the model loads them."""

import json

import pytest
from tarjuman import Message, Text, ToolCall, ToolsAdded, visible_tools
from tarjuman.fake import Fake

from diwan.agent import Agent
from diwan.log import Log
from diwan.tools import Tool, ToolContext, ToolError, ToolSearch, default_tools
from diwan.tools.base import schema

# --- a fake MCP server's worth of tools ------------------------------------------------------

SPECS = [
    ("gh__create_issue", "Create a new issue in a GitHub repository."),
    ("gh__list_issues", "List the issues of a GitHub repository, open or closed."),
    ("gh__close_issue", "Close an issue."),
    ("gh__create_pull_request", "Open a pull request from a branch."),
    ("gh__merge_pull_request", "Merge a pull request."),
    ("gh__search_code", "Search code across GitHub repositories."),
    ("cal__create_event", "Create an event in the calendar."),
    ("cal__list_events", "List calendar events between two dates."),
    ("cal__delete_event", "Delete a calendar event."),
    ("db__run_query", "Run a read-only SQL query on the database."),
    ("db__list_tables", "List the tables of the database and their columns."),
    ("mail__send", "Send an email to one or more people."),
    ("mail__search", "Search the mailbox for messages."),
]


class Remote(Tool):
    """A deferred tool, as an MCP server's would be; it records its calls."""
    deferred = True
    parameters = schema({"x": {"type": "string"}}, [])

    def __init__(self, name, description):
        self.name, self.description, self.calls = name, description, []

    def run(self, ctx, **args):
        self.calls.append(args)
        return f"{self.name} ran"


def deferred():
    return {n: Remote(n, d) for n, d in SPECS}


def context(tools, loaded):
    return ToolContext(paths=None, deferred=tools, load=loaded.extend)


def make_agent(tmp_path, script, tools=None):
    tools = {**default_tools(), **(tools if tools is not None else deferred()), "tool_search": ToolSearch()}
    return Agent(Fake(script), "m", Log.new(cwd=str(tmp_path)), tools, "sys")


def sent_names(agent):
    """The tools the model sees (Tarjuman shows the deferred ones once a ToolsAdded names them)."""
    return [d.name for d in visible_tools(agent.definitions(), agent.log.messages())]


# --- the plumbing (works now) ---------------------------------------------------------------

def test_deferred_tools_are_sent_hidden_until_loaded(tmp_path):
    agent = make_agent(tmp_path, [])
    sent = agent.definitions()
    names = sent_names(agent)
    assert "tool_search" in names and "read" in names
    assert not any(n.startswith(("gh__", "cal__")) for n in names)
    assert all(d.deferred for d in sent if d.name.startswith(("gh__", "cal__")))
    agent._load(["cal__list_events", "gh__create_issue", "read", "nope"])   # only real deferred ones
    agent._flush_loads()                                                      # (the step's end)
    assert agent.definitions() == sent                                        # the list never changes
    assert sent_names(agent)[-2:] == ["cal__list_events", "gh__create_issue"]   # seen, in load order
    assert agent.log.messages()[-1].content == [ToolsAdded(["cal__list_events", "gh__create_issue"])]
    agent._load(["gh__create_issue"])                                          # loading twice: no change
    agent._flush_loads()
    assert sent_names(agent).count("gh__create_issue") == 1


def test_a_summary_keeps_the_loaded_tools_seen(tmp_path):
    from diwan.log import Kind
    agent = make_agent(tmp_path, [Message("assistant", [Text("ok")])] * 2)
    agent.turn("first")
    agent._load(["db__run_query"])
    agent._flush_loads()
    agent.turn("second")
    agent.log.append(Kind.SUMMARY, {"cut": len(agent.log.messages()) - 1, "text": "summary"})
    view = agent.context_manager.view()
    assert view[1].content == [ToolsAdded(["db__run_query"])]               # right after the summary
    assert "db__run_query" in [d.name for d in visible_tools(agent.definitions(), view)]


def test_calling_a_tool_before_loading_it_is_refused(tmp_path):
    tools = deferred()
    script = [Message("assistant", [ToolCall("c1", "gh__close_issue", json.dumps({}))]),
              Message("assistant", [Text("ok")])]
    agent = make_agent(tmp_path, script, tools)
    agent.turn("close it")
    result = agent.log.messages()[2].content[0]
    assert result.is_error and "tool_search" in result.content[0].text
    assert tools["gh__close_issue"].calls == []


def test_loaded_tools_survive_a_resume_and_go_with_a_rewind(tmp_path):
    agent = make_agent(tmp_path, [Message("assistant", [Text("ok")])] * 2)
    agent.turn("first")
    agent._load(["db__run_query"])
    agent._flush_loads()
    again = Log.load(agent.log.path)
    assert again.loaded_tools() == ["db__run_query"]
    again.rewind(1)                         # back to before "first": the load is on the old branch
    assert again.loaded_tools() == []


def test_the_instructions_list_each_servers_tool_names():
    from diwan import mcp
    s1, s2 = mcp.Server("github", {}), mcp.Server("calendar", {})
    s1.instructions = "GitHub issues, pull requests and code search.\nMore lines."
    tools = {}
    for s, n in [(s1, "create_issue"), (s1, "list_issues"), (s2, "list_events")]:
        t = mcp.MCPTool(s, {"name": n, "description": f"{n} does things"})
        tools[t.name] = t
    text = mcp.prompt_section(mcp.Connected([s1, s2], tools, {}))
    assert "- github: GitHub issues, pull requests and code search.\n  create_issue, list_issues" in text
    assert "- calendar\n  list_events" in text
    assert "mcp__github__" not in text.split("mcp__<server>__<tool>")[1]   # names without the prefix
    assert "does things" not in text and "More lines" not in text       # definitions come with search
    assert mcp.prompt_section(None) == ""


# --- tool_search itself (yours: these fail until ToolSearch.run is written) ---------------

def test_select_loads_exact_tools_and_returns_their_definitions():
    loaded = []
    out = ToolSearch().run(context(deferred(), loaded), "select:cal__create_event,gh__merge_pull_request")
    assert loaded == ["cal__create_event", "gh__merge_pull_request"]
    assert "Create an event in the calendar." in out and '"parameters"' in out


def test_select_with_an_unknown_name_says_so():
    loaded = []
    with pytest.raises(ToolError, match="gh__nope"):
        ToolSearch().run(context(deferred(), loaded), "select:gh__nope")
    assert loaded == []


def test_select_with_a_server_name_lists_its_tools():
    with pytest.raises(ToolError, match="cal__create_event, cal__list_events, cal__delete_event"):
        ToolSearch().run(context(deferred(), []), "select:cal")


def test_keywords_load_the_best_matches_first():
    loaded = []
    ToolSearch().run(context(deferred(), loaded), "create issue")
    assert loaded[0] == "gh__create_issue"
    assert 1 <= len(loaded) <= 5


def test_keywords_that_match_nothing_load_nothing():
    loaded = []
    out = ToolSearch().run(context(deferred(), loaded), "weather forecast")
    assert loaded == [] and out   # an answer that says nothing matched, not an empty string


def test_search_then_call_in_the_next_step(tmp_path):
    tools = deferred()
    search = json.dumps({"query": "select:db__list_tables"})
    script = [Message("assistant", [ToolCall("c1", "tool_search", search)]),
              Message("assistant", [ToolCall("c2", "db__list_tables", json.dumps({}))]),
              Message("assistant", [Text("done")])]
    agent = make_agent(tmp_path, script, tools)
    agent.turn("what tables are there?")
    assert tools["db__list_tables"].calls == [{}]
    assert "db__list_tables" in sent_names(agent)


def test_the_load_is_logged_after_the_search_result(tmp_path):
    search = json.dumps({"query": "select:db__list_tables"})
    agent = make_agent(tmp_path, [Message("assistant", [ToolCall("c1", "tool_search", search)]),
                                  Message("assistant", [Text("ok")])])
    agent.turn("load it")
    roles = [(m.role, type(m.content[0]).__name__) for m in agent.log.messages()]
    assert roles[1:4] == [("assistant", "ToolCall"), ("tool", "ToolResult"), ("system", "ToolsAdded")]
