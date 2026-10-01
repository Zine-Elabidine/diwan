# Code review: Diwan + Tarjuman (2026-10-01)

A health check before building further. The question was: is the code clean, easy to
maintain, and easy to extend? Findings are ranked by **how much they hurt the next features**
(the decisions file, summary + recall, War Room, swarms), then by risk. Each one has a
location, a fix and an effort (S < half a day, M ≈ 1 day).

## Health in numbers

| | Diwan | Tarjuman |
|---|---|---|
| Source lines | 1,990 | 1,785 |
| Tests | 50 passing | 77 passing |
| Coverage | 79% (`cli.py` 24%, `tui.py` 74%) | 90% (`fake.py` 0%: only Diwan exercises it) |
| ruff (E, F, B, UP, SIM, PL…) | 31 findings | 12 findings |
| pyright | 78 errors | 53 errors |
| Lint/type config, CI | none | none |

Most pyright errors come from one design choice (finding 14) and from union types that
are never narrowed; the ones checked are false alarms, but at this volume a real error would
go unnoticed. The ruff findings include real leftovers: unused imports, a dead
variable in `grep`, and lambdas assigned to names.

**What's sound and should stay:** the neutral message format and its transform, the
replay envelope, the append-only tree log whose events store the exact rendered text, the
Cancel design, the stable error codes, and few dependencies. The structure has drifted;
the foundations are fine.

---

## A. Fix first: correctness and safety

### 1. Read-only tools reach anywhere, without approval (high)
`tools.py:134` (`resolve`): `read`, `grep` and `glob` are read-only, so they never ask,
and they accept absolute paths and `..`. The model can read `~/.diwan/env` (the API keys)
or `~/.ssh` without you seeing a dialog. Write tools ask, but nothing tells you that the
target is outside the project.
**Fix:** a path policy in a shared tool context. Inside the project is free. Outside asks
for approval. A deny list (`~/.diwan/env`, `~/.ssh`, `.env*`) is always refused. **S**

This closes the tools that run without approval. It is **not containment**: `bash` can still
`cat ~/.diwan/env`, and only its approval stands in the way, which `-y` or "always bash"
removes. Real containment needs a sandbox for commands (namespaces, or a container), a later
item.

### 2. Any unexpected error closes the app (high)
`agent.py:198` catches only `KeyboardInterrupt` and `TarjumanError`. In the TUI, the turn
runs in a Textual worker with the default `exit_on_error=True` (`tui.py:370`), so a bug in
the gauge, a log write that fails or a broken provider response closes the whole app. The
plain loop (`cli.py:150`) dies the same way.
**Fix:** `turn()` catches `Exception`, logs an `error` event with the traceback, and ends
the turn with reason `"error"`. The UI shows it. The session survives. **S**

### 3. `context()` is quadratic after a model switch (medium)
`agent.py:149-157`: with no report from the current model, the loop computes
`tokens.ratio(request[:i])` for **every** older reply, although only the most recent one is
used (`borrowed = borrowed or ratio`). Measured: 5 ms at 100 replies, 60 ms at 400,
206 ms at 800 (so about 0.8 s at 1,600, extrapolated), and it runs ~3 times per step.
**Fix:** compute the ratio only while `borrowed` is still unset, and compute the view once
per step (see 8). **S**

### 3b. Quitting during an approval leaves the process hanging (medium)
`tui.py:389`: the agent thread waits on `answer.result()` with no timeout. Reproduced: a
`write` that needs approval, then Ctrl+Q while the dialog is open. The app closes, but the
process never exits, because the worker thread is still waiting.
**Fix:** when the app closes, answer every pending approval with "no" and interrupt the
agent; the wait also watches the turn's Cancel. **S**

---

## B. Architecture: what makes features painful

