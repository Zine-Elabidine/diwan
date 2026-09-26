# Reading list

Twenty-two runtimes, cloned shallow into `refs/` on 2026-09-26. Read in tiers:
the small ones first, so the big ones read as "the same loop plus X".

For each one, answer the same questions (notes go in `docs/notes/<name>.md`):

1. **Loop.** Where is the while-loop? What ends it?
2. **Messages.** What is the internal message/event type? Provider-neutral or not?
3. **Tools.** How is a tool defined, validated, executed? Parallel calls?
4. **Context.** What happens when the window fills? (truncate, summarise, compact)
5. **Control.** Approvals, interrupts, undo, resume after a crash?
6. **Extension.** MCP, hooks, plugins, subagents?
7. **What I'd steal / what I'd do differently.**

## Tier 1: the bare loop

| Repo | Lang | Commit | Start here |
|---|---|---|---|
| mini-swe-agent | Py | 04d809c | `src/minisweagent/agents/default.py` (190 lines, the whole agent) |
| pi-mono | TS | 2b0a123 | `packages/agent/src/agent-loop.ts`, then `packages/coding-agent` |
| smolagents | Py | 227ef5e | `src/smolagents/agents.py` (ToolCallingAgent vs CodeAgent) |

## Tier 2: agent SDKs (the loop as a library)

| Repo | Lang | Commit | Start here |
|---|---|---|---|
| openai-agents-python | Py | 588826c | `src/agents/run.py`: handoffs, guardrails, tracing |
| pydantic-ai | Py | a383d75 | `pydantic_ai_slim/pydantic_ai/_agent_graph.py`: the loop as a graph |
| strands-agents | Py | c56b7de | `strands-py/src/strands/event_loop/event_loop.py` |
| langgraph | Py | 7daa3ab | `libs/langgraph`: state graphs, checkpoints (`libs/checkpoint`) |
| openhands-sdk | Py | d77ada7 | `openhands-sdk/openhands/sdk/agent/agent.py`, then `conversation/`, `context/` |
| claude-agent-sdk-python | Py | 36f9548 | `src/claude_agent_sdk/_internal/query.py`: a thin client over the Claude Code binary, read for the protocol, not the loop |

## Tier 3: full products (the loop plus everything around it)

| Repo | Lang | Commit | Start here |
|---|---|---|---|
| aider | Py | 5dc9490 | `aider/coders/base_coder.py`: edit formats, repo map |
| opencode | TS | b471c2b | `packages/opencode/src/session/`: `processor.ts`, `compaction.ts`, `revert.ts` |
| gemini-cli | TS | 2fe7c2d | `packages/core/src/core/client.ts`, `geminiChat.ts` |
| codex | Rust | 75a7148 | `codex-rs/core/src/session/turn.rs`, `compact.rs`, `exec_policy.rs`, sandboxing |
| goose | Rust | 04ed836 | `crates/goose/src/agents/agent.rs`, `crates/goose-context-management` |

## Tier 4: the 2026 wave

Newer runtimes, read after tiers 1-3 so their ideas stand out against the baseline.

| Repo | Lang | Commit | Start here |
|---|---|---|---|
| deepseek-harness | TS | 477b4f4 | `packages/core/agent-loop`, then `packages/compaction`, `packages/sandbox`: everything is a plugin, even the loop |
| hermes-agent | Py | 959c764 | `run_agent.py`, then `agent/context_*` and memory: an agent that learns how you work |
| openclaw | TS | d9fc255 | `src/agents`, `src/context-engine`, `src/channels`: a personal agent reached through chat apps |
| trueforge | TS | 7ab8b61 | `packages/trueforge/src/runtime`: a server-side loop with approvals and resumable sessions |
| qm | TS | 8adee4b | `src/harness/`: a harness *over* harnesses (Claude, Codex, opencode, pi); see `harness-router.ts`, `tape-*.ts` |
| crush | Go | 68d768c | `internal/agent/agent.go`: the loop in Go, plus `permission/`, `history/` |
| dirac | TS | 46d33ad | `src/core/context`: context curation, the reason it tops TerminalBench |
| microsoft-agent-framework | Py | 6f1522a | `python/packages/core/agent_framework/_agents.py`, `_compaction.py`, `_harness/` |

Also newer: the **Strands Harness** (AWS, 2026-09-21) lives in the `strands-agents`
clone under `harness-py/` and `harness-ts/`.

## Themes to compare across tiers

- **Compaction:** deepseek-harness `packages/compaction`, hermes `agent/context_compressor.py`, opencode `compaction.ts`, codex `compact*.rs`, goose `goose-context-management`, openhands `context/`
- **Sandboxing / exec policy:** codex `exec_policy*`, `bwrap`; openhands `workspace/`
- **Undo / revert:** opencode `revert.ts`, aider git integration
- **Durability / resume:** langgraph checkpoints, pi-mono `durable`, openhands `conversation/`
