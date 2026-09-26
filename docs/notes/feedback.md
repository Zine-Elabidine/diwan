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
