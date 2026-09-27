# Design decisions for a harness

**Name (✅ locked 2026-09-27): Diwan.** The register where everything is written and nothing erased
(the tree-shaped log), and a word that crossed every language (the provider library). Family:
the provider library is **Tarjuman** (ترجمان, the interpreter who made every language understood; repo `Zine-Elabidine/tarjuman`); **Tosk** (from Ratatoskr, the squirrel carrying
messages along the World Tree) is reserved for the messaging gateway between you and your agents
(phone, Telegram, approvals, job reports).

Every harness answers the same questions. These are the ones where the runtimes we studied
**actually diverge in code**, with the file that shows each answer. The last column of each
table, "Lean", is a first proposal; the choice is ours to make.

Code refs are relative to `refs/`. Claude Code is closed source; its column comes from
official docs and the Agent SDK, marked *(docs)*.

---

## 1. What is the source of truth?

The single most important decision: almost everything else (resume, fork, undo, compaction,
debugging) follows from it.

| Option | Who | How it looks in code |
|---|---|---|
| **Mutable message list** | Hermes | `conversation_loop.py`: a `messages` list of OpenAI dicts, mutated in place; SQLite for sessions |
| **Append-only event log; context is derived from it** | DeepSeek Harness, Codex, Claude Code *(docs)* | dsh `agent.ts`: every fact is `session.append('turn/start' \| 'user/message' \| 'assistant/message' \| 'tool/call' ...)` and a runtime invariant checks each request can be rebuilt from the log. Codex: the `rollout` crate (JSONL) + `rollout_reconstruction.rs` |
| **Full-state checkpoint after every step** | LangGraph | `pregel/_loop.py` `tick()`: after each super-step the whole state is saved (`durability`: `sync` / `async` / `exit`) |

**Trade-off.** A list is simplest, but compaction destroys history and resume is best-effort.
An event log keeps everything (compaction becomes a *view*, not a deletion), and fork and
replay come free, at the cost of writing a projection (log → messages). Checkpoints
give time travel for any state, not just messages, but store full snapshots.

**✅ LOCKED (2026-09-27): append-only event log, tree-shaped.**
- Every event has an `id` and a `parent`; the session keeps a `head` pointer to the current leaf.
- The prompt is projected from the path root → `head`, never from all events in the file
  (dead branches must not leak into context; enforced by a test).
- Rewind = move `head`; the next event starts a branch. Fork = new session pointing into the old one.
- Compaction is an event; originals are never deleted.
- Storage: one JSONL file per session to start.
- Why a tree over a line: branches (attempts side by side, rewinding a rewind), parallel
  writers without ordering by file position, fork without copying. Cost: one field + a head
  pointer + a projection that walks parents. (Codex's rollout is a line; Claude Code's
  on-disk transcript, as observed from our own session files, is a `uuid`/`parentUuid` tree.)

---

## 2. What does a message look like inside?

| Option | Who |
|---|---|
| Own neutral content blocks + adapter-private `replayState` | dsh `packages/llm`: blocks `text`, `reasoning`, `image`, `file`, `tool-call`, `tool-addition`, `tool-removal`; each assistant message records `provider`, `model` and opaque replay data (signatures, encrypted reasoning) |
| One vendor's wire format as the lingua franca | Hermes (OpenAI chat dicts + adapters, lots of `_is_copilot_url`-style special cases), Codex (OpenAI Responses items), Claude Code (Anthropic blocks) |
| Framework message classes | LangChain (`AIMessage.tool_calls`, `ToolMessage`) |

**Trade-off.** Borrowing a vendor format is fast but leaks: reasoning blocks, thinking
signatures and encrypted reasoning don't map across vendors, so switching models
mid-session breaks or silently drops data. dsh's `replayState` is the clean answer: keep
what the original provider needs, opaque to everyone else.

**✅ LOCKED (2026-09-27): own neutral format + own provider library, as a separate project.**
- Requirements: switch LLM between turns (a session never locks a model); support all providers.
- Neutral content blocks (`text`, `reasoning`, `image`, `tool_call` with raw args, `tool_result`);
  each assistant message records `provider`, `model`, `usage` and an opaque `replay` envelope
  (response-level + per-block), only read by the same provider+protocol+model.
- Model switch rules (from pi-ai `transform-messages.ts`): foreign reasoning → text, encrypted
  reasoning dropped, tool-call ids normalised per target, images → placeholder without vision,
  errored/aborted turns skipped, orphaned tool calls get a synthetic error result.
  A switch is logged as a `model/switch` event.
- Protocol / provider / model kept separate; provider quirks stored **as data** (compat table);
  catalog generated from models.dev + our overrides.
