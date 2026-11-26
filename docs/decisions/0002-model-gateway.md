# 0002. Thin in-house model gateway on official SDKs, with a LangChain adapter

- Status: Accepted
- Date: 2026-11-26

## Context

Four products will call LLMs and embedding models from several providers: Anthropic,
OpenAI, Azure OpenAI, AWS Bedrock, Google Gemini and Groq. We want:

- one place for routing, fallback, retries, timeouts, error handling, cost tracking
  and logging;
- the freedom to change providers by editing config rather than product code;
- predictable behaviour we can test without network access or keys, in a codebase
  held to strict typing and an 80% coverage gate;
- PII-safe logging, with prompt text never logged by default;
- LangChain available to products (Query Desk RAG, for example) without LangChain
  sitting in every model call.

We considered three options:

1. **LangChain everywhere.** Products use `ChatAnthropic`, `ChatOpenAI` and so on
   directly, with fallbacks from `with_fallbacks()`.
2. **LiteLLM** as a unified client or proxy.
3. **A thin in-house gateway** on the official SDKs, plus a LangChain adapter.

## Decision

Option 3. `platform/gateway` ships the package `ai_platform_gateway`. It exposes
`complete()` and `embed()`, and one adapter per provider built on that provider's
official SDK (`anthropic`, `openai`, `boto3`, `google-genai`, `groq`). All adapters
sit behind a small `Provider` protocol.

- **Routing is config.** `models.yaml` holds a model catalog (ids, prices, context
  limits, capability flags) and aliases, each with an ordered fallback chain.
- **The gateway owns reliability.**
  - tenacity retries: 3 attempts, exponential backoff with jitter, only on
    429/5xx/timeouts/connection errors. SDK-level retries are disabled so retries
    don't multiply.
  - Failover after retries run out; 401/403/404 fail over immediately.
  - Capability mismatches (`NotSupported`) skip to the next entry.
  - A genuine `InvalidRequest` stops the call at once.
- **Our own exception hierarchy.** Every SDK error is mapped into it, so product
  code never catches SDK exceptions.
- **Structured output is provider-agnostic.** The JSON schema goes in the system
  prompt, plus each provider's JSON mode where one exists. The reply is validated
  with pydantic, with one repair retry.
- **Cost comes from `models.yaml` prices.** Unconfirmed prices stay `null`, which
  gives `cost_usd = null` and `cost_unknown=true` rather than a guessed number.
- **One structured JSON log line per call**, plus an `on_call_complete` hook list
  that Phase 0.4 will use for LangSmith tracing.
- **`langchain_adapter.py`** provides `GatewayChatModel` (a `BaseChatModel`) and
  `GatewayEmbeddings`. They wrap `complete()` / `embed()`, so LangChain sits *on
  top of* the gateway, never inside it.
- **The folder is repurposed.** `platform/gateway` was first described as an HTTP API
  gateway; it is now the model gateway.

## Alternatives in more detail

**LangChain everywhere**
- For: large ecosystem; fallbacks and retries built in; easy to try ideas.
- Against:
  - Provider behaviour hides behind abstraction layers that change often across
    LangChain releases.
  - Error types differ per integration package, so a uniform
    "retry 429/5xx, never 400/401" policy is hard to guarantee.
  - Cost and logging would live in callbacks spread across products.
  - Strict mypy across many `langchain-*` packages is painful.
  - Six integration packages add to the dependency surface.

**LiteLLM**
- For: one call shape for 100+ providers; cost maps, fallbacks and a proxy server
  included.
- Against:
  - A large, fast-moving dependency sits in the critical path of every call.
  - Its cost tables are a third-party guess. Our rule is "confirmed price or null".
  - The proxy option adds a service to deploy and secure.
  - Less control over which errors are retried, what gets logged (PII) and how
    errors are typed.
  - New provider features usually reach official SDKs first.

## Trade-offs and consequences

- **We maintain six adapters**, each about 100–150 lines including error mapping.
  Each new provider means one more file and one more test module, using the recipe
  in `platform/gateway/README.md`.
- **Fewer features than LangChain or LiteLLM out of the box.** There's no streaming,
  tool calling, async or prompt caching yet. They will be added when a product
  needs them, behind the same interface.
- **Behaviour is explicit and testable.** Every routing and error path is unit-tested
  with fake providers and with each SDK's real exception classes, without network
  access.
- **Products can still use LangChain** through the adapter, and swapping LangChain
  out later doesn't touch provider code.
- **Prices need maintenance.** `test_unverified_prices` keeps unverified entries
  visible. Time-limited prices, such as the Gemini Flash price change on
  2027-01-01, are commented in `models.yaml`.
- **Embedding fallbacks must agree on dimensions.** `default-embed` fixes 1536
  dimensions across its chain so vectors stay comparable. Titan can't produce 1536,
  so it has its own alias (`titan-embed`, 1024). Mixing embedding models in one
  vector index is still a product-level decision.
- **Future work: an HTTP edge component.** Request routing to product services,
  CORS, rate limiting and correlation IDs were in the original `platform/gateway`
  description. They belong to a separate component (for example `platform/edge`),
  to be planned in its own phase. The model gateway stays an in-process library.
