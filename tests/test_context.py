"""The context gauge: counted in the current model's tokens, exact after its replies,
estimated in between and after a switch, and a switch that doesn't fit is refused."""

import pytest

from tarjuman import Message, TarjumanError, Text, Usage, limits
from tarjuman.fake import Fake

from diwan.agent import Agent, Limits
from diwan.log import Log
from diwan.tools import make_tools

from test_agent import call, home  # noqa: F401  (the fixture isolates DIWAN_HOME)


def reply(text, prompt, output=10, **kw):
    return Message("assistant", [Text(text)], usage=Usage(input=prompt, output=output), **kw)


def make(tmp_path, script, window=100_000, max_tokens=16_000):
    log = Log.new(cwd=str(tmp_path))
    return Agent(Fake(script, window=window), "fake-model", log, make_tools(tmp_path), "sys",
                 limits=Limits(max_tokens=max_tokens), sleep=lambda s: None)


def test_a_new_session_is_estimated_against_the_window_minus_the_answer(tmp_path):
    a = make(tmp_path, [])
    ctx = a.context_use
    assert not ctx.exact and ctx.used > 0
    assert (ctx.window, ctx.usable) == (100_000, 84_000)


def test_exact_right_after_a_reply_from_this_model(tmp_path):
    a = make(tmp_path, [reply("hello", prompt=1_234, output=56)])
    a.turn("hi")
    assert a.context_use.exact and a.context_use.used == 1_234 + 56


def test_what_came_after_the_reply_is_estimated_at_the_calibrated_ratio(tmp_path):
    a = make(tmp_path, [reply("ok", prompt=4_000, output=10)])
    a.turn("x" * 8_000)
    sent = [Message.system("sys"), Message.user("x" * 8_000)]
    tools = [s.tool for s in a.tools.values()]
    r = limits.ratio(sent, tools, 4_000)                    # this model's chars per token
    late = Message.user("y" * 2_000)                        # arrives after the report
    a.log.add_message(late)
    ctx = a.context()
    assert not ctx.exact
    assert ctx.used == 4_010 + limits.estimate([late], None, r)
    assert r < limits.CHARS_PER_TOKEN                       # calibrated, not the default


def test_another_model_lends_its_ratio_with_a_margin_never_its_count(tmp_path):
    sent = [Message.system("sys"), Message.user("w" * 9_000)]
    tools = [s.tool for s in make_tools(tmp_path).values()]
    prompt = round(limits.chars(sent, tools) / 2.5)          # this model: 2.5 chars per token
    a = make(tmp_path, [reply("ok", prompt=prompt, output=10)])
    a.turn("w" * 9_000)
    other = Fake([], window=1_000_000)
    other.provider = "other"
    ctx = a.context(other, "big-model")
    assert not ctx.exact
    full = [Message.system("sys"), *a.log.messages()]
    ratio = limits.ratio(sent, tools, prompt) * 0.85           # 15% more tokens, to be safe
    assert ctx.used == limits.estimate(full, tools, ratio)


def test_a_switch_that_doesnt_fit_is_refused_and_changes_nothing(tmp_path):
    a = make(tmp_path, [], window=1_000_000)
    a.log.add_message(Message.user("z" * 200_000))         # ~50k tokens
    small = Fake([], window=40_000)
    small.provider = "small"
    with pytest.raises(TarjumanError) as e:
        a.use(small, "tiny-model", "sys")
    assert e.value.code == "CONTEXT_WINDOW_EXCEEDED" and "tiny-model" in str(e.value)
    assert a.model == "fake-model"
    big = Fake([], window=2_000_000)
    big.provider = "big"
    a.use(big, "huge-model", "sys")
    assert a.model == "huge-model" and not a.context_use.exact


def test_unknown_window_means_no_percentage(tmp_path):
    a = make(tmp_path, [], window=None)
    assert a.context_use.usable is None and a.context_use.fraction is None
