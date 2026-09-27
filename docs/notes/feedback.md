# What people say (collected 2026-09-26)

Sources: Hacker News threads via the Algolia API, plus r/LocalLLaMA. Scores are HN points.

## Per runtime

**DeepSeek Harness** ([launch, 747 pts](https://news.ycombinator.com/item?id=49285244))
- The README says almost nothing: "But like, what is it?" You have to go to the docs.
- Endless "why is every harness Node.js?" debate. Answers: npm distribution, easy plugins,
  and everyone copied Claude Code. Counterpoints: Codex is Rust, and TUIs in Node eat RAM.
- "First-party harnesses don't make sense when the Pareto frontier changes every month; I want
  the same working surface across models, like the same text editor across languages."
- An ecosystem sprang up in weeks (plugin directories, VS Code, pricing boards), but one
  "Ask HN: anyone using dsh in a customer-facing product?" got zero answers.

**Hermes** ([52 pts](https://news.ycombinator.com/item?id=48419000), [migrate from OpenClaw, 122](https://news.ycombinator.com/item?id=48586005))
- "Even after disabling tons of skills in the setup wizard... over 10k of context used just
  to list them all." Skill/tool bloat is the most concrete complaint.
- Several people **rebuilt the parts they wanted** instead: "I asked GPT to create a Pi
  extension that has the magic of Hermes, which for me was long-term memory and cron."
- Trust: plagiarism accusations (Evolver), a default install routing web search to a third
  party, astroturfing suspicions. "None of these projects inspire enough trust to be
  installed on the machine I use every day."
- Personal agents: "As personal assistants they all fall short. It's too difficult to shape
  their output... couldn't get the models to stop being so verbose."

**Claude Code** ([unusable for complex tasks, 1364](https://news.ycombinator.com/item?id=47660925), [Unpacked, 1128](https://news.ycombinator.com/item?id=47597085), [HarnessTax, 232](https://news.ycombinator.com/item?id=49733726))
- Still rated "best overall" by people who tried them all, and its features (plan mode, todo,
  ask-user, hooks) are what everyone copied.
- But: a ~500k-line codebase, TUI flicker and resize freezes, an "80kb" initial prompt,
  CLAUDE.md files "exploding" with guard rails, and the model pushing to wrap up sessions early.
- Anthropic now drops the todo tools for frontier models: planning scaffolding matters less
  as models improve ("as the model gets smarter, you need to tell it less").

**Codex** ([sudo workaround, 664](https://news.ycombinator.com/item?id=48348578))
- Praised as "no fluff, minimal, just does its job", and it doesn't flicker.
- The sandbox is real but not magic: it escalated through the docker group, and "my codex
  just uses python to write files around the sandbox". Takeaway: run agents in a container
  or user namespace, not as your own user.

**LangChain / LangGraph** ([we chose LangGraph, 83](https://news.ycombinator.com/item?id=43468435), [why we no longer use LangChain, 480](https://news.ycombinator.com/item?id=40739982))
- LangChain: "meaningless abstractions, poorly documented, breaking changes"; "I'd rather
  use the vendor's official package." The main defence is bring-your-own-key apps across
  many providers.
- LangGraph gets respect as "a legitimate piece of workflow software": its real value is
  **human-in-the-loop with time travel** (checkpoint, edit, rewind, retry).
- PydanticAI is often named as the balance between control and abstraction.

## Cross-cutting findings

1. **The harness matters as much as the model, and less is often more.** HarnessTax and
   the [empirical harness study](https://news.ycombinator.com/item?id=49753878) (225 pts):
   switching to a lean harness (pi) can halve cost with no loss; for bash-capable models,
   bash-only tools beat predefined tools; planning mainly cuts *cost* for strong models;
   context management extends how long an agent can run.
2. **"Cost per successful task" is the metric people want**, and few harnesses show it.
3. **Model portability.** Users want one surface across models; first-party harnesses
   show no measurable advantage on the benchmarks cited.
4. **Minimal core + extensions wins with builders.** Pi (400 pts,
   ["minimal coding agent"](https://news.ycombinator.com/item?id=46844822)): "small and
   observable is excellent"; people extend it rather than adopt the big ones. It's also
   OpenClaw's engine.
5. **Trust and sandboxing** are unsolved for personal agents: users want isolation by
   default (container, namespace), not prompt rules or security theatre.
6. **Bloat is the common failure** of the big products: huge prompts, huge codebases,
   dozens of default skills.

## DeepSeek Harness on Reddit (r/LocalLLaMA, Aug–Sep 2026)

Threads: ["DSH is insanely good" (235 pts, 167 comments)](https://www.reddit.com/r/LocalLLaMA/comments/1vw10m3/),
["it escaped from its workspace folder" (118, 157)](https://www.reddit.com/r/LocalLLaMA/comments/1vxi7gp/),
["Opencode vs DSH" (21, 72)](https://www.reddit.com/r/LocalLLaMA/comments/1w53mwu/),
["DSH vs OMP" (7, 28)](https://www.reddit.com/r/LocalLLaMA/comments/1whz401/).

**Top complaint: no terminal interface.**
- "I hate that there is still no CLI/TUI version of it to manage it via ssh" (**112 upvotes**, the thread's top comment)
- "very clunky, CLI would be better" (36); "I prefer the terminal to yet another webui in my hundreds of tabs" (28)
- "DSH is lacking cli which is important to me"; "leaning towards TUI harnesses and less chatty UIs"
- The web UI only listens on localhost, so people use ssh tunnels or Tailscale to reach it remotely.

**Other complaints**
- Heavy: slow to boot ("slower than Gemini CLI"), RAM hog, too many npm deps; an install crash from a Node heap OOM (24 upvotes).
- **The sandbox only confines writes**; reads anywhere are allowed, so the agent wandered through the user's
  other files. Replies: "the workspace setting is a prompt-level convention, not an OS boundary";
  "never trust an application's own sandbox"; people run agents as a separate OS user, in Docker, or with bwrap/landrun.
- Prefix-cache misses on every turn at times; compaction "amazing when it works" but sometimes doesn't fire.
- Loops that redo finished tasks; subagent timeouts; better one-shots than continuations.
- The built-in web_search needs a DeepSeek API key and bills every search as model usage; no free search plugin.
- English docs read like a rough translation.

**What people love**
- **Modify it by asking it**: "I got it to integrate with SimpleX by simply asking it to"; plugins are easy.
- Progressive setup (no upfront config wall, unlike Hermes-style harnesses).
- Any provider out of the box; several models at once (agent teams across GPUs and machines).
- Tenacity ("a relentless harness that just keeps doing stuff") and good results with local Qwen 3.8.

**Who uses what**
- "DSH if you're a vibe coder. Pi or OMP or something you wrote yourself if you've got even the tiniest engineering bone."
- Engineers keep landing on **Pi** (minimal, "nothing built in, so it can become anything"), OpenCode (TUI), or their own.

**Takeaways for us**
1. An interactive terminal UI in v0, usable over SSH. The biggest gap DSH leaves.
2. Fast startup: lazy imports; `cambium --help` must be instant.
3. Real isolation by default, **reads included** (bwrap/Landlock or a container), not a prompt rule.
4. Cache discipline and compaction that reliably fire are what users notice.
5. Free defaults: no built-in tool that silently bills a paid API.
6. Progressive setup and "extend it by asking it" are loved; keep both in mind for extensions.
