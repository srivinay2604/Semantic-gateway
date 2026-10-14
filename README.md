# CacheGate: Production LLM Gateway with Semantic Caching

[![CI](https://github.com/cachegate/cachegate/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Coverage: 87%](https://img.shields.io/badge/coverage-87%25-brightgreen.svg)](docs/benchmarks.md)

An OpenAI-compatible LLM gateway with **exact + semantic caching**, multi-provider resilience, atomic rate limiting, and full observability — deployed on **AWS ECS Fargate**.

Drop it into any application by changing a single line of code:
```python
from openai import OpenAI

# Change base_url to point to CacheGate
client = OpenAI(base_url="http://localhost:8000/v1", api_key="cg-secret-token")
```

---

## The Problem

Every team building AI products hits the same operational bottlenecks:
1. **High Latency:** LLM API calls take 800 ms to 5,000+ ms per turn.
2. **Exponential Costs:** LLMs bill per token; redundant prompts (support queries, common FAQs) repeatedly burn budget.
3. **Provider Outages & Rate Limits:** Cloud LLMs fail, throttle with 429s, or suffer major latency spikes.

**CacheGate** sits between client applications and LLMs, adding two-tier caching, automatic failover, single-flight stampede protection, and cost tracking.

---

## Architecture

```mermaid
flowchart TB
    Client["Client Applications<br/>(OpenAI SDK, LangChain, curl)"] -->|HTTPS| ALB["AWS Application Load Balancer"]
    
    subgraph ECS["ECS Fargate Tasks"]
        direction TB
        MW["Middleware Stack<br/>RequestId → SizeLimit → Metrics → Auth → RateLimit"]
        CHAT["/v1/chat/completions"]
        
        subgraph Caching["Two-Tier Cache Manager"]
            EXACT["Exact Match Cache<br/>(Normalized SHA-256)"]
            SEM["Semantic Vector Cache<br/>(all-MiniLM-L6-v2 + HNSW KNN)"]
        end
        
        SF["Single-Flight Coalescer<br/>(Asyncio Futures + Redis Distributed Lock)"]
        
        subgraph Routing["Resilience & Multi-Provider Router"]
            RETRY["Jittered Exponential Backoff"]
            CB["Three-State Circuit Breaker"]
            FALLBACK["Fallback Chain (OpenAI → Anthropic)"]
        end
        
        USAGE["Usage & Cost Tracker<br/>(Tokens, Incurred Cost, Cost Saved)"]
    end
    
    ALB --> MW
    MW --> CHAT
    CHAT --> Caching
    Caching -->|Exact Hit (<1ms)| CHAT
    Caching -->|Semantic Hit (<15ms)| CHAT
    Caching -->|Cold Cache Miss| SF
    SF --> Routing
    Routing --> USAGE
    
    Caching <--> REDIS[("Redis Stack<br/>KV + Vector Index + Lua Limiter")]
    SF <--> REDIS
    MW <--> REDIS
    
    Routing -->|Primary| OAI["OpenAI API"]
    Routing -->|Fallback| ANT["Anthropic Claude API"]
    Routing -->|Dev / Test| MOCK["Mock Provider"]
    
    ECS -.metrics.-> PROM["Prometheus"]
    PROM --> GRAF["Grafana Dashboard"]
    ECS -.logs.-> CW["CloudWatch Logs"]
```

---

## Measured Performance & Benchmarks

Full benchmark methodology and threshold curves in [`docs/benchmarks.md`](docs/benchmarks.md):

| Metric | Baseline (No Cache) | With CacheGate | Measured Improvement |
|---|---|---|---|
| **p50 Latency** | 67.3 ms | **43.0 ms** | **36.1% reduction** |
| **p95 Latency** | 92.4 ms | **100.9 ms** | Bounded tail latency via local embedder |
| **Cache Hit Rate** (Exact + Semantic) | 0.0% | **90.0%** | **90.0% traffic offloaded from LLMs** |
| **Cost per 1K Requests** | $0.30 | **$0.03** | **90.0% cost saved** |
| **Throughput (req/s at p95 target)** | 35 req/s | **182 req/s** | **5.2x higher throughput** |
| **False-Hit Rate on Hard Negatives** | n/a | **0.00%** | Eliminated via calibrated threshold (`0.92`) |
| **Cold Stampede Calls (200 identical)** | 200 calls | **1 call** | **99.5% reduction** (Single-Flight) |

---

## Quickstart (3 Commands)

### 1. Launch with Docker Compose
```bash
git clone https://github.com/your-org/cachegate && cd cachegate
make up
```
Starts:
- **CacheGate Gateway** on `http://localhost:8000`
- **Redis Stack** (with RediSearch vector indexing) on port `6379`
- **Prometheus** on `http://localhost:9090`
- **Grafana** (pre-loaded dashboard) on `http://localhost:3000`

### 2. Verify Health
```bash
curl http://localhost:8000/readyz
```

### 3. Send Your First Request
```bash
curl -i -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mock-fast",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "How do I reset my password?"}
    ],
    "temperature": 0.0
  }'
```
First request returns `X-Cache: MISS`. Repeating the call (or a paraphrase like *"What are the steps to recover my forgotten password?"*) returns `X-Cache: HIT-EXACT` or `X-Cache: HIT-SEMANTIC` in milliseconds!

---

## Architectural Design Tradeoffs

1. **Redis Stack vs Dedicated Vector Databases (Pinecone/Weaviate/pgvector):**
   - *Decision:* Redis Stack handles exact key-value caching, HNSW vector search, atomic token-bucket rate limiting via Lua, and single-flight distributed locks in a single in-memory engine.
   - *Benefit:* Drastically simplifies infrastructure and eliminates multi-hop network overhead.
2. **In-Process Embeddings (`all-MiniLM-L6-v2`) vs External Embedding APIs:**
   - *Decision:* Embed prompts locally in an async worker thread pool.
   - *Benefit:* Avoids external network latency (100–250ms per OpenAI embedding API call). Local inference completes in 5–15ms.
3. **Atomic Lua Token Bucket vs Sliding Window:**
   - *Decision:* Custom Lua script executing atomically on Redis.
   - *Benefit:* $O(1)$ memory consumption per key, zero race conditions across distributed workers, and strict burst tolerance.
4. **Strict Fail-Open Resilience:**
   - *Decision:* If Redis or the local embedder crashes, the gateway logs a warning, increments `cg_cache_errors_total`, and seamlessly forwards the request directly to the upstream LLM.
   - *Benefit:* Caching is an acceleration layer; an auxiliary cache failure never takes down core customer traffic.
5. **Two-Tier Single-Flight Coalescing:**
   - *Decision:* Combines in-process `asyncio.Future` maps with short-lived Redis distributed locks (`cg:v1:lock:{sha256}`).
   - *Benefit:* Resolves the classic cache stampede problem, reducing upstream calls from 200 to 1 on cold spikes.

---

## Limitations & Future Work

- **Streaming Caching:** Responses streamed via SSE are cached upon completion. Chunk-level speculative prefix caching is slated for a future release.
- **GPU Embeddings Cluster:** Local CPU embeddings are ideal up to ~500 req/s. Higher throughput targets can decouple embeddings to a dedicated Triton GPU cluster.
- **Multi-Modal Data:** Currently supports text completions. Image/audio embeddings are not yet cached.

---

## Documentation Links

- [System Architecture Specification](docs/architecture.md)
- [Benchmark & Evaluation Report](docs/benchmarks.md)
- [Architecture Decision Records (ADR-001 to ADR-008)](docs/decisions.md)
- [Operations & Disaster Recovery Runbook](docs/runbook.md)
- [Engineering Deep Dive & Interview Guide](docs/interview_prep.md)
- [Terraform AWS Infrastructure Stack](infra/terraform/)

---

## Project Status & Phases

| Phase | Description | Status |
|---|---|---|
| **Phase 0 & 1** | Scaffold, Docker, CI, and OpenAI Core Gateway | ✅ Complete |
| **Phase 2** | Real Providers (OpenAI/Anthropic) & SSE Streaming | ✅ Complete |
| **Phase 3** | Resilience: Retries with Jitter, Circuit Breaker & Fallbacks | ✅ Complete |
| **Phase 4** | Exact Match Cache & Redis Store | ✅ Complete |
| **Phase 5** | Semantic Vector Cache, KNN Search & Partition Isolation | ✅ Complete |
| **Phase 6** | API Key Auth, Admin Endpoints & Lua Token Bucket Limiter | ✅ Complete |
| **Phase 7** | Single-Flight Stampede Protection & Fail-Open Hardening | ✅ Complete |
| **Phase 8** | Observability: Prometheus Metrics & Grafana Dashboard | ✅ Complete |
| **Phase 9** | Benchmarks: Threshold Tuning, Locust & Load Testing | ✅ Complete |
| **Phase 10** | AWS Deployment: Terraform ECS Fargate, ALB & OIDC CI/CD | ✅ Complete |
| **Phase 11** | Documentation Polish & Engineering Interview Guide | ✅ Complete |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
