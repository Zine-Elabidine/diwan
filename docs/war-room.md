# War Room — Phase 0: bets before code

Status: Phase 0 (no code). Research behind this: `research-multi-agent.md`.

## 1. The pain

**What he does today:** he runs 3–5 Claude Code sessions at once, each on a different task, and has had no issues. He has never put two agents on the same task.

**Where several agents on one task could help:**
- (a) Hard tasks or heavy brainstorming.
- (b) Research.
- (c) A real team with roles, such as a backend builder, a frontend builder and a tester working on one project at once.

**Challenge (2026-10-09):** the split into two room types was rejected (see 1b). The risk difference below still matters.
- **No proven pain yet.** The parallel sessions work fine, and the same-task use cases are hypothetical. Neither has a pain he has felt himself. Phase 1 logging checks whether hidden friction exists, such as checking on sessions or carrying information between them.
- **The use cases carry very different risk:**
  - **(a) and (b): low risk.** The agents only think or read and nobody edits shared code, so the CooperBench failure, where one agent ignores its partner's plan, can't happen. These match the council modes in `research-multi-agent.md` (Majlis, Debate).
  - **(c): high risk.** It is the exact setup CooperBench measured: agents splitting one codebase, where pairs got about half the success of a single agent. It is the most appealing case and the one most likely to fail.

## 1b. His direction (2026-10-09)

- **One room, no hardcoded workflows.** He rejected splitting it into a "thinking room" and a "building team". The user decides what the room is for.
- **An awareness switch on the room:**
  - ON: agents spawned in the room know about each other's work.
  - OFF: the room is just several sessions running side by side.
- **Tiling is aesthetic.** Hyprland-like, with a "focus" action that makes one session bigger. He asked to be challenged on all of this.

**Challenges:**
1. **A generic room is the right call, but the switch hides the real design question.** What exactly does an aware agent receive?
   - (a) A board it reads when it chooses to (pull).
   - (b) Updates pushed into its context (the stale-message, token-burning problem seen in Claude Code agent teams).
   - (c) Direct messages to other agents.

   This is the core design decision of the War Room. "ON/OFF" is only the UI for it.
2. **A generic room doesn't remove the CooperBench risk. It hands that risk to the user.** If two aware agents edit the same repo, the "ignored the partner's plan" failure still happens. A room that supports any workflow has to make failures visible, using signals the runtime can check:
   - file conflicts;
   - "done" claims that the tests don't back up;
   - unanswered questions.
3. **Awareness OFF is already a useful product.** It's what he does every day with 3–5 sessions, and it costs the least. It should ship first, and awareness ON should be an experiment on top of it.
4. **Tiling vs his window manager.** If he already runs a tiling WM, it tiles terminals for free, so a tiler inside Diwan would duplicate it. A WM can't show the shared state of the room (who is blocked, who is waiting on an approval, who touched which file), and Diwan can. So the value is a room status layer. The tiling itself is aesthetic, and it's still OK to build it for that reason.

## 1c. How people make agents communicate: research (2026-10-09)

**What worked:**
- **Single writer, many advisors.** Cognition (April 2026 follow-up to "Don't Build Multi-Agents") says parallel-writer swarms still fail. What works is one agent writing while the others contribute ideas and reviews.
  - Their clean-context reviewer catches about 2 bugs per PR, 58% of them severe.
  - A "smart friend" gets a fork of the full context, not a summary.