### 4. `agent.py` has six jobs
`Agent` holds the loop, retries, interrupts, the switch guard, the context gauge and the
clearing trigger (373 lines). `context()` (`agent.py:140`) is a query that also writes
`self._ratio` (line 180). Adding the summary tier and `recall` here would push it past 500
lines.
**Fix:** split it:
- `Agent`: the loop, steps and tools.
- `ContextManager` (new module): view, gauge, clearing, the switch fit check, and later the
  summary.
- A small retry policy.

The Agent asks the context manager for "the request to send now" and tells it "a step
happened". **M**

### 5. The provider interface is informal
Diwan's `Provider` protocol (`agent.py:30`) declares only `stream`. Everything else is
reached by `getattr` behind a catch-all (`_ask`, `agent.py:355`), so a typo or a crash in a
provider silently becomes "unknown". `Fake` gained methods one at a time. In Tarjuman,
`OpenAIChat` and `Anthropic` duplicate client setup, `info`, `context_window`, `stream`,
`_events` with its cancel wiring, and `complete` (about 60 lines each,
`anthropic.py:76-195` vs `openai_chat.py`).
**Fix:** Tarjuman defines the interface (`Provider`: `provider`, `protocol`, `stream`,
`complete`, `info`, `context_window`, `target`) and an `HTTPProvider` base that owns the
shared parts. A protocol only supplies `body()`, its URL path and its `_Parser`. `Fake`
implements the full interface. Adding Gemini or Responses then means writing one body and
one parser. **M**

### 6. Two front-ends duplicate everything
Plain mode (`ui.py`, `cli.py`) and the full-screen app (`tui.py`) each implement, on their
own:
- the slash commands (`cli.py:152-177` and `tui.py:329-368`) and their `HELP` text
  (`cli.py:26`, `tui.py:35`);
- approvals and the "always" set (`ui.py:196`, `tui.py:383`);
- the write/edit preview (`ui.py:221` `_preview`, `tui.py:116` `diff_text`);
- event handling (`ui.py:130` and `tui.py:399`: two `isinstance` chains of 15-18
  branches);
- token formatting (`ui.fmt_tokens` and `models._tokens`).

They have already drifted: `/cost` shows the agent's total in plain mode but the session
total in the TUI. The TUI reads `agent.context_use` directly while plain mode listens for
events. Every new command or event means editing both.
**Fix:**
- a `Session` object (agent, router, log, approval policy, `/new`, `/model`) that both
  front-ends drive;
- a command registry (`commands.py`: name, help, handler) that generates `/help`;
- one `present.py` for summaries, previews and numbers.

The front-ends become renderers only. **M** (the biggest win for "features aren't a pain").

### 7. Tools are closures in one 170-line function
`tools.py:132` `make_tools` defines every tool inline. Each JSON schema is written apart
from its function signature, so the two can drift. `Spec.cancellable` (`tools.py:31`) is a
one-off flag, and the next capability (path policy, a project index, a sub-agent handle)
would add another.
**Fix:** one module per tool, a small `Tool` class (`name`, `description`, `schema`,
`readonly`, `run(args, ctx)`) and a `ToolContext` (cwd, cancel, path policy, limits)
passed to every tool, plus a registry. The path policy of finding 1 lives there. **M**

### 8. The log is stringly typed and rebuilt on every read
Event types are bare strings (`"message"`, `"mask"`, `"model_switch"`…). `messages()`,
`masked()`, `mask_points()` and `current_model()` (`log.py:92-114`) each walk the branch
again, and `messages()` rebuilds every `Message` object from its dict, several times per
step. It's linear today (17 ms per step at 1,600
steps), but every new event kind adds another walk.
**Fix:** event kinds as constants, and the branch state (messages, masks, current model)
kept up to date on `append` instead of rebuilt on every read. **S-M**

### 9. Switching models needs the caller to rebuild the system prompt
`models.switch` (`models.py:72`) calls `system_prompt(...)` and passes the string to
`agent.use`. Every caller that switches (and later sub-agents) must know to do this.
**Fix:** `Agent` takes a prompt builder `(provider, model) -> str`. **S**