- Translators written by us: OpenAI Chat Completions + Anthropic Messages first; Gemini and
  OpenAI Responses later. No litellm core dependency (lossy OpenAI-shaped format, size,
  March 2026 PyPI compromise); a litellm-backed adapter stays possible for the long tail.
- **Lives in its own repo, `tarjuman`,** and knows nothing about the runtime: messages + tools in, a stream of
  neutral events out. The runtime depends on it as a versioned package.
- References: `notes/llm-layer-dsh.md`, pi-ai `src/api/*`, `src/types.ts` (compat interfaces).

---

## 3. What is the unit of work, and what ends it?

| | Unit | Ends when | Limits |
|---|---|---|---|
| dsh | **turn** = 0..n **steps**; step = 1 model request + its tools | no tool calls, or a tool sets `concludesTurn`, or `max-tokens` (sticky), and nothing is waiting in the inbox | none in the loop |
| Codex | turn → sampling requests | `!needs_follow_up` (no tool calls and no pending user input) | none; stop hooks can refuse to end |
| Hermes | iterations of one user turn | text reply with no tool calls | `max_iterations` (default unlimited), `iteration_budget`, a **wall-clock budget** that injects a wrap-up notice at 80% |
| LangGraph | super-step (tick) | no triggered tasks | `recursion_limit` (default 10,007) |
| Claude Code *(docs)* | turn | no tool calls; Stop hook can block and feed a new prompt (the "Ralph" loop) | `--max-turns` in headless mode |

**Decisions inside this one:**
- **Limits:** step count, token budget, wall-clock, cost, or none. Hermes's "warn at 80% then
  let it wrap up" is kinder than a hard stop.
- **Can something outside the model keep the turn going?** dsh `agent/turn-stopping`, Codex
  stop hooks and CC Stop hooks all can. That single hook is enough for loops like "keep going
  until tests pass".
- **Errors and retries:** dsh routes a failed request through an `agent/request-error` event
  so a plugin decides whether to retry. Hermes has a built-in retry loop plus provider
  fallback. Failed attempts: dsh logs them as `assistant/attempt`, *kept in the log but
  excluded from the model's history*.

**Lean:** turn/step vocabulary; end on no-tool-calls; a budget object (steps, tokens,
seconds, dollars) with a warning before the hard stop; a stop hook.

---

## 4. What happens when the user types while the agent works?

| Option | Who |
|---|---|
| **Queue and inject at the next step boundary** | Codex (`input_queue.get_pending_input` at the top of each loop iteration), dsh (`inbox`: `next-turn` vs `next-step` targets) |
| Interrupt and redirect | Hermes (interrupt-and-redirect), Claude Code *(docs: Esc, queued messages)* |
| Not a runtime concern | LangGraph (the caller decides) |

And **what happens to a half-streamed answer on interrupt?** dsh keeps the partial content
as an assistant message flagged `interrupted: true`; nothing is lost and the model sees
where it stopped.

**Lean:** an inbox with both targets; keep interrupted partials, flagged.

---

## 5. How are tools executed?

Three sub-decisions where all four codebases differ:

**When does a tool start?**
- Codex starts each tool **while the model is still streaming**, as soon as that call's
  item completes (`stream_events_utils.rs`: `handle_output_item_done` → `tool_future`).
- dsh and Hermes wait for the full assistant message.

**What may run in parallel?**

| Who | Policy |
|---|---|
| Codex | each tool declares `supports_parallel_tool_calls`; parallel tools take a **read lock**, others a **write lock** (`tools/parallel.rs`) |
| dsh | per-tool concurrency mode; exclusive calls are barriers, parallel ones use a bounded pool (default 10); **results commit in model order** (`agent-loop/src/tool-calls.ts`) |
| Hermes | an **allowlist** of parallel-safe tools + **path reservations**: readers of the same subtree run together, any overlap with a writer splits the batch (`tool_dispatch_helpers.py` `_plan_tool_batch_segments`) |
| LangGraph `ToolNode` | all calls in parallel |

**What survives a crash or abort?**
- Hermes: **persist before execute**, so a restart after a destructive tool sees that it ran.
- dsh: on abort, calls that never started get **synthetic error results**, so every tool
  call in the log has a matching result and replay stays valid.