- **Independent parallel work.** Anthropic's research system: a lead agent plus isolated subagents scored 90% better than one agent, at about 15x the tokens. It works because the subtasks don't depend on each other. Anthropic itself says coding has fewer truly parallel tasks.
- **Contracts before code.** VibeHQ makes agents sign an API spec before coding, which targets the "built against different assumptions" failure (CooperBench's 63%). Its evidence is self-reported single runs.
- **File reservations.** MCP Agent Mail (2.2k stars) lets agents reserve files, but the reservations are advisory and work only if agents respect them. An optional pre-commit hook enforces them.
- **Hand off exact git SHAs, not descriptions** (r/ClaudeCode, 2026-10-03, "worktrees talking to each other").
- **Deliver messages at checkpoints or when the agent is idle, never mid-turn.** Seen in VibeHQ's idle-aware queue, in postbag (letters become user turns), and in the r/ClaudeCode inbox-at-checkpoints post.
- **A blackboard with evidence** (tianpan.co, "The Blackboard Is Back"). Each entry records who wrote it and how it was verified. Facts, diagnoses and plans are kept separate. A verifier, not the implementer, decides when the work is done.

**What failed:**
- **Parallel writers making conflicting implicit choices**, such as style and edge cases (Cognition's Flappy Bird example).
- **Too many updates.** Anthropic's early versions had agents "distracting each other with excessive updates".
- **Unstructured negotiation between agents.** Cognition calls it "mostly a distraction".
- **Polling overload.** VibeHQ had to cap each agent at 5 hub calls per minute.
- **Agents rejecting or fighting each other's tasks** (r/ClaudeCode, 2026-10-03).
- **Stub files and false "done" claims.** VibeHQ added size and stub checks.
- **Forks blowing up context:** 360k → 2 × 700k (r/ClaudeCode).

**What this means for Diwan.** Diwan owns the runtime, so it can enforce what others can only advise:
- File claims enforced inside its own edit tool.
- Board entries written by the runtime (diffs, SHAs, test results), not claimed by agents.
- Delivery through the existing inbox at turn boundaries.

**Clarification from him (2026-10-09):** awareness ON is not an advisor. An advisor is a tool call inside one session, a strong model asked for advice (he uses it in Claude Code and it works well). The Anthropic orchestrator-worker setup is also in-session, and Diwan already has it (subagents, fresh and fork). **Awareness is between independent sessions** in a room, each with its own task from the human.

**Reframe (me):** in a room, "single writer" can mean one writer *per file area*, not one per project. Backend, frontend and tester each own their area, and the risk sits at the boundary between areas. That boundary needs a contract (the API spec) on the board. This keeps his team idea possible without parallel writers on the same files. Still to be tested in Phase 2; the decision is his.

## 1d. Awareness logic: draft v0 (2026-10-09)

His scope: **no roles and no team definitions.** Define only the generic awareness logic and make it efficient. Agents in a room know about each other, can communicate, and can cooperate.

**Draft primitives:**
1. **Status card per session, written by the runtime and free in agent tokens.** It shows:
   - **title**: generated once at session start by the session's own model with a side request, like Claude Code. It does not change.
   - **now**: the session's latest `progress` note (the existing `note` tool, so no extra tokens). Falls back to the last user prompt. It changes mid-session, which the title can't. It is agent-written, so the card marks it as a claim.
   - **decisions**: the session's `decision` notes, shown on the board so other sessions see announced plans (CooperBench's 63% failure).
   - (He pointed out on 2026-10-09 that the title is not the goal.)
   - state: working / idle / waiting for approval / blocked;
   - files touched;
   - last diff stat and commit;
   - last test result.

   Diwan already knows all of these, so no agent has to report them.
2. **Pull: a `room` tool.** It returns every card as a short summary of about 100 tokens per session. `room show <id>` gives details (the real diff). Agents read it when they want to.
3. **Push: only what's relevant, only at turn boundaries.** The runtime injects a notice through the existing inbox in just two cases:
   - another session touched a file this one touched;
   - a message is addressed to this session.

   Notices are deduplicated and always show the latest state. Nothing else is broadcast.
4. **Talk: `send` / `ask` to one session or to everyone.** Messages are kept as threads on the board. An `ask` stays open until it's answered and shows up as a blocker. A message to a session that has ended or gone stale is refused, which avoids the token burn seen in Claude Code agent teams.
5. **Cooperate safely: file claims enforced by Diwan's own edit tool.** A session claims a file automatically on its first edit. Another session editing it is stopped with "owned by X: ask or request a handoff". The claim is released on commit, or when the session ends.
   - **Decision (2026-10-09): block by default.** He asked for my recommendation. Advisory reservations get ignored, which is MCP Agent Mail's weakness, and an overwrite is costly to undo while a block is cheap.
   - **Escapes so a block never becomes a deadlock:**
     - the owner is idle or waiting for approval → auto-handoff;
     - the owner has ended → the claim is gone;
     - otherwise the agent `ask`s the owner, or the human approves a handoff.
   - It's a room setting (`claims: block|warn`), so Phase 2 can compare the two.
   - **Claim rules (draft):**
     - **Claim:** on the first successful `edit`/`write`. Reading never claims.
     - **Granularity:** one whole file.
     - **Release:** ~~on commit~~ (he caught on 2026-10-09 that agents don't commit after each edit, so the claim would be held all session). **A claim lives only while the owner is in a turn.** It is released when that turn ends, on handoff, or when the session ends. Danger only exists while someone is mid-change. Between turns the owner's uncommitted edits sit on disk, and the stale-read check protects the owner when it resumes.
     - **Decided (2026-10-09): shared working tree for v0.** That's his real habit: 3–5 sessions, one repo directory, different tasks. Worktrees can come later as a room option.
       - Bonus from the card's "files touched": a per-session commit (stage only that session's files) to stop one session's half-done work leaking into another's commit.
     - Background on the choice, shared working tree vs a worktree per session:
       - In a shared tree, uncommitted changes from several sessions mix, and git can't say whose is whose.
       - With worktrees, nothing needs blocking, conflicts show up at merge time, and awareness can warn early ("you both changed api.py"). The cost is disk and setup.
     - **Shared files** (lockfiles, `pyproject.toml`, `__init__.py`, migrations): `warn` instead of `block`, set in a room list.
     - **Gap: shell writes** (`sed -i`, formatters, `git checkout`) bypass the edit tool.
       - Detect them by comparing `git status` before and after each shell call, and claim what changed.
       - A formatter that touches many files would claim them all, so tool-only writes may need an exemption.
   - **Stale-read check (separate low-regret task, also useful solo):** `write`/`edit` refuse if the file changed since this session last read it. Today `files.py` has no such check.
   - **Wait and wake (his idea, 2026-10-09).** A blocked session can choose to *wait* instead of working around the block:
     - Its turn ends and its card shows `waiting on api.py (session 1)`. It costs zero tokens while waiting.
     - When the claim is released (commit, handoff, owner ended), Diwan starts a new turn by itself with a note like "api.py is free now, continue".
     - **Code today:** `Agent.send()` (agent.py) only queues messages. A queued message starts a turn only through `_turn_done` (tui.py), when a turn finishes. Nothing starts a turn for a session that is already idle, so waking an idle session is the new piece.
     - **Safety:**
       - Wait cycles (A waits on B, B waits on A) are detected in the wait graph and shown to the human.
       - A wait that lasts too long goes to the board as "stuck".
       - Ctrl-C or Esc on a waiting session cancels the wait, like any interrupt.
6. **Verify, don't trust.** "Done" on the board means runtime evidence (tests run, a commit SHA), not an agent's claim.

**Efficiency budget:**
- No continuous chatter.
- Cost in context: the cards when pulled, plus notices about overlaps.
- Target: awareness ON costs under 10% extra tokens per session. Measure it in Phase 2.

## 1e. Corrections and the gentle block (2026-10-09)

**Push timing, his correction: between model requests, not between turns.** A turn makes many requests. Diwan's inbox already works this way: `Agent.run` calls `_deliver()` before every request except the first (agent.py ~165). A pushed message lands after the tool results and before the next request. Room messages reuse this, and the only new piece is waking an idle session.

**`send` / `ask` (draft):**
- `send(to, text)`: fire and forget. The message lands in the target's inbox, tagged `[from s1 · <title>]`, and is seen at the target's next request. The sender continues.
- `ask(to, question)`: same delivery, plus an open thread on the board. The sender keeps working, or waits (asleep, woken by the answer). The target answers with `reply(thread, text)`, which goes to the asker's inbox. An unanswered ask shows as open on the board.

**The gentle block (draft):**
- **Block only on "active right now":** the owner edited the file within its last N model requests (N≈3) in its current turn. Otherwise there's no block, just an overlap notice.
- **Flow:**
  1. s2's edit is refused, with s1's title, its "now" and the options.
  2. Diwan sends s1 a request automatically: "s2 wants api.py (s2 now: <its now>)". It arrives at s1's next request, so within seconds.
  3. s1 answers `release` (done with it) or `after` (keeps it and releases when its current change is done), or doesn't answer.
  4. The claim also lapses by itself after N requests without an edit to that file. Most blocks end this way, with no negotiation.
  5. s2 waits asleep or does other work, and is woken when the file is free.
- The blocked state stays visible on the board the whole time.

## 1f. Data check: how agents actually edit files (2026-10-09)

**Source:** his own Claude Code transcripts on this machine. Older ones are pruned, so only 11 main sessions were left: 25,333 model requests, 2,007 edits, 768 (session, file) pairs. Copies made by resumes were deduplicated, and subagent sidechains were excluded.

- **70% of files are edited in a single request** and never touched again in that session.
- **When a session edits a file again**, the gap to the next edit, in model requests:

  | Gap | Share of re-edits |
  |---|---|
  | ≤ 1 | 31% |
  | ≤ 3 | 44% |
  | ≤ 8 | 51% |
  | ≤ 50 | 69% |
  | p90 | about 500 |

  The pattern is bimodal: a quick fix-up right after the edit, or coming back much later (after tests, or on a new task).
- **Collisions between his parallel sessions, all in the same directory:** the same file was edited by two sessions within 10, 30 or 60 minutes **0 times**.

**What it means:**
- A short window (N≈3) covers fewer than half of re-edits, so "mid-change" can't be read reliably from edit timing. A long window would block for hours.
- For his own way of working, blocking addresses a collision that never happened in this sample.
- This supports facts-only for v0 (stale-read check, overlap notice, protection against git restore), with blocking as a room option for same-task rooms, tested in Phase 2.
- The sample is small and covers only different-task sessions, so it says nothing about same-task rooms.

**Within one turn** (from a user prompt to the end of the agent's reply; 604 turns with edits, 1,362 turn-file pairs):
- An editing turn makes a median of 10 model requests (p90 = 33) and edits a median of 1 file (p90 = 4).
- 79% of files are edited in exactly 1 request of the turn, 13% in 2, 4% in 3, and 2% in 5 or more.
- When a file is edited 2+ times in a turn, the span from first to last edit is a median of 3 requests (p75 = 6, p90 = 12), a median of 17% of the turn.
- **The right metric for a "since last edit" window** is the gap between *consecutive* edits of a file within a turn, not the first-to-last span. My first figure of 10 used the span and was wrong; he questioned it.
  - Consecutive gaps (n = 536): ≤ 1 request 64%, ≤ 2 82%, ≤ 3 88%, ≤ 4 90%, ≤ 6 94%, ≤ 10 96%.
  - Time between model requests: median 11 s, p75 26 s, p90 81 s.
- **So editing is tight within a turn and loose across turns.** Candidate rule: a file is held until **3 requests** pass with no edit to it (88% of returns covered, about 30 s to 1 min of holding), and it is always released at the end of the turn. Collisions were still zero, so the open question remains whether blocking is needed at all.

Scripts: `~/Research/scripts/war-room-edit-gaps.py` and `war-room-turn-edits.py`, outside the repo because it reads private transcripts; only these aggregate numbers are recorded here.

**Decided (2026-10-09): window = 3.** A file is blocked only while its owner edited it within its last 3 model requests in the current turn. It is always released at the end of the turn, and the gentle-block flow of 1e applies. Earlier claim rules (held for the whole turn, released on commit) are superseded.

## 2. Bets

**His position (2026-10-09):** he rarely runs workflows that need agents to collaborate, but he imagines other people will want it. So the collaboration part is built for *other users*, and his own logs can't validate it.

**Evidence that others want it:** home-made inter-session messaging tools, posted in 2026:
- MCP Agent Mail (2.2k stars);
- postbag;
- AI-Connect (about 1,250 messages since January);
- Barid;
- the "worktrees talking to each other" post;
- the 2,116-message self-analysis (6% of messages were carrying information between agents by hand).

That shows demand from power users, not proof that the pattern works.

**Correction, also from him:** he *deliberately* gives each session completely different work so that sessions don't collide. The zero collisions in 1f are the result of that discipline, not luck.

That is a hidden cost. He plans around a limit: he can't give two sessions related work (the same feature, the same module) safely. Awareness plus the gentle block could remove that limit. This is a bet about him, testable on him.

Split the bets by whose problem they solve:

Each bet: the claim, your prediction (a number), and what result would prove it wrong.

### H1 — Freedom to split (his bet, about him)
- **Claim:** with awareness ON (cards, overlap notices, the 3-request gentle block), he can give 2–3 sessions *related* work in the same area, not only completely separate tasks, and finish a feature faster with no extra breakage.
- **Prediction (his, 2026-10-09):** about **30% faster** wall-clock time per feature with 2 sessions vs 1, with no more breakage than solo.
- **Wrong if:** across about 5 comparable features, the split is less than 10% faster, *or* it causes more breakage (reverted work, broken tests, lost edits) than solo, *or* he ends up separating sessions by hand again anyway.

### H2 — Same-task collaboration (for other users)
- **Claim:** two aware sessions on one task beat one session.
- **Prediction:** unknown. CooperBench says pairs reach about half the success of one agent.
- **Wrong if:** in Phase 2, the aware pair is worse than solo. The aware pair must also beat the blind pair, or awareness adds nothing.

### Tiling — no bet
It's aesthetic: he loves Hyprland. It is built as the frame for the room, not judged by a number.

## 3. Traps (from the research)

| Trap | Evidence | How we avoid it |
|---|---|---|
| Agents ignore a partner's announced plan | CooperBench: 63% of failures | |
| False "done" claims | CooperBench | |
| Chat between agents burns tokens on stale info | r/ClaudeCode agent-teams post | |
| More agents = worse (68.6% → 46.5% → 30%) | CooperBench | |
| Worktrees eat disk, shared DB/ports collide | parallel-manager users | |
| Building a 7th Claude Squad | Claude Squad, Conductor, Superset... | |

## 4. Kill criteria (agreed 2026-10-09)

1. **H1 fails:** keep only the facts layer (cards, the stale-read check, overlap notices, protection against git restore). Remove the block and the handoff protocol.
2. **H2 fails:** no same-task "collaboration" mode. The README says the War Room is for parallel work, not agent teams.
3. **Awareness costs more than 10% extra tokens per session:** cut push and keep only pull.

## 5. Build order (Phase 0 done 2026-10-09)

Low-regret first: each step is useful even if the War Room is killed.
1. **Stale-read check** in `write`/`edit`: refuse if the file changed since this session read it.
2. **Sandbox `.git` fix:** make `.git` read-only in bwrap.
3. **Server mode:** sessions as a process that a window or room can drive.
4. **Waking an idle session** on a message (`send()` only queues today).
5. **The room, awareness OFF:** several sessions plus tiling.
6. **Facts layer:** cards, the `room` tool, overlap notices, protection against git restore.
7. **Talk and the gentle block:** `send`/`ask`/`reply`, the 3-request window, handoff, wait and wake.
8. **Test H1 on himself** (about 5 features); later, the Phase 2 benchmark for H2.