### 10. Interrupts reuse `KeyboardInterrupt`
`agent.py:196` raises `KeyboardInterrupt` for Esc. That mixes our own stop with the
operating system's Ctrl+C, and any `except KeyboardInterrupt` in a tool or library gets
both.
**Fix:** a dedicated `Interrupted(BaseException)`. **S**

### 11. Error codes as string literals
`agent.py:129` (`"CONTEXT_WINDOW_EXCEEDED"`), `:262` (`"SERVER_ERROR"`) and `:267`
(`"CANCELLED"`). A typo would never be caught.
**Fix:** use `tarjuman.errors` constants. **S**

### 12. Name collisions force aliases
- Tarjuman's `limits` module collides with Diwan's `Limits` and the `limits` parameter, so
  `agent.py:10` imports it as `tokens`.
- Diwan's `context.py` is imported as `ctx`, next to `Agent.context()`, `ContextUse` and
  `ContextChanged`.

**Fix:** rename `tarjuman.limits` to `tarjuman.tokens`, `diwan/context.py` to `clearing.py`
(it becomes part of the context manager in 4), and `Agent.context()` to `measure()`. **S**

### 13. Tarjuman knows about Diwan
`tarjuman/data/providers.json:10` sends `HTTP-Referer: github.com/Zine-Elabidine/diwan` and
`X-Title: Diwan` for every OpenRouter user. Tarjuman is meant to stand alone.
**Fix:** `connect(..., app_name=, app_url=)`, which Diwan passes. **S**

### 14. Types that say one thing and accept another
`ToolResult.content` is typed `list[Text | Image]` but accepts a string
(`types.py:69-76`). That causes most of the pyright errors in both repos and hides real
ones. The cases checked (`agent.py:164`, `:180`, `openai_chat.py:325`) are guarded by the
surrounding logic, so they're narrowing gaps rather than bugs, but the checker can't tell.
Stop reasons are assigned plain strings where the type says `Stop`.
**Fix:** a `ToolResult.text(...)` constructor (or type the field as `str | list`), narrow
the block unions, and add pyright to the checks so it stays at zero. **S-M**

---

## C. Hygiene

15. **No lint, type or test gate.** Add ruff and pyright settings to both `pyproject.toml`
    files plus one `check` command (ruff, pyright, pytest) to run before each commit. Fix
    the current findings: unused imports (`tui.py:21`, `openai_chat.py:17`), the dead
    `files_hit` (`tools.py:228`), lambdas assigned to names (`context.py:56`,
    `tools.py:86`). **S**
16. **Test helpers imported from another test file.** `call` and `home` come from
    `test_agent.py`; move them to `tests/conftest.py`. `cli.py` is 24% covered: the
    command registry (6) makes it testable. **S**
17. **Small inconsistencies.**
    - The version is in both `__init__.py` and `pyproject.toml`.
    - `ui.short()` uses the process cwd, not the project's.
    - The `bash` tool description still says "use it to list and search files"
      (`tools.py:297`), against the prompt's "use grep/glob". **XS**

---

## D. Readiness for War Room and swarms (checked, not built)

The foundations are promising: the log is already a tree (a sub-agent can be a branch), and
Cancel is a clean signal. What blocks several agents at once:

| Need | Today | Change |
|---|---|---|
| Know which agent an event came from | one `on` callback, events carry no agent id | events wrapped with `agent_id`, one event bus the UI subscribes to |
| Stop a whole group | one `Cancel` per turn | `Cancel.child()`: a parent's cancel cascades to its children |
| Run in parallel | blocking provider calls, one worker thread | a thread pool or async in Tarjuman (already on its list) |
| Sub-agent history | one head per log | a child branch or child log linked to the parent |
| Approvals per agent | one "always" set per front-end | the approval policy lives in `Session` (6), per agent |

Findings 4, 5, 6 and 7 are the prerequisites. None of the multi-agent work should start
before them.

---

## Proposed order

