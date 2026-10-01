# Context management and compaction: research (2026-09-30)

What happens when a session outgrows the model's window, and how Diwan should handle it.
The problem Zine names: compaction without memory is like starting a new session.

## 1. How the agents in `refs/` do it

| Agent | Trigger | What is kept verbatim | Summary | Other |
|---|---|---|---|---|
| **Codex** (`core/src/compact.rs`) | configurable token limit | the user's own messages, newest first, up to 20k tokens; the initial context re-injected | "handoff summary for another LLM": progress, decisions, constraints, next steps, critical data | the next model is told "another model started this, here is its summary" |
| **OpenCode** (`session/compaction.ts`, `overflow.ts`) | context − 20k reserved | the last 2k–15k tokens | LLM summary | **prunes first**: walks back, keeps the newest 40k tokens of tool output, replaces older ones with `[Old tool result content cleared]` (only if at least 20k would be saved) |
| **pi** (`agent/src/harness/compaction/`) | context − 16k reserved | the last 20k tokens, cut at turn boundaries | **structured template**: Goal · Constraints & Preferences · Done / In progress / Blocked · Key decisions · Next steps · Critical context; "preserve exact file paths, function names, error messages" | the summary is **updated** at the next compaction, never re-summarized; lists of files read and modified are appended; the full log is kept |
| **Gemini CLI** (`context/chatCompressionService.ts`, `toolOutputMaskingService.ts`) | **50%** of the window | the newest 30% | `<state_snapshot>`, then a **verify pass**: "did you omit any file paths, tool results, user constraints? if so, produce a better one" | masks old tool outputs (above 50k tokens of protection, if at least 30k can be saved) by **writing them to files** the model can reread |
| **OpenHands** (`context/condenser/`) | more than 240 events | the first 2 events, plus the newest half | rolling LLM summary | pipeline of condensers |

## 2. Research

- **The Complexity Trap** (JetBrains, arXiv 2508.21433): on SWE-bench Verified with five
  models, **observation masking** (older tool outputs replaced by a placeholder, reasoning and
  actions kept) **halves cost** and matches or slightly beats LLM summarization. The simplest
  strategy is the strongest baseline.
- **ACON** (Microsoft, arXiv 2510.00615): the compression **guideline is optimized from
  failures**. On paired runs where the full context succeeds and the compressed one fails, an
  LLM finds what the summary dropped and rewrites the guideline. It uses 26–54% fewer peak
  tokens and keeps 95% of accuracy, even when distilled to a small compressor. **This is a
  self-improving harness mechanism**: the harness learns what to keep.
- **TRACE** (arXiv 2608.06503): repeated compression makes agents unstable. Recent
  interactions lose influence, blocked actions rise, exploration repeats and results vary
  between runs. Fix: evaluate each compression event at its boundary, with paired
  simulations, and optimize the prompt.
- **Context rot** (Chroma, 2025): quality drops as context grows, even on simple tasks, and
  before the window is full. Practitioners on r/ClaudeAI report the same around 35–50%.
- **Anthropic context editing** (API): server-side clearing of old tool results
  (`clear_tool_uses`), plus a memory tool the model writes to. Anthropic reports +29% on its
  agent evals from editing alone, +39% with memory.
- **Manus lessons**: KV-cache hit rate is the key metric; compression must be **restorable**
  (drop a page's content but keep its URL or path); keep a todo file the model rewrites to hold
  its goal in recent attention; keep errors in context.

## 3. What users say (r/ClaudeAI, X, 2026-09)

- Most heavy users **never auto-compact**. They stop at 35–50%, have the agent write a handoff
  note or update its docs, and start a new session. One user automated exactly that at 35%.
- **What auto-compaction loses most: rejected options.** "It keeps what you decided and drops
  what you tried first and why it failed, so the next session rebuilds the version you already
  abandoned." The fix: write decisions down **when they happen** (append only), not
  reconstructed in a summary at the end, which "comes out plausible instead of true".
- Compact **at a natural boundary** (between plan phases), with an instruction about what comes
  next, not at an arbitrary token count mid-task.
- Cost: each turn rereads the context, so a big context is expensive even with caching (about
  10% of full price per turn). Compacting from 300k to 100k saves that on every later turn.
  **Set the threshold per model**: one Hermes user compacts Opus at 25% and GPT at 85%.
- Rewriting earlier context (pruning, compressing) can invalidate signed thinking blocks and the
  cache. Change the prefix rarely, in batches.

## 4. Proposal for Diwan (cheapest layer first)

1. **Measure.** Exact usage from the last response against the catalog's window size, shown in
   the status bar. A soft threshold and a hard one, per model (weaker models: earlier).
2. **Don't bloat.** Search tools with short output (grep/glob). Large tool outputs go to a
   file, with a pointer in context (restorable).
3. **Mask old tool outputs** (Complexity Trap, OpenCode, Gemini): past the newest N tokens of
   tool output, replace the result with a placeholder that keeps the call ("read src/x.py:
   output cleared, read again if needed"). No LLM call. Done in batches, since it breaks the
   cache, and never inside the current turn.
4. **A decisions file written as work happens.** A session notes file the model appends to
   when it decides or rejects something, with the reason. It survives compaction verbatim and
   covers the "rejected options" failure.
5. **Compaction as a view, never a deletion.** The log keeps everything (it already does). The
   compacted request = system + the user's messages verbatim (Codex) + a structured summary
   (pi's template, plus a **Rejected approaches** section) + files read and modified + the
   newest ~20k tokens verbatim. Update the previous summary instead of summarizing it again.
6. **`recall`: search what was compacted away.** The model can search the full session log
   (tool outputs, earlier messages) when the summary isn't enough. This makes compaction
   lossless: Diwan's answer to "compaction = a new session".
7. **Compact at a natural stopping point.** At the soft threshold, a reminder asks the model to
   finish its step, update the notes and say what comes next. At the hard threshold, compaction
   is forced.
8. **Later (self-improvement): ACON-style guideline learning.** When a task fails after a
   compaction but would have succeeded without it, find what the summary dropped and update the
   guideline. It is measurable, local to the harness, and it improves without us editing it.

## 5. Decision (2026-09-30): age tiers

Measured on 6 real sessions (~15M tokens): tool outputs 61%, tool-call arguments 17%, agent
text 13%, file contents in write/edit 6%, user text 2%. Masking outputs alone shrinks a session
only about 2.5x, and the history keeps growing. Codex's "user messages + summary" loses what
the agent learned and makes it redo finished work. So the treatment depends on age:

| Tier | What is sent | Size |
|---|---|---|
| Recent (~30k tokens) | everything, verbatim | 1x |
| Middle | user and agent messages, short call stubs (`edit auth.py`, `bash: pytest -q`); outputs and large arguments cleared | ~5-6x smaller |
| Old | one structured summary, updated rather than rewritten: goal, steps done, decisions and rejected options, files touched | ~50-100x smaller |

The full log stays on disk; `recall` searches it. Content moves from recent to old as the
session grows. Build order: search tools + context gauge → masking tiers → progress/decisions
file → summary + recall.
