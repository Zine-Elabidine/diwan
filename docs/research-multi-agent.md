# Research: many agents on one task (councils, swarms, fusion)

Collected 2026-09-28 while looking for Diwan's edge. Diwan was historically a *council*,
so this may be the feature the name was waiting for.

## What exists today

| System | Shape | How it works | Notes |
|---|---|---|---|
| **OpenRouter Fusion** (Jun 2026) | panel → analyst → synthesis | 1–8 models answer in parallel (each with web search/fetch, ≤4 tool calls). An analyst (temp 0) *compares, does not merge*: JSON of consensus, contradictions, partial coverage, unique insights, blind spots. The outer model writes the final answer. Exposed as a tool the model decides to call; no nesting. | ~4–5× the cost of one call, 2–3× slower. Budget panel (Gemini Flash + Kimi + DeepSeek) ≈ within 1% of Fable 5 on 100 research tasks at ~half the price. **Community: great for research, poor for coding** (placed 12th on a real coding benchmark; synthesized code crashed). |
| **Sakana Fugu** | a model that orchestrates models | One endpoint; a model trained to decide *whether* to delegate, split the task, dispatch to expert models, verify, synthesize. Tiers trade latency for more agents. | Delegation is learned, not scripted. |
| **Claude Code agent teams** (experimental) | lead + teammates | Separate sessions, own context each. Shared task list with dependencies and file-locked claiming; per-agent JSON mailbox; hooks on idle / task created / task completed can refuse and push back. | Claude only. Advice: 3–5 teammates, each owning separate files. Weak points: no resume, task status lags, lead quits early or does the work itself. |
| **Cursor best-of-N + judging** | race | Same prompt to several models in parallel, then a judge recommends a winner with reasons. | Agents *race*, they don't collaborate. Selection, not synthesis. |
| **Grok Heavy / Gemini Deep Think** | study group | Parallel agents think independently, cross-check, a captain synthesizes. | Inside the model product, not a harness. |
| **Kimi agent swarm** (K2.5→K3) | trained orchestrator | Up to 100–300 sub-agents. Decomposition is **trained into the weights with RL (PARL)**; reward first encourages parallelism, then shifts to task success. 3–4.5× faster. | The orchestrator is trained; sub-agents are frozen. |
| **Fusion Harness V2** (Pi extension) | poll / debate / collaborate | Poll = fan out with cost stats. Debate = state positions, see the others, revise over rounds. Collaborate = all plan, one architect merges into a task list with owners. **Models get aliases**, never real names, to avoid rivalry and sabotage. | Mid-tier panels at ~1/10 of frontier cost. |
| **parley** (CLI/MCP) | glue between harnesses | `fuse` (panel + judge), `ask` (one agent, seeded with another's transcript), `converse` (two agents talk for N turns). | "Have Kimi review Claude's work" keeps finding real gaps. |
| **HeavySkill** (paper) | heavy thinking as a skill | Parallel reasoning + aggregation packaged as a skill the agent invokes only when a problem deserves it. | Selective use keeps cost sane. |

## What the evidence says

1. **Most of debate's gain is ensembling.** With equal compute, plain majority vote often
   matches or beats vanilla debate ("Debate or Vote", 2508.17536). Debate earns its keep on
   **hard problems where the first answers differ**.
2. **Diversity is the lever.** Different starting answers correlate with the final accuracy;
   diversity-aware starts and **confidence-weighted** exchange beat voting (2601.19921).
   Different *providers* give more diversity than one model sampled N times.
3. **Synthesis suits prose, selection suits code.** Merging several answers works for research
   and plans. For code, run each candidate in isolation, **verify** (tests, types, run it), then
   pick. Don't blend diffs.
4. **Escalate, don't default.** Convene the panel when being wrong costs more than 4–5× the
   tokens: security, migrations, destructive commands, conflicting evidence, a stuck agent.
   Log cost, latency and **whether the panel changed the decision**.
5. **Keep the dissent.** The disagreement summary is often worth more than the polished answer.
6. **Coordination is the hard part of swarms.** File ownership, task claiming, a lead that
   waits, and resuming a team are where Claude Code's version is still weak.
7. **Anonymize the voices.** Aliases stop models from deferring to, or attacking, a known rival.

## Where Diwan could have an edge

Things nobody combines today:
- **Provider-neutral councils.** Claude Code teams are Claude-only; Fusion is a black box behind
  one API. Diwan + Tarjuman can seat Claude, GPT, Gemini, Kimi, DeepSeek and local models at the
  same table, with real tools and the user's repo.
- **The council lives in the tree log.** Every voice is a branch of the event tree (decision #1).
  You can open any member's full reasoning, replay a round, swap one member and re-run from that
  point, and resume a council after a crash (Claude Code's teams can't).
- **Councils produce training data.** Every session records who proposed what, what the
  verifier said, and what won. That is exactly the data behind PARL-style orchestrator training.
  It links to his RL/GRPO work: later, train a small model to decide *when to convene and whom
  to seat*.
- **Honest accounting.** Each council costs dollars and time and shows whether it changed the
  outcome, so you learn which panels are worth it.

## Candidate modes (a sketch, not decided)

| Mode | For | Mechanics | Ends when |
|---|---|---|---|
| **Majlis** (panel) | questions, plans, designs, reviews | N anonymized members answer in parallel (read-only tools) → analyst JSON (consensus / conflicts / unique / blind spots) → chair writes the answer and **keeps the dissent** | all answered or budget |
| **Race** (best-of-N) | code changes | N members work in separate git worktrees → verifier runs tests/checks → judge ranks → you pick or accept | winner chosen |
| **Debate** | hard, contested calls | positions + confidence → rounds seeing the others' (anonymized) → revise or hold | convergence, round cap, or budget |
| **Review** | any finished work | a different provider critiques the work; the author answers each point | all points answered |
| **Team** | large decomposable work | lead + workers, task list with dependencies, file ownership, mailbox, all as events | task list done |
| **Escalation** | normal sessions | the agent can call `convene(mode, question)` as a tool when stakes or doubt are high; policy + budget gate it | as the mode |

## What this changes in the open decisions

- **#3 (what ends a turn):** a turn can now fork into parallel member runs. It needs rules for
  convergence, round caps and **dollar budgets**, which become central rather than optional.
- **#5 (tool execution):** isolation per member (worktrees, read-only panels) and write ownership.
- **#12 (subagents):** councils are the general case; a plain subagent is a council of one.
- **#1 (tree log)** already fits: members are sibling branches, and the verdict is a join event.

## Sources
- OpenRouter Fusion docs: https://openrouter.ai/docs/guides/routing/routers/fusion-router
- Fusion as escalation: https://www.developersdigest.tech/blog/openrouter-fusion-model-panels-escalation
- Fusion on a coding benchmark: https://x.com/Zortosdev/status/2066250418179731511
- Sakana Fugu: https://sakana.ai/fugu/
- Claude Code agent teams: https://code.claude.com/docs/en/agent-teams
- Kimi agent swarm: https://www.kimi.ai/blog/agent-swarm
- Fusion Harness V2: https://openclawdatabase.com/news/videos/2026-08-24-fusion-harness-multi-model-debate/
- parley: https://github.com/KerryRitter/parley
- Debate or Vote: https://arxiv.org/abs/2508.17536
- Demystifying Multi-Agent Debate: https://arxiv.org/html/2601.19921v3
- HeavySkill: https://arxiv.org/abs/2605.02396
- Cursor best-of-N / judging: https://www.elegantsoftwaresolutions.com/blog/cursor-multi-agent-not-what-you-think

---

# What people want from their agents (X, Reddit, HN, 2026-09-28)

Searched r/LocalLLaMA, r/ClaudeAI, X and HN for people who built their own harness and for
what they wish existing ones did. Engagement is noted where it signals demand.

## Recurring wants, strongest first

1. **Parallel agents you can actually see and steer.** "I run many agents in parallel and lose
   the picture of who spawned whom, which is working; agents finish or die quietly and I find out
   half an hour later" (HN, live-map viewer). Andreas Kling (796 likes): design doc → coordinator
   agent → workers on 4 machines over SSH, with **regular welfare checks** for stuck or
   crash-looping workers; projects run for days. Murmell: parallel agents' branches collided.
   → *Want: a live view of the tree of agents, health checks, per-agent isolation.*
2. **Code decides, the model writes, and everything leaves a receipt.** The "Jev" harness posts
   (650–980 likes, 2026-09-23..27): the model stops making the decisions; small deterministic code
   picks the model tier, the next worker and keep/drop; state is **evidence, never a summary**.
   Also a small fast model as a **fuzzy linter after every edit** (621 likes).
   → *Want: a deterministic control plane around the model, and verification gates.*
3. **The harness matters more than the model, so measure it.** TrueForge: same model, same 11/14
   solved, **63% fewer tokens, ~27% cheaper** than Claude Managed Agents. "Same task, comparable
   models, one harness passes clean, the other doesn't." llama-leash: "built to be measured rather
   than believed", scoring the harness against the bare model on hidden tests.
   → *Want: built-in evals and cost accounting per run.*
4. **Verify gates, especially for small or local models.** A roles + verify gate harness lets 4B
   and 12B models match a 35B one on agentic coding. TDD gates that re-derive their own evidence.
   "Keeps working until tests pass, not until the chat says done."
   → *Want: verification as a first-class step, not a prompt instruction.*
5. **A work queue, not a chat.** "Tasks → agent works → tests → report back → next task; a junior
   developer with a work queue." Cron agents that wake up, read a work ledger, and **post blocking
   questions to a dashboard, then keep going with their best guess**; the human answers over
   morning coffee and the next wake-up checks the answers.
   → *Want: an async inbox of questions, background and scheduled runs.*
6. **Continuity across sessions, models and harnesses.** "Software is continuous; Claude forgets
   to update the todo list and CLAUDE.md" (186 upvotes, 113 comments). `lsa handoff 0 codex`
   moves a conversation to another harness; Tutti shares context between Claude Code and Codex.
   → *Want: memory that travels (Telepathy) and a portable session format.*
7. **Local models without pain.** Forcefield, KoboldCpp Agent (159 upvotes), and a Qwen-built
   harness (75 upvotes, 137 comments) all exist because Claude Code-style tools are hard to point
   at local models: tool-call syntax failures, heavy prompts. One uses XML tool calls plus a small
   classifier that catches malformed calls and nudges a retry.
   → *Want: robust tool calling on weak models (Tarjuman's job).*
8. **Safety that's one line.** "What sandbox are you all using?" (127 comments): people want
   `sandbox agent --allowed-folder=X`. **Just-in-time code review before a tool call** is approved.
9. **Context hygiene.** "Chats go dumb after 20–25 turns": keep tool outputs in *working memory*
   that isn't saved to permanent history; every turn archived so you can `/restore`.
10. **Other ideas worth stealing:** an agent that **forges its own tools** mid-task behind an
    approval gate (6% → 61% on capability-gap tasks); personas that each keep a daily dashboard of
    what they're good at; three-way chat (user + main agent + subagent in one thread); session
    transcripts analysed for cost per shipped PR (Tuneloop).

## The skeptic's view (also real)

"Everyone wants to reinvent the wheel"; "Sounds like Hermes with extra steps"; "Can we stop with
all the vibe-coded harnesses?" A new harness gets attention only with **one clear, measurable
difference**, shown with numbers, not a feature list.

## What this suggests for Diwan

The council idea lines up with wants 1–4: provider-neutral councils, visible and steerable as a
tree, with verification deciding the winner and every run measured (cost, tokens, did the panel
change the outcome). Wants 5–6 (work queue, continuity) fit the event log and Telepathy.
Lead with **one** measurable claim, e.g. "a council of cheap models + tests beats one frontier
model on X at Y% of the cost", and publish the numbers.

## Sources (community)
- Andreas Kling's workflow: https://x.com/awesomekling/status/2102089363631059199
- Jev harness posts: https://x.com/polydao/status/2104090661041369397 , https://x.com/Av1dlive/status/2102802621664985241
- Fuzzy linter: https://x.com/MichaelThiessen/status/2103831653575544914
- Live map of parallel agents: https://github.com/Latand/live-log-viewer-next
- TrueForge harness comparison: https://www.reddit.com/r/LocalLLaMA/ (post "We built an open-source, model-neutral agent harness…", 2026-09-03)
- llama-leash: https://github.com/vorlac/llama-leash
- Work-queue / cron agents thread: https://www.reddit.com/r/LocalLLaMA/comments/1wilmnz/
- Best local harness thread: https://www.reddit.com/r/LocalLLaMA/comments/1vukppf/
- Sandbox thread: https://www.reddit.com/r/LocalLLaMA/comments/1vrps78/
- "Looking for the right harness" (continuity): r/ClaudeAI, 2026-09-17
- Artificium thread (skeptic replies): https://www.reddit.com/r/LocalLLaMA/comments/1wfmzez/
- Forcefield: https://github.com/fabledruns/forcefield ; Tuneloop: https://github.com/tuneloop/tuneloop

---

# Decision models ("System One"): Jev, CLM-8B and friends (2026-09-28)

## What they are
- **Jev** (TypeSafe AI, launched 2026-09-15, $40M seed, founder Diogo Almeida, ex-OpenAI). A
  **non-autoregressive decision model**: it never writes text. Given a context and a declared
  set of options, it returns a typed decision with probabilities in one forward pass.
  Interface: **Noul** (probability a statement is true), **Choice** (one of N options),
  **Score** (level on an ordered rubric). Trained with "RL for Calibrated Decisions".
  Claims 20–200× faster and 40–400× cheaper than small frontier LLMs; output can't break the
  schema. Limits: 32K context, text/JSON only, waitlist, **no paper and no independent
  benchmarks**; vendor numbers only. The founder concedes it can be "confidently wrong".
- **CLM-8B** (Stanford + NVIDIA, open weights, Apache 2.0, 2026-09-23). Frozen Qwen3-8B with a
  *state head* and an *action head* trained contrastively (InfoNCE) on 60M QA pairs and 1M
  agent trajectories. Same Noul/Choice/Score API as Jev. **Caches the action embeddings**, so
  re-scoring the same candidates is nearly free: 9× lower latency than Jev, 13× with ~1k
  candidates. Measured on *decision accuracy* (not task success):
  - picking the correct solution among Opus 5 / Fable 5 candidates: DeepSWE 81.6% (Jev 71.1%),
    Terminal-Bench 2.1 87.6%;
  - tool calling 95.2% (Jev 99.2%); WikiRacing 26/30 (Jev 30/30).
  Its own pitch: "large models generate and reason; CLMs cheaply **select, verify and monitor**."
- Ecosystem in two weeks: open replicas (jevlike, Eikos, laya-mps at 0.74 GB on a Mac), a free
  Jev Router on the Dot platform, "Jev-ify any open model" (SimpleJev). jevlike's recipe: each
  option becomes a query that attends over the context, then a shared dot product and a softmax.

## The "Jev harness" (hype vs substance)
A 12-page blueprint, much reposted (up to 980 likes): *the LLM writes, the harness executes, the
decision model decides what each turn sees, where it routes and whether it runs.* The places the
decision model is used: **model-tier routing**, **scoring context chunks** (keep/summarize/hide),
**tiered tool disclosure**, **routing by trust/permission**, **gating every command**
(allow/ask/deny). Measured: ~$0.0002 per step. **No published end-to-end numbers**; one tracker
notes that "most of what circulated is an architecture sketch with no number attached".
"200× cheaper" is per decision, not per task. A coding task's cost is dominated by the big
model's generation, so the task-level saving is much smaller.

## Honest verdict on usefulness in agent workflows
It pays off only where a harness makes **many small decisions that today cost an LLM call** (the
rule of thumb quoted: decisions outnumber generations 10:1). In a plain coding loop there are few
such calls, so the gain is small. That matches his doubt. But councils and swarms are full of
them:

| Decision point in Diwan | Today | With a decision model |
|---|---|---|
| **Race: which candidate wins** | LLM judge (slow, costly) or tests only | CLM scores N candidates + tests; the best measured use so far |
| **Debate: converged yet? who is confident?** | ask an LLM every round | Noul/Score per round, calibrated probabilities |
| **Escalation: convene the council?** | the agent guesses | Noul "is this risky or uncertain?" on every step, nearly free |
| **What ends a turn (#3)**, continue/stop | model stops calling tools | an extra calibrated check: "is the task actually done?" |
| **Permission gating (#10)** | rules + prompts | Choice allow/ask/deny with a probability; ask when unsure |
| **Context scoring (#8)** | recency, whole outputs | Score each chunk: keep / summarize / drop |
| **Model routing** (Tarjuman) | fixed config | Choice of tier per step |
| **Monitoring a swarm** | a human watching | Noul "is this worker stuck/looping?" on every event |

## How to engineer it into Diwan
- One interface, `decide(kind=noul|choice|score, context, options) → probabilities`, with
  backends: **CLM-8B local**, Jev API, or a fallback that asks an ordinary LLM for a JSON answer
  (with logprobs when available). Tarjuman could host it as a second model family.
- **Every decision is an event in the log**, with its inputs, probabilities and the eventual
  outcome, so each one can later be scored against what happened. That is also a training set
  for fine-tuning his own decision head (contrastive heads on a frozen encoder, like CLM). This
  sits right in his RL/fine-tuning skill set.
- Calibration matters more than accuracy: use thresholds with an "ask the user / escalate to a
  big model" band in the middle.
- Treat vendor claims as unverified until measured on our own tasks.

## Sources
- Latent Space AINews on Jev: https://www.latent.space/p/ainews-jev-a-system-one-model-that
- Skeptical audit: https://flowtivity.ai/blog/jev-typesafe-ai-decision-model/
- Jev harness summary: https://madewithjev.com/jev-agentic-harness
- CLM-8B: https://venturebeat.com/technology/stanford-and-nvidias-open-clm-8b-caches-reusable-agent-actions-and-runs-up-to-9x-faster-than-jev-in-tests , https://huggingface.co/Contrastive-LM/CLM-v0.1-8B
- jevlike: https://github.com/vinnylarouge/jevlike
- Jev explainer (1.8k likes): https://x.com/matthewcanham/status/2102077098756280413