1. **Safety (½ day):** 1, 2, 3, 3b, 11, 15, 16, 17. Small, low risk, immediate value.
   pyright runs in report-only mode until phase 2 brings it to zero, so the new check
   doesn't fail from the first commit.
2. **Core structure (1-2 days):** 5 (provider interface and base), 8 (log state), 4 + 9 + 10
   (split the Agent, prompt builder, own interrupt), 12 (names), 13, 14 (types to zero).
3. **Extensibility (1-2 days):** first, tests for the slash commands in both front-ends
   (`cli.py` is 24% covered, so "behaviour unchanged" can't be checked yet). Then 6
   (Session, command registry, shared presentation) and 7 (tool classes, ToolContext, path
   policy).

Then the next features (decisions file, summary, `recall`) go into the new context
manager instead of `agent.py`.

Each step is its own commit with all tests green, and behaviour is unchanged unless the
finding says otherwise.

## Progress

- **Phase 1 done** (2026-10-01): findings 1, 2, 3, 3b, 11, 15, 16, 17.
- **Phase 2 done** (2026-10-01):
  - 5: `tarjuman.Provider` and `HTTPProvider` (Tarjuman 4b1203e);
  - 13: `connect(app=App(...))`;
  - 8: the branch state is cached by head (0.02 ms per step instead of 9.2 at 2,000 messages), and event kinds are a `Kind` enum;
  - 10: `Interrupted`;
  - 9: the prompt builder; the `/model` fit check now measures the new model's own prompt;
  - 4: `events.py`, `ContextManager` in `context.py`, `clearing.py`;
  - 12: the renames;
  - 14: pyright is at 0 and blocking in both repos.

  Reaching 0 surfaced one real bug: glob used `Path.full_match`, which Python 3.12 doesn't have, so Diwan now requires 3.13. A live smoke test passed: `-p` with tools, and a session with `/model`, `/cost` and `/exit`.
- **Phase 3:** next.

---

## Target layout (decided before phase 2, so nothing is renamed twice)

**Tarjuman**

| Module | Holds |
|---|---|
| `provider.py` (new) | `Provider` protocol: `provider`, `protocol`, `stream()`, `complete()`, `info()`, `context_window()`, `target()`. `HTTPProvider` base: client setup, `info`, `context_window`, `stream` / `_events` with Cancel, `complete`. A protocol supplies `body()`, its request path, its `_Parser`, its auth headers and its error mapping. |
| `openai_chat.py`, `anthropic.py` | Subclasses of `HTTPProvider`: wire format only |
| `tokens.py` (was `limits.py`) | windows from `/models`, `chars`, `estimate`, `ratio` |
| `providers.py` | `connect(name, ..., app=App(name, url))`. The app identity headers come from the caller; `providers.json` only says which header carries what. |
| `fake.py` | implements the full `Provider` protocol |

**Diwan**

| Module | Holds |
|---|---|
| `agent.py` | `Agent`: the loop, steps, tools, retries, `Interrupted`. Public surface unchanged: `turn`, `interrupt`, `use`, `context_use`, `total`. Takes a prompt builder `(provider name, model) -> str`, called at construction and on `use()`. |
| `events.py` (new) | what the agent tells the UI: `StateChanged`, `ToolStarted`, `ToolFinished`, `Retrying`, `ContextChanged`, `ContextCleared`, `TurnEnded`, `UIEvent` |
| `context.py` | `ContextManager`: the view sent to the model, `measure()` (returns `ContextUse`, including its chars-per-token), the fit check for `/model`, when to clear. Later: the summary tier and `recall`. |
| `clearing.py` (was the body of `context.py`) | pure functions: `plan`, `apply`, placeholders |
| `log.py` | event kinds as constants, branch state cached by head id |
| `paths.py` | the path policy (phase 1) |
| phase 3: `session.py`, `commands.py`, `present.py`, `tools/` | Session shared by both front-ends, command registry, shared presentation, one module per tool + `ToolContext` |
