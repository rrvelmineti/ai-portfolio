# core/gateway — model gateway

The one way products call AI models. Product code never imports a provider SDK;
it calls `complete()` or `embed()` with an **alias**, and the gateway picks the
provider, retries, fails over, works out the cost and logs the call.

Python package: `ai_platform_gateway` (in `src/`); the folder is `core/gateway`.

## Calling it

```python
from ai_platform_gateway import complete, embed, Message, ImagePart, TextPart

r = complete("Summarise this synthetic claim note: ...", model="default")
r.text, r.provider, r.model, r.cost_usd, r.request_id, r.fallback_used

# System prompt, limits, timeout and your own request id
r = complete(
    [Message(role="user", content="Classify: 'roof leak after storm'")],
    model="fast",
    system="Answer with one word.",
    max_tokens=16,
    timeout_s=10,
    request_id=incoming_request_id,
    metadata={"product": "claims-onboarding"},
)

# Structured output: validated with pydantic, one repair retry
from pydantic import BaseModel


class Claim(BaseModel):
    claim_type: str
    amount: float


r = complete("Extract the claim: ...", response_schema=Claim)
claim: Claim = r.parsed

# Images (base64; use the "vision" alias)
img = ImagePart.from_bytes(png_bytes, "image/png")
r = complete(
    [Message(role="user", content=[img, TextPart(text="What food is this?")])], model="vision"
)

# Embeddings
e = embed(["chunk one", "chunk two"])  # alias "default-embed", 1536 dims
e.vectors, e.dimensions, e.cost_usd
```

### Errors

| Exception | Meaning |
|---|---|
| `InvalidRequest` | Bad request or parameters (400/422), unknown alias. Raised immediately. |
| `NoProvidersConfigured` | No provider in the alias's chain has its env vars set. The message lists the variables to set. |
| `AllProvidersFailed` | Every configured provider failed. `.attempts` holds one `AttemptRecord` per entry (provider, model, outcome, error type, reason, attempts). |
| `SchemaValidationError` | Output failed `response_schema` validation after one retry. `.errors`, `.raw_text`. |
| `RateLimited`, `ProviderUnavailable`, `GatewayTimeout`, `AuthError`, `ModelNotFound`, `NotSupported` | Per-provider errors. You normally see them inside `AllProvidersFailed.attempts`. |

All of them subclass `GatewayError`.

### LangChain

```python
from ai_platform_gateway.langchain_adapter import GatewayChatModel, GatewayEmbeddings

llm = GatewayChatModel(alias="default")  # a LangChain BaseChatModel
emb = GatewayEmbeddings(alias="default-embed")  # a LangChain Embeddings
```

Both go through `complete()` / `embed()`, so routing, fallback, cost and logging
still apply. Tool calling, streaming and stop sequences aren't supported yet.

### Testing product code

```python
from ai_platform_gateway import set_gateway
from ai_platform_gateway.testing import FakeProvider, completion, fake_gateway

set_gateway(fake_gateway({"anthropic": FakeProvider("anthropic", [completion('{"ok": 1}')])}))
```

These fakes don't use the network, SDKs or keys.

## Aliases and fallback (`src/ai_platform_gateway/models.yaml`)

Routing is config, not code. The file has two sections:

- **`models`** is a catalog of concrete models. Each entry has `provider`, `model`
  id, `price_input_per_1m` / `price_output_per_1m` (USD), `context_limit`,
  `supports_temperature`, `supports_images`, and for embeddings
  `supported_dimensions`. A model id of `${ENV_VAR}` is read from the environment
  at call time (Azure deployment names).
- **`aliases`** map what products ask for to an ordered `fallback` list.

| Alias | Chain |
|---|---|
| `default` | Claude Sonnet 5.5 (Anthropic) → Claude Sonnet 5.5 (Bedrock) → GPT-4o |
| `best` | Claude Opus 5.5 (Anthropic) → Claude Opus 5.5 (Bedrock) |
| `fast` | Groq Llama 3.1 8B → Claude Haiku 5.5 → GPT-6 Luna |
| `vision` | Claude Sonnet 5.5 → GPT-4o → Gemini 3.8 Flash |
| `openai-mini` | GPT-6 Luna (OpenAI) → Azure OpenAI deployment |
| `claude-sonnet`, `gemini-flash`, `groq-llama` | Pinned to one model family |
| `default-embed` | text-embedding-3-small → Azure deployment → gemini-embedding-2 (all 1536-dim) |
| `titan-embed` | Titan Text Embeddings V2 (1024-dim) |

