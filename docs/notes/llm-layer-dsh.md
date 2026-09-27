# How DeepSeek Harness supports "all" LLMs

Read 2026-09-27: `refs/deepseek-harness/packages/llm/*` and the library it delegates to,
`refs/pi-mono/packages/ai` (pi-ai).

## The layering

```
agent loop / session log
      │  neutral Message + ContentBlock  (packages/llm/llm/src/types.ts)
      ▼
LlmRuntime  (ctx.llm)       routes by provider name → adapter; retry policy; errors → stable codes
      │
      ├── dsh-llm-deepseek   native adapter, DeepSeek only (files API, pricing, SSE)
      └── dsh-llm-pi-ai      one adapter for everything else
              │  neutral → pi-ai messages (context.ts, replay.ts)
              ▼
            pi-ai            ~10 wire protocols, ~50 providers, generated model catalog
```

So the "crazy good" support is mostly **pi-ai**. DeepSeek's own work is the neutral format,
the adapter contract, replay, retries, token metering and the config UX.

## 1. Three separate concepts: protocol, provider, model

pi-ai (`src/types.ts`) keeps them apart, which is why adding a provider is often config-only:

- **API (wire protocol):** `openai-completions`, `openai-responses`, `azure-openai-responses`,
  `openai-codex-responses`, `anthropic-messages`, `bedrock-converse-stream`,
  `google-generative-ai`, `google-vertex`, `mistral-conversations`, `pi-messages`. One module
  per protocol in `src/api/` (e.g. `openai-completions.ts` 1,726 lines).
- **Provider:** a base URL + auth + which API it speaks (~50: anthropic, openai, google,
  groq, deepseek, mistral, openrouter, cerebras, fireworks, huggingface, xai, bedrock...).
- **Model:** a catalog entry: `contextWindow`, `maxTokens`, `reasoning`, `thinkingLevelMap`,
  `input` modalities, `cost` per token type, `promptCache` lifetimes, `compat` overrides.
  **Generated** by `scripts/generate-models.ts` from **models.dev** and the OpenRouter API,
  with hand corrections, into `src/providers/data/*.json`.

A gateway nobody ships (vLLM, a company proxy) is declared in config with just `api`,
`baseURL` and a `models` list (see the `acme-gateway` example in `llm-pi-ai/README.md`).

## 2. "OpenAI-compatible" is a lie: the compat flags

`OpenAICompletionsCompat` in pi-ai `src/types.ts` lists what actually differs between
providers that all claim the OpenAI Chat Completions API. Each is auto-detected from the URL
and can be overridden:

- `supportsStore`, `supportsDeveloperRole` (`developer` vs `system` role)
- `supportsReasoningEffort`, `supportsUsageInStreaming`, `supportsFinishReason`
- `maxTokensField`: `max_completion_tokens` vs `max_tokens`
- `requiresToolResultName`, `requiresAssistantAfterToolResult`
- `requiresThinkingAsText`, `requiresReasoningContentOnAssistantMessages`
- **`thinkingFormat`**: how to turn reasoning on. There are 11 dialects: `openai`
  (`reasoning_effort`), `openrouter` (`reasoning: {effort}`), `deepseek` (`thinking: {type}`),
  `together`, `baseten`, `zai`, `qwen` (`enable_thinking`), `chat-template`
  (`chat_template_kwargs`), `qwen-chat-template`, `string-thinking`, `ant-ling`.

The Responses and Anthropic protocols have their own compat sets (strict tools, grammar tools,
long cache retention, session-affinity headers, eager input streaming...).

**Lesson:** the translator per protocol is only half the work. The other half is a table of
per-provider quirks. Keep that table **as data**, not as `if provider == ...` branches (the
Hermes anti-pattern).

## 3. Switching models mid-session: `transformMessages`

pi-ai `src/api/transform-messages.ts` runs on every request. The rules:

1. **Same model** = same `provider` + `api` + `model`. Only then is private data replayed.
2. **Thinking from another model becomes plain text**, not dropped (empty thinking is dropped).
3. **Redacted / encrypted thinking** from another model is dropped: it's unreadable anyway.
4. **Gemini `thoughtSignature` on tool calls** is removed for other models.
5. **Tool-call ids are normalised** for the target: OpenAI Responses ids are 450+ chars with
   `|`; Anthropic requires `^[a-zA-Z0-9_-]+$`, max 64. A map rewrites the matching tool results.
6. **Images** become a placeholder text if the target model has no vision.
7. **Errored or aborted assistant messages are skipped**: replaying half a turn causes API
   errors ("reasoning without following item").
