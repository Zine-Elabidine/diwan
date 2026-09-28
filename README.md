# Diwan

An agent runtime that keeps a permanent record of everything your agent does, and
speaks every model's language. Built from the loop up: no framework underneath.

> A *diwan* (ديوان) was the register where everything was written down and nothing
> erased: Caliph Umar's register of every soldier, the chancery, the council, a poet's
> collected works. The word crossed Persian, Arabic, Turkish, Italian (*dogana*),
> Spanish (*aduana*) and French (*douane*).

**Status:** v0 works: a terminal coding agent with four tools (read, write, edit, bash),
approvals, retries, and a tree-shaped JSONL log of every session in `~/.diwan/sessions/`.

```
uv tool install -e ~/diwan          # once
export OPENROUTER_API_KEY=...       # https://openrouter.ai/keys
diwan                               # chat in the current folder
diwan -m deepseek/deepseek-v4-flash # any OpenRouter model
diwan -p "fix the failing test" -y  # one shot, approve everything
diwan -r                            # resume the last session here
diwan --base-url http://localhost:8000/v1 -m <model>   # local vLLM / llama.cpp
```

## Layout

```
docs/reading-list.md   what to read in each reference runtime, and in what order
scripts/fetch-refs.sh  clone the reference runtimes into refs/ (gitignored)
refs/                  22 open-source runtimes, read-only, never committed
```