**How one call is handled:**

1. Entries whose provider is missing env vars are **skipped** with a warning log
   line. If none are configured, the gateway raises `NoProvidersConfigured`.
2. Capability checks come next. If the request has images and the entry has
   `supports_images: false`, or an embedding alias needs a size the entry can't
   produce, the entry is skipped. The same happens when an adapter raises
   `NotSupported` (for example `embed()` on Anthropic, or images on Groq).
3. Each remaining entry is tried with up to **3 attempts**, using exponential
   backoff with jitter (tenacity). Only these errors are retried: 429, 5xx,
   timeouts and connection errors.
4. After the retries are used up, the gateway **fails over** to the next entry.
   `401`/`403`/`404` fail over immediately without retrying.
5. A genuine `InvalidRequest` (400/422) is raised immediately, because another
   provider won't fix a bad request.
6. If the whole chain fails, the gateway raises `AllProvidersFailed` with every
   attempt and its reason.

`fallback_used` is true when the answer didn't come from the first entry in the
chain. SDK-level retries are disabled, so the gateway is the only layer that retries.
`timeout_s` applies to each attempt.

**Temperature:** if an entry has `supports_temperature: false` (current Claude
models), the adapter doesn't send `temperature` and writes a debug log line.

**Prices:** a price is only filled in from the provider's official pricing page.
An unconfirmed price is `null` with a `# verify:` comment. For those entries
`cost_usd` is `null` and the log line has `cost_unknown: true`. Run
`uv run pytest -k unverified -s` to list them.

## Configuration (env vars only)

| Provider | Variables |
|---|---|
| anthropic | `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| azure_openai | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` (chat), `AZURE_OPENAI_EMBED_DEPLOYMENT` (embeddings), optional `AZURE_OPENAI_API_VERSION` |
| bedrock | `AWS_REGION`, plus standard AWS credentials (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, web identity or container role) |
| gemini | `GEMINI_API_KEY` |
| groq | `GROQ_API_KEY` |
| all | `DEBUG_LOG_PROMPTS=true` also logs prompt and response text (off by default) |

## Logging and hooks

Every call writes **one** JSON log line, `gateway.call`, with these fields:

- `request_id`, `operation`, `alias`, `provider`, `model`
- `attempts`, `fallback_used`, `input_tokens`, `output_tokens`, `cost_usd`, `cost_unknown`
- `latency_ms`, `status`, `error_type`
- `chain`: per-entry outcome and a gateway-written reason

Prompts, responses, metadata values and keys are never logged by default. Reasons
are written by the gateway, never copied from provider error text.

`ai_platform_gateway.on_call_complete` is a list of callbacks. Each one receives a
`CallRecord` after every call, and an exception raised in a hook can't fail the
call. Phase 0.4 will register LangSmith tracing here.

## Adding a provider

1. Add `src/ai_platform_gateway/providers/<name>.py` with a class that subclasses
   `BaseProvider` and has:
   - `name` and `required_env`;
   - `complete(CompletionRequest) -> ProviderCompletion`;
   - optionally `embed(EmbeddingRequest) -> ProviderEmbedding`. The default raises
     `NotSupported`.
2. Build the SDK client lazily, with **SDK retries off**, and pass `timeout_s` through.
3. Map **every** SDK exception to a gateway error. `error_from_status()`,
   `map_stainless_error()` and `map_transport_error()` cover most SDKs.
4. Register it in `providers/registry.py` (`_BUILTIN`).
5. Add catalog entries and aliases in `models.yaml`. Use only confirmed prices,
   otherwise `null` with `# verify:`.
6. Add `tests/test_providers_<name>.py`. Use a mocked client and the SDK's own
   exception classes, and test the mapping for 400/401/403/404/429/5xx, timeouts
   and connection errors.
7. Add the env vars to `.env.example` and to the table above.

## Smoke test (live, not in CI)

```bash
set -a; source .env; set +a
uv run python scripts/smoke_gateway.py      # table: provider, model, ok/fail, latency, cost
uv run pytest -m live                       # live pytest tests (deselected by default)
```