8. **Orphaned tool calls get a synthetic result** ("No result provided", `isError`), since
   every API demands a result for every call.
9. System messages that land between a tool call and its results are held until after the results.

DeepSeek adds `foreignAssistant()` (`llm-pi-ai/src/replay.ts`): a message with no replay state
is marked with a fake api `'dsh-foreign'`, so rule 1 can never treat it as same-model.

## 4. Replay envelope

`ReplayEnvelope` (`llm/src/types.ts`) = `response` (ids, native stop reason, provider-reported
model) + `blocks[]` (one entry per content block: `textSignature`, `thinkingSignature`,
`redacted`, `thoughtSignature`). Stored opaque on the assistant message; if assembly drops a
block, it drops the entry at the same index; a length mismatch discards the whole envelope.

## 5. One streaming vocabulary

Every adapter emits the same chunks (`StreamChunk`): `block-start`, `text-delta`,
`reasoning-delta`, `tool-call-delta` (raw argument JSON), `block-end` (with the assembled
block), `usage` (before finish), `finish` (reason + replay envelope). Adapter exceptions are
normalised into a terminal `error`/`aborted` finish, so the loop never sees a raw throw.

Finish reasons: `stop`, `tool-calls`, `max-tokens`, `aborted`, `error`, extendable.

## 6. Usage normalised to disjoint counts

`TokenUsage`: `inputTokens` is **uncached input only**; `cacheReadTokens` and
`cacheWriteTokens` are separate (billed input = sum of the three). Providers that fold cache
hits into `prompt_tokens` (DeepSeek, OpenAI) have them subtracted. `reasoningTokens` is
optional. pi-ai also computes `cost` per type from the catalog. Without this normalisation,
cost across providers can't be compared.

## 7. Errors and retries

- Failures become stable codes: `RATE_LIMIT`, `SERVER`, `TIMEOUT`, `TRANSPORT`,
  `EMPTY_RESPONSE`, `CONTEXT_WINDOW_EXCEEDED`, `QUOTA`, `ACCOUNT_QUOTA`,
  `INVALID_CREDENTIAL`, `MISSING_CREDENTIAL`, `IMAGE_OFFLOAD_REQUIRED`, plus HTTP status,
  provider `retry-after` and request id.
- **Retry policy is owned by the provider config** (`retryPolicy` per route): `normal` (5
  retries of the transient codes, exponential backoff 500 ms → 10 s, 10% jitter) or `always`.
- The `llm-retry` plugin executes it at the loop's `agent/request-error` point, and each
  scheduled retry is **written to the log before the wait**.
- `CONTEXT_WINDOW_EXCEEDED` is not retried; it triggers compaction then retry.

## 8. Capabilities declared per model, used by the loop

`LlmResolvedModelInfo` tells the loop how *this* model behaves:
- `context.contextWindow`, `defaultMaxTokens`
- `reasoning.efforts` + `defaultEffort` (the selector shows only what the model supports)
- `systemPromptUpdate: 'in-history'`: a changed system prompt can be appended instead of
  rewriting message 0 (cache preserved)
- `toolUpdate: 'addition-only' | 'in-history'`: added/removed tools can be appended as blocks
  (Anthropic `defer_loading`) instead of rewriting the tool list
- `inputModalities`

## 9. Other details worth copying

- **`prepareCall`**: resolve model metadata and bind the dispatch to the same config
  generation, so a settings change mid-request can't mix one config's limits with another's endpoint.
- **Credentials by reference**: config holds `apiKeyEnv: OPENAI_API_KEY`, never the key; a
  missing one fails with `MISSING_CREDENTIAL` before any network call.
- **Model discovery**: for unknown gateways, `GET {baseURL}/models`; catalog routes answer offline.
- **Files never go to providers natively**: a file block becomes handle text (name, size,
  read-only path) and the model reads it with tools.
- **Images**: per-request pixel budget, byte limits, and "offload oldest images" when over.
- **Token meter**: deterministic estimate from the log, no model call; the provider's reported
  usage is reused only when the request envelope is identical.

## What this means for our decision #2

- The neutral format is confirmed, and so is the replay envelope.
- **Change to my proposal:** foreign reasoning becomes **text**, not dropped (pi-ai rule 2).
- Architecture to copy: protocol / provider / model as three things; **compat quirks as data**;
  a `transform_messages` pass with the 9 rules; one chunk vocabulary; disjoint usage; stable
  error codes; retry policy per provider.
- **Model catalog:** generate ours from models.dev (free JSON), like pi-ai.
- The Python counterpart of pi-ai is **litellm**. The open question stays: write our own
  translators (openai-completions + anthropic-messages first), or put litellm underneath.
