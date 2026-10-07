# Diwan

An agent runtime that keeps a permanent record of everything your agent does, and
speaks every model's language. Built from the loop up: no framework underneath.

> A *diwan* (ديوان) was the register where everything was written down and nothing
> erased: Caliph Umar's register of every soldier, the chancery, the council, a poet's
> collected works. The word crossed Persian, Arabic, Turkish, Italian (*dogana*),
> Spanish (*aduana*) and French (*douane*).

**Status:** v0 works: a terminal coding agent with eight tools (read, grep, glob, write,
edit, bash, note, recall), approvals, retries, long sessions that never run out of context,
and a tree-shaped JSONL log of every session in `~/.diwan/sessions/`.
One conversation can move between models and providers mid-session
(`/model anthropic:claude-sonnet-5-5`, then `/model openrouter:deepseek/deepseek-v4-flash`):
[Tarjuman](https://github.com/Zine-Elabidine/tarjuman) adapts the history, and each model's
own reasoning comes back to it intact.

```
uv tool install -e ~/diwan          # once
echo OPENROUTER_API_KEY=... > ~/.diwan/env && chmod 600 ~/.diwan/env   # or export it
diwan                               # full-screen app in the current folder (--plain for line mode)
diwan -m z-ai/glm-5.3-flash         # any OpenRouter model (default: deepseek/deepseek-v4-flash)
diwan -m anthropic:claude-sonnet-5-5   # [provider:]model; providers: openrouter, anthropic,
                                       #   openai, deepseek, local (keys in ~/.diwan/env)
diwan -p "fix the failing test" -y  # one shot, approve everything
diwan -r                            # resume the last session here
diwan --base-url http://localhost:8000/v1 -m <model>   # local vLLM / llama.cpp
diwan --provider anthropic --base-url <url>   # an Anthropic-compatible gateway
diwan --context 30000               # cap the window (to see compaction early)
diwan --no-summaries                # clear old tool outputs, but never summarize
```

Inside a session: `/model [provider:]<id>` switches model, `/models [provider] [text]` lists
the catalog with prices, `/compact [on|off]`, `/notes`, `/cost`, `/think`, `/new`, `/help`.

## Long sessions

The log is never edited. Every request is rebuilt from it: the *view* is what the model is
sent, and the log keeps what it no longer sees. Keeping the context small is three steps,
cheapest first:

| when | what | cost |
|---|---|---|
| context at 50% | **clear** old tool outputs (the newest stay word for word); a `mask` event records the replacement text | free |
| still at 75% (or 200k tokens) | **summarize** the oldest messages into a handoff note; a `summary` event records where it cut | one request |
| any time | **recall** searches the full log, cleared outputs and summarized messages included | free |

A summary is the model's note (goal, decisions, rejected approaches, next steps...) plus
what code copies from the log word for word: the user's messages, the model's notes, and the
files it changed. The cut never separates a tool call from its result, and never takes the
model's latest message. The next summary updates the note; the copied part is rebuilt.

The note is written *in place*, as Codex and Claude Code do: the conversation is sent again
exactly as the model last saw it, with one request at the end. The provider's prompt cache
covers almost all of it and nothing is shortened. If that request fails, a plain-text
transcript is sent instead, long tool outputs sharing the room. On a 31k-token DeepSeek session
both ways answered the same follow-up questions about as well; in place, 27.4k of the 27.5k
input tokens came from the cache.

Summaries leave details out, so the model is told to re-read or use `recall` for exact values
rather than answer from memory. In an early test without `recall`, DeepSeek invented the
retryable error codes after a summary; with it, it found them in the log.

Background reading and the decision: [docs/research-context.md](docs/research-context.md).

## Layout

```
docs/reading-list.md   what to read in each reference runtime, and in what order
scripts/fetch-refs.sh  clone the reference runtimes into refs/ (gitignored)
refs/                  22 open-source runtimes, read-only, never committed
```