**Lean:** per-tool `concurrency: "read" | "write" | "exclusive"` (Codex's lock idea, simple);
commit results in model order; persist the call before running it; synthetic results on abort.
Streaming dispatch is an optimisation for later.

---

## 6. What tools does the model get, and can they change mid-session?

- **Bash-only vs rich tools.** The harness study found bash-only wins for bash-capable
  models at lower cost; predefined tools help weaker models (see `notes/feedback.md`).
- **Edit format.** Codex: its own `apply_patch` diff grammar (a whole crate). Claude Code:
  exact string replace, must Read first *(docs)*. dsh ships both styles (`tool-str-replace-editor`)
  plus an `fs-observation-policy` package (read-before-write). Hermes: `patch` with its own parser.
- **Changing the tool list.** Every tool schema is paid on every call and changing it breaks
  the prompt cache. Hermes forbids changes mid-conversation. dsh records changes as
  `tool-addition` / `tool-removal` **blocks in history**, so the cached prefix survives.
  Claude Code defers rarely used tools behind a search tool *(docs)*. Codex freezes the tool
  list per step (`StepContext`).

**Lean:** small core toolset (bash, read, edit via string replace with read-before-write,
search); tool changes appended to history, never rewriting the prefix.

---

## 7. What happens to a huge tool output?

| Option | Who |
|---|---|
| Truncate | the naive default |
| **Spill to a file, give the model a preview + path** | Hermes (`tool_result_storage.py`: per-tool cap → spill over threshold → per-turn budget), dsh (`spill` seam) |
| Prune old tool outputs before compacting | Hermes, dsh (`compaction-tool-result-pruner`) |

**Lean:** spill + preview + path; the model can read more if it needs it.

---

## 8. How is the context window managed?

| | Trigger | What is kept verbatim | Who writes the summary |
|---|---|---|---|
| dsh | `floor(min(0.8·W, W − out − 64k))` | newest **16%** of the window, all message types | same model by default |
| Codex | token limit reached mid-turn | **only the user's own messages** (most recent, up to 20k tokens) + summary; assistant and tool items dropped (`compact.rs` `build_compacted_history`) | same model, or OpenAI's server (`compact_remote_v2`) |
| Hermes | threshold | **head and tail** protected, middle summarised | a **cheap auxiliary model**; tool outputs pruned first |
| LangChain | `trigger` (tokens and/or messages) | last **20 messages** | configurable |

The summary prompt is a handoff note in both Codex and dsh: progress, decisions,
constraints, next steps, critical data. Codex's is 8 lines.

**Decisions:** trigger formula, what's kept (recent tail vs user messages vs head+tail),
which model summarises, whether compaction is destructive (with an event log it isn't),
and whether to prune tool outputs first (cheap, often enough on its own).

**Lean:** prune tool outputs first; then summarise with a handoff prompt, keep a recent tail;
compaction is an event in the log, and the originals stay.

---

## 9. How strict is prompt-cache discipline?

- **Hermes:** the system prompt is byte-stable for the whole conversation. Memory is a
  *frozen snapshot* taken at session start; writes go to disk and appear next session.
  `prompt_caching.py` places 4 breakpoints (static prefix, end of system, last 2 messages).
- **dsh:** system prompt changes are logged as `system/message` events and can be sent as an
  in-history update instead of rewriting the prefix.
- **Codex:** records per-step "world state" changes into history instead of editing the prompt.

**Lean:** treat the prefix as append-only. Anything that changes mid-session is appended, never
edited. Measure cache hit rate per request (ties into the observability direction).

---

## 10. How are dangerous actions controlled?

Two separate knobs, and every mature runtime keeps them separate:

**Enforcement (what is physically possible):**
- OS sandbox: Codex (`linux-sandbox`: bwrap + Landlock + seccomp; Seatbelt; Windows), dsh
  (`sandbox-local`: same three). Modes `read-only` / `workspace-write` / `full-access`.
- Swap the execution environment: Hermes (7 backends: local, Docker, SSH, Modal...).

**Decision (who says yes):**
- Rules: Codex `execpolicy` (Starlark `prefix_rule` with allow / prompt / forbidden and
  examples that are checked on load); Claude Code `Tool(pattern)` allow/deny *(docs)*.
- Pattern detection: Hermes (`approval_detection.py`: hardline + dangerous regexes).
- An LLM judge: Codex "guardian", Hermes `approval_smart.py`, Claude Code auto mode *(docs)*.
- Codex's orchestrator order: **approve → pick sandbox → run → if the sandbox denied it,
  retry escalated** (no second prompt).

**How approval waits:**
- A blocking `await` while the process stays alive (dsh, Codex, Hermes).
- A **durable suspend**: LangGraph `interrupt()` raises, the state is checkpointed, and on
  resume the node **re-executes from its start** with the answer. Survives restarts, but
  nodes must be safe to re-run.

**Lean:** sandbox mode × approval policy as two knobs; rules first, LLM judge optional;
approval as a durable event in the log (resumable), not just an in-memory await.

---

## 11. How is the harness extended?

| Mechanism | Who | Power / risk |
|---|---|---|
| In-process plugins with full access | dsh (everything, even the loop, is a Cordis plugin), Hermes plugins | maximum power; a bad plugin breaks the core |
| Typed hooks around model and tool calls | LangChain middleware (`before_model`, `wrap_model_call`, `wrap_tool_call`...) | clean, testable; in-process |
| Out-of-process hooks (shell commands, exit codes / JSON) | Claude Code *(docs)*, Codex hooks | any language, isolated; slower |
| Plain files | Claude Code skills / agents / CLAUDE.md, Codex AGENTS.md + skills, Hermes skills | anyone can extend; progressive disclosure keeps context small |
| MCP servers | all | standard; every tool costs tokens on every call |

Hermes adds a rule worth adopting as is, the **footprint ladder**: extend code → CLI
command + skill → gated tool → plugin → MCP → new core tool (last resort).

**Lean:** typed in-process hooks for the core, shell hooks and file-based skills for users.

---

## 12. Subagents: fresh or forked?

| Option | Who |
|---|---|
| **Fresh context**, parent only sees the call and a summary | Hermes `delegate_tool.py` (depth limit 1 by default: "each extra level multiplies API cost"), Claude Code Agent tool *(docs)* |
| **Fork**: child inherits the parent's finished turns (cheap via cache) | dsh `subagent-fork-in-process`, Hermes's background reviewer |
| Another product as the child | dsh `subagent-codex`, `subagent-claude-code`, `subagent-acp` |

**Lean:** both, as two modes of one tool; default fresh, depth 1.

---

## 13. Memory across sessions

- Files injected at session start: Hermes `MEMORY.md` + `USER.md` (frozen), Claude Code
  CLAUDE.md + auto-memory *(docs)*.
- **Learned automatically:** Hermes forks the agent after a turn to decide what to save
  (`background_review.py`).
- Recall: Hermes `session_search` (SQLite FTS5 + LLM summary); LangGraph `Store`.

**Lean:** v3 material. The frozen-snapshot rule matters from day one because it protects caching.

---

## 14. Library, or engine behind a protocol?

| Option | Who |
|---|---|
| In-process library | LangChain/LangGraph, Hermes core (`AIAgent`) |
| Engine behind a protocol; every UI is a client | Codex (`app-server`, JSON-RPC: `thread/start`, `turn/start`), Claude Code (stream-JSON over stdio; the SDK spawns the CLI *(SDK code)*), dsh (web server; the Python SDK launches `dsh --profile sdk`) |

A protocol costs a little upfront and makes every future surface (TUI, web, Telegram, IDE via
ACP) a thin client.

**✅ LOCKED (2026-09-27): library + terminal first, server in v1.**
- v0: the Python library (the core) + an **interactive terminal CLI** usable over SSH + a
  headless one-shot mode (`run "task" --json`) for scripts and CI.
- v1: `serve`, JSON-RPC over stdio/socket; web, desktop, phone, editor (ACP) and Telegram
  become thin clients.
- Later: TUI polish (Textual); a single-file binary (e.g. PyApp) only if people ask.
- Why: DeepSeek Harness shipped browser-first and its top Reddit complaint is "no CLI/TUI to
  manage it via ssh" (112 upvotes). Every harness engineers keep (Pi, OpenCode, Codex, aider)
  is terminal-first. Startup must be fast (lazy imports).

---

## 15. Language and model coupling

- **Language:** TypeScript (dsh, Claude Code, most new harnesses: npm distribution, easy
  plugins), Rust (Codex: speed, a single binary, no flicker), Python (Hermes, LangChain:
  ML ecosystem). HN spends whole threads on this; it matters less than decisions 1–10.
- **Models:** one vendor (Codex, Claude Code) or any (dsh ~40 providers, Hermes, LangChain).
  Feedback says users want one surface across models.

**✅ LOCKED (2026-09-27): Python, for both the runtime and the provider library.**
- Why: his strongest language; the loop is I/O-bound; eval/RL work plugs in; Hermes, aider and
  OpenHands show Python doesn't block adoption.
- Codex's Rust reasons (no Node install, OS sandbox bindings, ms startup for CI fan-out, a wire
  protocol) are product-at-scale reasons; we cover install with uv and the protocol with #14.
- Hedge: everything crossing a boundary is language-neutral (JSONL log, JSON-RPC, JSON-able
  types), so any component can be ported later without a redesign.
- Distribution: PyPI + `uv tool install`, plus a short Hermes-style `install.sh` (pinned uv with
  a SHA-256 check, then `uv tool install`). Goal: open source, public adoption.

---

## Order to decide in

1 → 2 → 3 → 5 are needed for v0. 4, 6, 7, 8, 9 for v1. 10–14 can wait but 1 must
anticipate them: an event log makes resume, approval-as-event, fork and memory review cheap later.
