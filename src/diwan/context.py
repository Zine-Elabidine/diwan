"""What the model sees of the conversation, how full that is, and keeping it from filling.

The tiers are in docs/research-context.md §5: the newest tool outputs stay verbatim, older
ones are cleared (clearing.py), and the oldest part is summarized (a "summary" event). The log
keeps everything; this module only decides what each request carries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tarjuman import Message, Provider, Tool, tokens

from . import clearing
from .log import Kind, Log
from .tools.notes import KINDS

# another model's chars-per-token, reused for this one, assumes 15% more tokens (tokenizers differ;
# measured: DeepSeek 3.08, Claude 2.68 on the same session)
OTHER_TOKENIZER = 0.85


@dataclass
class ContextUse:
    """How full the context is, in one model's own tokens."""
    used: int             # tokens the next request will hold
    window: int | None    # the model's context window, if known
    usable: int | None    # the window minus the room kept for the answer
    exact: bool           # True right after a reply from this model; otherwise partly estimated
    chars_per_token: float = tokens.CHARS_PER_TOKEN   # the ratio `used` was counted at

    @property
    def fraction(self) -> float | None:
        return self.used / self.usable if self.usable else None

    @property
    def overflows(self) -> bool:
        return self.usable is not None and self.used > self.usable


class ContextManager:
    def __init__(self, log: Log, answer_room: int, cap: int | None = None):
        """answer_room: tokens kept free for the model's reply (its max_tokens).
        cap: an upper bound on the window (--context), also used when the window is unknown."""
        self.log = log
        self.answer_room = answer_room
        self.cap = cap

    def view(self) -> list[Message]:
        """The conversation as the model sees it: the log's messages, with cleared outputs.
        After a summary, its text (as stored, as a user message) replaces the first `cut`
        messages; message `cut` onward is sent as before."""
        s = self.log.summary()
        if s is None:
            return clearing.apply(self.log.messages(), self.log.masked())
        rest = self.log.messages()[s.cut:]
        return [Message.user(s.text), *clearing.apply(rest, self.log.masked())]

    def notes(self) -> list[tuple[str, str]]:
        """The model's notes on this branch, oldest first, as (kind, text). Read from the log,
        not the view, so none is lost when older messages stop being sent."""
        out = []
        for m in self.log.messages():
            for c in m.tool_calls:   # finished calls only: interrupted ones sit in `partial`
                if c.name != "note":
                    continue
                try:
                    a = c.args()
                except ValueError:
                    continue
                if a.get("kind") in KINDS and str(a.get("text") or "").strip():
                    out.append((a["kind"], str(a["text"]).strip()))
        return out

    def measure(self, system: str, tools: list[Tool], provider: Provider,
                model: str) -> ContextUse:
        """How many tokens the next request holds, counted for `model`. Exact from this model's
        last usage report, plus an estimate for what came after it, at a chars-per-token ratio
        calibrated on that report. With no report from this model (a new session, or right
        after a switch) the whole history is estimated."""
        request = [Message.system(system), *self.view()]
        s = self.log.summary()
        offset = 2 - s.cut if s else 1   # log message k is request[k + offset]
        used, exact, ratio, borrowed = None, False, tokens.CHARS_PER_TOKEN, None
        for i in range(len(request) - 1, 0, -1):
            m = request[i]
            u = m.usage
            if m.role != "assistant" or u is None:
                continue
            k = i - offset
            if s and k < s.point:
                # reported before the summary: that prompt still held the summarized messages,
                # so neither its count nor its ratio fits this request (nor do older reports)
                break
            prompt = u.input + u.cache_read + u.cache_write
            if not prompt:
                continue
            if m.model != model or m.provider != provider.provider:
                if borrowed is None:   # the most recent other model's ratio, as a fallback
                    borrowed = tokens.ratio(request[:i], tools, prompt) or 0.0
                continue
            ratio = tokens.ratio(request[:i], tools, prompt) or tokens.CHARS_PER_TOKEN
            if any(point > k for point in self.log.mask_points()):
                # outputs were cleared after this report: the reported count is too high now
                used = tokens.estimate(request, tools, ratio)
                break
            after = request[i + 1:]
            used = prompt + u.output + (tokens.estimate(after, None, ratio) if after else 0)
            exact = not after
            break
        if used is None:
            # no report from this model: the same text measured on another model, plus a margin
            # for a different tokenizer; else the default
            ratio = min(borrowed * OTHER_TOKENIZER, tokens.CHARS_PER_TOKEN) if borrowed \
                else tokens.CHARS_PER_TOKEN
            used = tokens.estimate(request, tools, ratio)
        window = _ask(provider.context_window, model)
        if self.cap:
            window = min(window, self.cap) if window else self.cap
        info = _ask(provider.info, model)
        reserve = min(self.answer_room, info.max_output) if info and info.max_output \
            else self.answer_room
        return ContextUse(used, window, max(window - reserve, 0) if window else None, exact, ratio)

    def clear(self, use: ContextUse) -> clearing.Plan | None:
        """Clear old tool outputs once the context is half full: logged as a "mask" event, so
        every later request is built the same way. None when there is nothing worth clearing."""
        if not use.usable or (use.fraction or 0) < clearing.MASK_AT:
            return None
        s = self.log.summary()   # summarized messages are no longer sent: nothing to clear there
        sent = self.log.messages()[s.cut:] if s else self.log.messages()
        p = clearing.plan(sent, self.log.masked(), use.usable, use.chars_per_token)
        if p is not None:
            self.log.append(Kind.MASK, {"entries": p.entries, "saved": p.saved})
        return p


def _ask[T](lookup: Callable[[str], T | None], model: str) -> T | None:
    """Optional knowledge from the provider (window, catalog info); None if it has none."""
    try:
        return lookup(model)
    except Exception:  # a lookup must never break the session
        return None
