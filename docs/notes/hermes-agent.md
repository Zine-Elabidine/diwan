# Hermes Agent

`NousResearch/hermes-agent` @ 959c764 · Python · MIT · since 2025-07, ~250k stars.

**In one line:** a *personal* agent that learns. The same core runs behind the CLI/TUI,
a messaging gateway (Telegram, WhatsApp, Slack... ~20 platforms), cron and a desktop app,
and after each turn it asks itself "should I save a memory or a skill from this?".

## Design invariants (from AGENTS.md; the most useful thing in the repo)

- **Prompt caching is sacred.** The system prompt stays byte-stable for the life of a
  conversation. Nothing mutates past context, swaps toolsets or reloads memory mid-session.
  The only exception is compression. Commands that change the prompt apply *next session*
  unless `--now`.
- **Narrow waist.** Every tool schema is paid for on every call, so new capability goes, in
  order of preference: extend code → CLI command + skill → service-gated tool → plugin →
  MCP → new core tool (last resort).
- Strict role alternation; never inject a synthetic user message mid-loop.

## The 7 questions

1. **Loop.** `agent/conversation_loop.py` `run_conversation()` drives one user turn, split
   into phase modules (`turn_preflight_gate`, `turn_request_assembly`, `turn_api_call`,
   `turn_tool_round`, `turn_finalizer`...). A classic while-loop over OpenAI-format
   messages. Plus a repetition guard that interrupts runaway loops.
2. **Messages.** OpenAI chat-completions dicts are the lingua franca, with adapters for
   Anthropic, the Responses API, Bedrock... Sessions are stored in SQLite with FTS5.
3. **Tools.** A central registry, ~250 tool modules (terminal, browser, vision, code
   execution, delegate...). Tools are grouped into *toolsets* and gated by `check_fn`.
   **Code execution via RPC:** the model writes a Python script that calls tools, collapsing
   a multi-step pipeline into one turn (same idea as DeepSeek's PTC).
4. **Context.** `conversation_compression*.py` runs on a background thread with a commit
   fence (work finishing after a timeout is discarded). `prompt_caching.py`: 4 Anthropic
   breakpoints (static system prefix, end of system, last 2 messages).
5. **Control.** Approval system (`tools/approval_*`: smart, human-wait, gateway-wait for
   chat apps), interrupt-and-redirect, checkpoints, 7 terminal backends (local, Docker,
   SSH, Singularity, Modal, Daytona, Vercel Sandbox).
6. **Extension.** Skills (agentskills.io format, a hub with install/sync), plugins, MCP,
   one external memory provider at a time (e.g. Honcho), subagents via `delegate_tool`.
7. **The learning loop (its signature):**
   - `memory` tool: `MEMORY.md` (agent notes) + `USER.md` (user profile), injected as a
     **frozen snapshot** at session start. Writes during the session hit disk, not the prompt.
   - `background_review.py`: after a turn, a daemon thread **forks the agent** with the same
     cached prefix and a tool whitelist, replays the conversation, and decides whether to
     write or update memories and skills. The main conversation never sees it.
   - `/learn`: turns a URL, code dir or "what we just did" into a skill.
   - `session_search`: FTS5 over past sessions + LLM summary = cross-session recall.

## Steal / avoid

- Steal: the frozen-snapshot memory (cache-safe); the forked background reviewer that reuses
  the prefix cache; the footprint ladder; USER.md as a first-class object.
- Avoid: god-files (`run_agent.py`, `conversation_loop.py` are still ~1.7k lines each after
  extraction); OpenAI-dict messages everywhere mean lots of provider special-casing
  (`_is_copilot_url`, `_is_ollama_glm_backend`...).

## What it provides

TUI, CLI, desktop, 20 chat platforms, cron, voice, browser, 7 sandboxes, skills hub,
memory providers, trajectory export for RL training.
