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
