# Comparison and options

## Side by side

| | DeepSeek Harness | Hermes | Claude Code | Codex | LangGraph / LangChain |
|---|---|---|---|---|---|
| Shape | plugin microkernel | personal agent + gateway | coding CLI, file-based extensions | coding engine behind JSON-RPC server | graph runtime + agent factory |
| Truth | append-only event log | OpenAI messages + SQLite | JSONL transcript | rollout JSONL | checkpoint per super-step |
| Loop | step/turn state machine | phased while-loop | while tool_calls | `run_turn` loop + steering | Pregel tick |
| Context | threshold + retain 16%, pruners | background compression, cache-sacred | auto-compact, subagents | handoff-summary compaction | summarization middleware |
| Control | approval seam, 3 sandbox modes | approvals, 7 backends | permission modes + rules, sandbox | OS sandbox + execpolicy | `interrupt()` + time travel |
| Extension | everything is a plugin | skills, plugins, 1 memory provider | hooks, skills, agents, plugins | MCP both ways, hooks, skills | middleware, subgraphs |
| Signature idea | "model-visible means logged" | learns from each turn | progressive disclosure | app-server + policy-as-tests | checkpoints = HITL + replay |

## What all five agree on (the core we must build no matter what)

1. A loop: sample → run tools → append → repeat until no tool calls.
2. **A durable log as the truth**, with the model's context derived from it. Every serious
   runtime converged here (event log, rollout, transcript, checkpoints).
3. Provider adapters over one internal message type.
4. Tools with schemas, parallel execution, results committed in order.
5. Context management: compaction as a handoff summary, keep the recent tail verbatim.
6. A control layer: approvals, interrupts, sandbox.
7. Extension points: MCP, skills with progressive disclosure, hooks.

## Where they leave room (from feedback.md)

- Nobody shows **cost per successful task** or makes the harness's own token overhead visible.
- Only LangGraph does real **time travel** (fork and rewind any step), and it needs a graph DSL to get it.
- Personal agents are **bloated and hard to trust**; builders rebuild the one or two parts
  they want on a minimal core.
- The big products are too large to read; **small and observable** is what builders praise.

## Three directions (same core, different surface)

**A. Lean observable harness.** Minimal core (~2k lines), model-agnostic, every token
accounted for, cost per task, a built-in eval suite, benchmarked against pi and Claude Code on
the same tasks. *Proves:* you understand where harness cost and quality come from. Closest
to the HarnessTax research, and strongest as a portfolio piece.

**B. Personal agent, lean and trustworthy.** Hermes's good ideas (frozen memory
snapshot, background reviewer, cron, Telegram) on a small core, sandboxed by default,
no bundled skill zoo. *Proves:* memory and long-lived agents. It's also something you'd use
daily (logging, reminders, study, job search).

**C. Time-travel runtime.** The event log as the product: fork, rewind, edit and replay any
step, with a UI to inspect a session like a debugger. LangGraph's best idea without the
graph. *Proves:* durable execution and agent debugging, which is hard and rarely done well.

All three start with the same v0: the loop, the log, 2 providers, 4 tools. The choice only
matters from v1 on.
