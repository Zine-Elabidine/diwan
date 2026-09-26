# DeepSeek Harness (dsh)

`deepseek-ai/deepseek-harness` @ 477b4f4 · TypeScript · MIT · released 2026-08-13, developer preview.

**In one line:** a microkernel. There's no core to patch; every piece, including the
agent loop, is a Cordis plugin, and the product is a YAML-composed plugin tree.

## How it's built

- **Cordis** (their DI/effects framework): plugins contribute *services* (`ctx.llm`,
  `ctx.tools`, `ctx.sessions`, `ctx.agents`...), *typed events* and *reversible effects*.
  Unloading a plugin unwinds everything it registered. Hot reload comes for free.
- **Profiles and bundles:** `web`, `headless`, `sdk`, `acp` are profiles that stack bundles
  (`dsh-base` = adapters, tools, persistence, sandbox, approval...). The user patches any row
  with `cordis.patch.yml`. `dsh --dump-config` prints the whole tree.
- **Capability seams:** each swappable capability comes in three parts: a Service Definition
  (interface), a Provider (implementation) and a Consumer (usually a model-facing tool).
  Point fs and subprocess at a remote sandbox and Bash, PTY and LSP all move with it.

## The 7 questions

1. **Loop.** `packages/core/agent-loop/src/agent.ts` (`ReactLoopAgent`, ~670 lines). A *step*
   is one model request plus its tool calls. A *turn* is zero or more steps and closes "once
   nothing is owed" (no pending tool results, no queued input). One inbox feeds the driver.
2. **Messages.** An **append-only session event log** is the source of truth: `turn/*`,
   `step/*`, `user/message`, `assistant/message`, `tool/call`, `tool/result`...
   Model history is *derived* from it (`deriveMessages()`). Invariant: **"model-visible means
   logged"**. A runtime check proves every request can be rebuilt from the log. Fork,
   resume, transcripts and telemetry are all projections of the same log.
3. **Tools.** A registry with a typed schema DSL and mandatory output schemas. Pipeline:
   `tools/pre-execute → execute → post-execute`. Scheduling (`tool-calls.ts`): *exclusive*
   calls form barriers, *parallel-safe* calls run in a bounded pool (default 10), and results
   always commit in model order. On abort, unstarted calls get synthetic error results so
   replay stays valid. Also has **PTC** (programmatic tool calling: the model writes a program
   that calls tools).
4. **Context.** `packages/compaction/*` as plugins. Trigger `floor(min(0.8W, W − O − 64k))`;
   keeps the newest 16% verbatim and summarises the rest; condenses and retries on overflow;
   `/compact`; separate pruners for big tool results and images.
5. **Control.** An approval seam (`ctx.approval`) with an answerer waterfall (UI, ACP,
   policy) and audit events; sandbox modes `read-only / workspace-write / danger-full-access`
   (bwrap/Landlock, Seatbelt, Windows ACL); cancellation with explicit causes; fork a session
   at any turn boundary.
6. **Extension.** Everything. MCP client, ACP, skills, hooks. Subagent providers can even be
   *other products* (`subagent-codex`, `subagent-claude-code`, `subagent-acp`). Also agent
   teams (roster, task board, mailbox), goals, jobs, schedules, webhooks.
7. **Steal / avoid.**
   - Steal: the event log as the only truth; "model-visible means logged"; step vs turn;
     ordered commit of parallel tool results; synthetic results on abort.
   - Avoid: the ceremony. Branded ids, projections and a 3-role seam for everything make a
     huge surface (~100 packages). Great for a platform, heavy for one person.

## What it provides out of the box

Web UI, desktop app (Electron), TS and Python SDKs, ACP, ~40 providers, sandboxing, MCP,
skills, subagents, compaction, sessions with fork/resume.
