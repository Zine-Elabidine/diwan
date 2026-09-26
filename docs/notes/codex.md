# Codex (OpenAI)

`openai/codex` @ 75a7148 · Rust (`codex-rs/`, ~100 crates) · Apache-2.0.

**In one line:** a production coding agent whose engine is a Rust library behind a JSON-RPC
**app-server**. The TUI, the IDE extension, `codex exec` and the desktop app are all clients
of the same server (`thread/start`, `turn/start`...).

## The 7 questions

1. **Loop.** `core/src/session/turn.rs` `run_turn()`. Its doc comment is the clearest
   statement of the loop in any repo: *"the model replies with either requested function
   calls or an assistant message. If a function call, execute it and send the output back in
   the next sampling request. If only an assistant message, record it and consider the turn
   complete."* Each iteration: drain **pending input** (messages the user typed while the
   model was running, i.e. steering), run hooks, capture a `StepContext` (model, tools, MCP
   servers frozen for this request), maybe auto-compact, sample, dispatch tools, repeat.
2. **Messages.** OpenAI **Responses API** items (`ResponseItem`) are the internal format,
   wrapped in turn items for the UI. Every session is written to a **rollout** file
   (`rollout/` crate, JSONL + compression). Resume and fork reconstruct from it
   (`rollout_reconstruction.rs`).
3. **Tools.** `core/src/tools/`: registry, router, `parallel.rs` (parallel calls),
   `orchestrator.rs` (approval → sandbox → run → retry without sandbox if the user allows).
   `apply_patch` is its own crate with its own diff grammar. There's also "code mode", where
   the model writes code that calls tools.
4. **Context.** `compact.rs` plus remote compaction (`compact_remote_v2`: OpenAI's servers
   do the summary). The prompt (`prompts/templates/compact/prompt.md`) is 8 lines: a
   *"handoff summary for another LLM that will resume the task"*: progress, decisions,
   constraints, next steps, critical data. The new context is prefixed with "another language
   model started to solve this problem...". Plus `token_budget.rs`, `rollout_budget.rs`.
5. **Control.** Its strongest area:
   - **Sandbox** at the OS level: `linux-sandbox` (bwrap + Landlock + seccomp), macOS
     Seatbelt, a Windows sandbox service. Modes: read-only / workspace-write / full access,
     network off by default.
   - **execpolicy:** Starlark `prefix_rule(pattern, decision=allow|prompt|forbidden,
     justification, match=[...], not_match=[...])`. The `match` examples act as unit tests
     that are validated when the policy loads.
   - Approvals, a "guardian" reviewer, network approval, steering mid-turn, interrupt.
6. **Extension.** MCP (client *and* `codex mcp-server`), AGENTS.md, skills, plugins, hooks
   (pre/post compact, stop, session start), subagents (`multi_agents.rs`, `agent_roles`).
7. **Steal / avoid.**
   - Steal: engine behind a JSON-RPC app-server so every UI is just a client; the rollout
     file as durable truth; execpolicy with examples-as-tests; the handoff-style compaction
     prompt; pending-input steering.
   - Avoid: scale. `turn.rs` alone is 3,167 lines and `session/` is 40k lines of Rust.
     You read Codex for specific mechanisms, not front to back.

## What it provides

TUI, `codex exec` (headless/CI), IDE extension, desktop app, cloud tasks, sandboxing on
3 OSes, MCP both ways, OpenAI models only (plus OSS through `--oss`/providers config).
