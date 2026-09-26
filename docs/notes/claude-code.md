# Claude Code (Anthropic)

**Closed source.** `anthropics/claude-code` @ 7779afb holds only docs, example settings,
hooks and official plugins. The engine ships as a bundled binary. These notes come from the
official docs (code.claude.com/docs), the public plugins, and the Agent SDK
(`claude-agent-sdk-python`), which shows the wire protocol. Leaked source is not used.

**In one line:** the harness that set the current vocabulary: CLAUDE.md, subagents, hooks,
skills, plugins, slash commands, plan mode, permission modes. It does very little in the
core, and exposes almost everything as a *file-based extension point*.

## The 7 questions

1. **Loop.** A single-threaded "master loop": sample → run tool calls → append results →
   repeat until a reply has no tool calls. There's no planner graph; planning is a tool
   (TodoWrite) and a mode (plan mode). Subagents (the `Agent`/Task tool) run their own loop
   with a fresh context and return only a summary. That's the main context-hygiene tool.
2. **Messages.** Anthropic Messages API content blocks (`text`, `tool_use`, `tool_result`,
   `thinking`). Sessions are JSONL transcripts under `~/.claude/projects/<cwd>/`; `--resume`,
   `--continue`, fork a session, and checkpoints/rewind (undo file edits and conversation).
3. **Tools.** A small, sharp built-in set: Read, Write, Edit (exact string replace, must
   Read first), Glob, Grep (ripgrep), Bash (persistent shell), WebFetch, WebSearch, Agent,
   TodoWrite, plus MCP tools. Some tools load on demand (deferred schemas via a search tool)
   to keep the prompt small.
4. **Context.** Auto-compact near the limit (a summary replaces history), `/compact` with
   instructions, `/clear`, subagents as context firewalls, prompt caching everywhere,
   CLAUDE.md files (user, project, directory-scoped) loaded as memory, plus an auto-memory
   directory the agent writes to itself.
5. **Control.** Permission modes (default ask, acceptEdits, plan, bypass, auto with a
   classifier), allow/deny rules per tool and pattern (`Bash(git push:*)`), an OS sandbox
   for Bash (bubblewrap / Seatbelt), checkpoints, Esc to interrupt, queued messages to steer.
6. **Extension (its strongest point).** All of it is plain files a user can write:
   - **CLAUDE.md**: memory and instructions.
   - **Hooks**: shell commands on lifecycle events (PreToolUse, PostToolUse, Stop,
     UserPromptSubmit, SessionStart, PreCompact...). Exit code or JSON output can block,
     rewrite or add context. The `ralph-wiggum` plugin turns a *Stop hook* into an
     infinite work loop: a whole agent technique built from one hook.
   - **Skills**: a folder with `SKILL.md` (name + description in frontmatter); only the
     description sits in context until the model decides to load the body (progressive
     disclosure).
   - **Subagents**: markdown files with a prompt, tools and model.
   - **Slash commands**, **output styles**, **status line**, **plugins** (bundles of all
     the above) and **marketplaces**.
   - MCP servers, headless `claude -p` with stream-JSON output.
7. **The SDK protocol.** The Agent SDK spawns the CLI and talks **stream-JSON over stdio**.
   The CLI sends `control_request` messages back to the host (`can_use_tool`,
   `hook_callback`...) so your code can answer permission prompts and hooks. The engine is
   the same binary; the SDK is only a transport.

## Steal / avoid

- Steal: extension points as plain files (skills, hooks, agents as markdown); progressive
  disclosure for skills and tool schemas; subagents as context firewalls; Edit as exact
  string replace with a read-before-write guard; permission rules as `Tool(pattern)`.
- Avoid / can't copy: it's closed, so you learn it as a *user* and from the protocol, not
  the code. Tied to Claude models.

## What it provides

Terminal UI, IDE extensions, desktop and web apps, headless mode, Agent SDK (Py/TS),
GitHub Actions, plugins marketplace, remote/cloud sessions.
