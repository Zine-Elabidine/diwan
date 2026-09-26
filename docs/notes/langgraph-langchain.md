# LangGraph + LangChain (+ Deep Agents)

`langchain-ai/langgraph` @ 7daa3ab, `langchain-ai/langchain` @ 80b7409 (v1 in
`libs/langchain_v1`), `langchain-ai/deepagents` @ 60c0228 · Python · MIT.

**In one line:** three layers, each on top of the last (from `deepagents/libs/ARCHITECTURE.md`):

```
Deep Agents   opinionated harness: defaults, middleware, backends, profiles
LangChain     agent abstraction: model + tools + middleware → agent loop  (create_agent)
LangGraph     runtime: state, checkpoints, streaming, interrupts           (Pregel)
```

## LangGraph: the runtime

- The agent is a **graph**: nodes read and write typed **state** through **channels**
  (with reducers, e.g. messages append). Execution is a **Pregel / BSP** loop:
  `pregel/_loop.py` `tick()` = prepare the tasks triggered by the channels updated last
  step → run them (in parallel) → apply writes → **checkpoint** → repeat until no tasks.
- **Checkpoints after every super-step** (`BaseCheckpointSaver`: memory, SQLite,
  Postgres). This one mechanism gives you resume after crash, **time travel** (replay or
  fork from any checkpoint), and **human-in-the-loop**: `interrupt()` inside a node stops
  the graph, persists, and resumes later with `Command(resume=...)`.
- Streaming modes (values, updates, messages, custom), a long-term `Store` (cross-thread
  memory), subgraphs, retries and caching per node.

## LangChain v1: `create_agent` + middleware

- `langchain/agents/factory.py` `create_agent(model, tools, system_prompt, middleware,
  response_format, checkpointer, store...)` compiles to a LangGraph graph: model node ⇄
  tools node, loop until no tool calls.
- **Middleware** is the extension point (`agents/middleware/types.py`):
  `before_agent`, `before_model`, `after_model`, `after_agent`, and the wrappers
  `wrap_model_call(request, handler)` / `wrap_tool_call`. Built-ins: summarization,
  human_in_the_loop, model/tool retry, model fallback, call limits, PII redaction,
  context_editing, tool_selection, todo, shell tool, provider tool search.
- `langchain_core` gives the provider-neutral `BaseChatModel` and message types
  (`AIMessage.tool_calls`...) across dozens of providers.

## Deep Agents: the harness

`create_deep_agent()` = `create_agent()` with a default middleware stack: planning (todos),
a virtual **filesystem** with pluggable **backends** (state, disk, store, sandbox),
subagents, summarization/offloading, skills, memory (AGENTS.md), prompt caching, HITL.
Basically the Claude Code recipe, rebuilt on LangGraph. Also a CLI (`libs/code`).

## The 7 questions

1. **Loop.** Not a while-loop but a graph tick; the "agent loop" is an edge from tools back
   to the model.
2. **Messages.** LangChain message objects in graph state, with a reducer.
3. **Tools.** `BaseTool` / decorated functions, `ToolNode`, parallel by default.
4. **Context.** Summarization middleware, context editing, offloading to the filesystem.
5. **Control.** Checkpoints + `interrupt()` = the best HITL/resume/time-travel story here.
6. **Extension.** Middleware; graphs as nodes (subgraphs); MCP adapters.
7. **Steal / avoid.**
   - Steal: checkpoint-per-step as the basis of resume, time travel and HITL; middleware
     hooks around model and tool calls (clean, typed); state reducers.
   - Avoid: abstraction depth. A simple loop becomes graph + channels + reducers + runnables,
     which is the core of the community complaints (see feedback.md).
