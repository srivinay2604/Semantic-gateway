# CacheGate

> **Status:** Phase 1 in progress 🚧

An OpenAI-compatible LLM gateway with exact + semantic caching, multi-provider fallback, per-key rate limiting, and full observability — deployed on AWS.

---

## Problem Statement

Every team building AI products hits the same operational wall: LLM API calls are slow (1–10 s), expensive (billed per token), and providers rate-limit and fail. Meanwhile, many user prompts are repeated or near-duplicates. CacheGate sits between your application and any LLM provider, adding two-tier caching (exact match + semantic similarity), automatic fallback, rate limiting, and a Prometheus/Grafana observability stack — all behind a single OpenAI-compatible endpoint. Drop it in by changing one line: `base_url="http://your-gateway/v1"`.

---

## Architecture

```mermaid
flowchart LR
    Client["Client apps<br/>(OpenAI SDK, curl, Locust)"] -->|HTTPS| ALB["AWS ALB"]
    ALB --> GW1["Gateway task 1<br/>(FastAPI)"]
    ALB --> GW2["Gateway task N<br/>(FastAPI)"]

    subgraph Gateway["Gateway internals"]
        direction TB
        MW["Middleware<br/>request-id, auth, rate limit, metrics"]
        ROUTER["Chat completions router"]
        CACHE["Cache manager"]
        EMB["Embedder"]
        PROV["Provider router<br/>retry, circuit breaker, fallback"]
        COST["Cost / usage tracker"]
        MW --> ROUTER --> CACHE
        CACHE --> EMB
        CACHE -->|miss| PROV
        PROV --> COST
    end

    GW1 --- Gateway
    CACHE <--> REDIS[("Redis Stack<br/>exact cache + vector index<br/>+ rate limits + usage")]
    PROV --> OAI["OpenAI"]
    PROV --> ANT["Anthropic"]
    PROV --> MOCK["Mock / Ollama"]

    GW1 -.metrics.-> PROM["Prometheus"]
    PROM --> GRAF["Grafana"]
    GW1 -.logs.-> CW["CloudWatch Logs"]
```

---

## Quickstart

```bash
# 1. Clone and enter the project
git clone <repo-url> && cd cachegate

# 2. Start all services (gateway + Redis Stack + Prometheus + Grafana)
make up

# 3. Test the gateway
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-fast","messages":[{"role":"user","content":"Hello!"}]}'
```

> **Phase 2+:** Replace `mock-fast` with `gpt-4o-mini` after configuring `OPENAI_API_KEY`.

---

## Configuration

See [`.env.example`](.env.example) for all environment variables. Key settings:

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `MOCK_LATENCY_MS` | `800` | Mock provider artificial latency |
| `CACHE_ENABLED` | `true` | Enable/disable caching |
| `SEMANTIC_THRESHOLD` | `0.92` | Min cosine similarity for semantic hit |
| `RATE_LIMIT_DEFAULT_RPS` | `5` | Token bucket refill rate |

---

## Benchmark Results

> 🚧 **To be filled in Phase 9** — results will show p50/p95 latency, cache hit rate, and cost reduction vs baseline.

| Metric | Baseline (no cache) | With CacheGate | Improvement |
|---|---|---|---|
| p50 latency | measured | measured | measured |
| p95 latency | measured | measured | measured |
| Cache hit rate | 0% | measured | n/a |
| Cost per 1K requests | measured | measured | measured |

---

## Development

```bash
pip install -e ".[dev]"
make test        # run all tests with coverage
make lint        # ruff check
make typecheck   # mypy
make format      # ruff format
```

---

## Project Phases

| Phase | Status | Description |
|---|---|---|
| 0 — Scaffold | ✅ Done | Repo skeleton, Docker, CI |
| 1 — Core gateway | ✅ Done | MockProvider, schemas, middleware |
| 2 — Real providers + streaming | 🔜 Next | OpenAI, Anthropic, SSE |
| 3 — Resilience | 🔜 | Retries, circuit breaker, fallback |
| 4 — Exact cache | 🔜 | SHA-256 key, Redis TTL |
| 5 — Semantic cache | 🔜 | Embeddings, RediSearch KNN |
| 6 — Auth + rate limiting | 🔜 | Token bucket, API keys |
| 7 — Single-flight + hardening | 🔜 | Coalescing, fail-open |
| 8 — Observability | 🔜 | Grafana dashboard |
| 9 — Benchmarks | 🔜 | Locust load tests, threshold tuning |
| 10 — AWS deployment | 🔜 | ECS Fargate, Terraform |
| 11 — Docs polish | 🔜 | Runbook, demo GIF |

---

## License

MIT — see [LICENSE](LICENSE).
