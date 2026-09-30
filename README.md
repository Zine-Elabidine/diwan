# Diwan

An agent runtime that keeps a permanent record of everything your agent does, and
speaks every model's language. Built from the loop up: no framework underneath.

> A *diwan* (ديوان) was the register where everything was written down and nothing
> erased: Caliph Umar's register of every soldier, the chancery, the council, a poet's
> collected works. The word crossed Persian, Arabic, Turkish, Italian (*dogana*),
> Spanish (*aduana*) and French (*douane*).

**Status:** v0 works: a terminal coding agent with four tools (read, write, edit, bash),
approvals, retries, and a tree-shaped JSONL log of every session in `~/.diwan/sessions/`.
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
```

Inside a session: `/model [provider:]<id>` switches model, `/models [provider] [text]` lists
the catalog with prices, `/cost`, `/think`, `/new`, `/help`.

## Layout

```
docs/reading-list.md   what to read in each reference runtime, and in what order
scripts/fetch-refs.sh  clone the reference runtimes into refs/ (gitignored)
refs/                  22 open-source runtimes, read-only, never committed
```
