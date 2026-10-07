"""The summary tier, step (b2): summaries in the running agent. Clearing comes first; a summary
when the context is still past the trigger; /compact; the off switch; failures never end the
turn; a "too long" error is summarized away once."""

from types import SimpleNamespace

from tarjuman import Message, TarjumanError, Text, errors
from tarjuman.fake import Fake

from diwan import commands, summary
from diwan.agent import Agent, Limits
from diwan.events import ContextSummarized
from diwan.log import Log
from diwan.tools import default_tools

NOTE = "## Goal\nwrite the essay"


def reply(text, **kw):
    return Message("assistant", [Text(text)], **kw)


def make(tmp_path, script, exchanges=8, size=12_000, **limits):
    """A session of long user messages (clearing can't shrink them): ~89% of 36k usable."""
    log = Log.new(cwd=str(tmp_path))
    for i in range(exchanges):
        log.add_message(Message.user(f"part {i}: " + "x" * size))
        log.add_message(reply(f"read part {i}"))
    events = []
    a = Agent(Fake(script, window=40_000), "fake-model", log, default_tools(), "sys",
              on=events.append, limits=Limits(max_tokens=4_000, **limits), sleep=lambda s: None)
    return a, events


def notices(events):
    return [e.text for e in events if isinstance(e, ContextSummarized)]


def test_a_full_context_is_summarized_before_the_next_request(tmp_path):
    a, events = make(tmp_path, [reply(NOTE), reply("done")])
    assert summary.due(a.context_use.used, a.context_use.usable)
    a.turn("next")
    asked = a.provider.requests[0]                          # the summarizer's request
    assert len(asked) == 1 and "<transcript>" in asked[0].text
    sent = a.provider.requests[1]                           # the turn's request
    assert sent[1].role == "user" and sent[1].text.startswith(summary.HEADER)
    cut = a.log.summary().cut
    newest = max(i for i, m in enumerate(a.log.messages()[:cut]) if m.role == "user") // 2
    assert NOTE in sent[1].text and f"- part {newest}: xxx" in sent[1].text   # copied, newest kept
    assert a.log.summary() is not None
    assert notices(events) and notices(events)[0].startswith("summarized ")
    assert a.context_use.used < a.context_use.usable * summary.TARGET * 1.5


def test_the_off_switch_sends_everything(tmp_path):
    a, events = make(tmp_path, [reply("done")], summaries=False)
    a.turn("next")
    assert a.log.summary() is None and not notices(events)
    assert len(a.provider.requests) == 1


def test_a_failed_summary_never_ends_the_turn(tmp_path):
    a, events = make(tmp_path, [TarjumanError(errors.INVALID_REQUEST, "bad"), reply("done")])
    ended = a.turn("next")
    assert ended.reason == "done" and a.log.summary() is None
    assert notices(events) == ["could not summarize (INVALID_REQUEST); going on without"]
    assert any(e.type == "error" and "summary" in e.data["message"] for e in a.log.events)


def test_a_cut_off_note_is_not_stored(tmp_path):
    a, events = make(tmp_path, [reply("## Goal\nwri", stop="max_tokens"), reply("done")])
    assert a.turn("next").reason == "done"
    assert a.log.summary() is None
    assert notices(events)[0].startswith("could not summarize")


def test_too_long_is_summarized_away_once(tmp_path):
    a, _ = make(tmp_path, [TarjumanError(errors.CONTEXT_WINDOW_EXCEEDED, "too long"),
                                reply(NOTE), reply("done")], exchanges=4)
    assert not summary.due(a.context_use.used, a.context_use.usable)   # the gauge saw no problem
    assert a.turn("next").reason == "done"
    assert a.log.summary() is not None and len(a.provider.requests) == 3


def test_too_long_twice_ends_the_turn(tmp_path):
    too_long = TarjumanError(errors.CONTEXT_WINDOW_EXCEEDED, "too long")
    a, _ = make(tmp_path, [too_long, reply(NOTE), too_long], exchanges=4)
    ended = a.turn("next")
    assert ended.reason == "error" and len(a.provider.requests) == 3


def test_compact_summarizes_now_and_the_switch_turns_auto_off(tmp_path):
    a, events = make(tmp_path, [reply(NOTE)], exchanges=4)
    a.compact()
    assert a.log.summary() is not None and notices(events)[0].startswith("summarized ")
    s = SimpleNamespace(agent=a)
    assert commands.run(s, "/compact").effect == "compact"   # the front-end runs it
    assert commands.run(s, "/compact off").text == "automatic summaries off"
    assert a.limits.summaries is False
    assert commands.run(s, "/compact off", busy=True).kind != "error"   # allowed while working
    assert commands.run(s, "/compact", busy=True).kind == "error"


def test_compact_with_nothing_to_summarize_says_so(tmp_path):
    a, events = make(tmp_path, [], exchanges=0)
    a.compact()
    assert notices(events) == ["nothing to summarize yet"] and not a.provider.requests
