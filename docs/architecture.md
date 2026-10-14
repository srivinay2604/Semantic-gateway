# CacheGate System Architecture

CacheGate is a production-grade, OpenAI-compatible LLM gateway designed for high throughput, low latency, and cost efficiency. It adds two-tier caching (exact + semantic), resilient multi-provider routing with circuit breakers, token-bucket rate limiting, and observability to any LLM infrastructure.

---

## 1. High-Level Architecture

```mermaid
flowchart TB
    Client["Client / SDK / App"] -->|HTTP / HTTPS| ALB["AWS Application Load Balancer"]
    
    subgraph ECS["ECS Fargate Tasks"]
        direction TB
        MW["Middleware Stack<br/>RequestId → SizeLimit → Metrics → Auth → RateLimit"]
        CHAT["/v1/chat/completions Handler"]
        
        subgraph Caching["Two-Tier Cache Manager"]
            EXACT["Exact Match Cache<br/>(SHA-256 Request Hash)"]
            SEM["Semantic Vector Cache<br/>(Dense Embeddings + KNN)"]
        end
        
        SF["Single-Flight Coalescer<br/>(In-Process Futures + Redis Locks)"]
        
        subgraph Routing["Provider Router & Resilience"]
            RETRY["Exponential Backoff with Full Jitter"]
            CB["Circuit Breaker (Closed / Half-Open / Open)"]
            FALLBACK["Multi-Provider Fallback Chain"]
        end
        
        USAGE["Usage & Cost Tracker<br/>(Token Accounting & Savings)"]
    end
    
    ALB --> MW
    MW --> CHAT
    CHAT --> Caching
    Caching -->|Exact Hit| CHAT
    Caching -->|Semantic Hit| CHAT
    Caching -->|Cold Miss| SF
    SF --> Routing
    Routing --> USAGE
    
    Caching <--> REDIS[("Redis Stack<br/>Key-Value + HNSW Vector Index")]
    SF <--> REDIS
    MW <--> REDIS
    
    Routing -->|Primary| OPENAI["OpenAI API"]
    Routing -->|Secondary| ANTHROPIC["Anthropic Claude API"]
    Routing -->|Dev / Test| MOCK["Mock Provider"]
```

---

## 2. Request Lifecycle & Execution Pipeline

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant GW as Gateway Middleware
    participant CM as Cache Manager
    participant SF as Single Flight
    participant Router as Provider Router
    participant LLM as Upstream LLM (OpenAI/Anthropic)
    participant Redis as Redis Stack

    Client->>GW: POST /v1/chat/completions (Bearer Key)
    GW->>GW: Validate Auth, Rate Limit (Lua Token Bucket), Size Limit
    GW->>CM: Lookup Exact Cache (cg:v1:exact:sha256)
    CM->>Redis: GET key
    alt Exact Cache Hit
        Redis-->>CM: Cached Response JSON
        CM-->>Client: 200 OK (X-Cache: HIT-EXACT)
    else Exact Cache Miss
        CM->>CM: Generate Dense Vector & Filter Tags
        CM->>Redis: FT.SEARCH idx:cg:v1:sem KNN 3 (cosine similarity)
        alt Semantic Cache Hit (Similarity >= 0.92)
            Redis-->>CM: Top Match (similarity >= 0.92)
            CM-->>Client: 200 OK (X-Cache: HIT-SEMANTIC)
        else Cold Cache Miss
            CM->>SF: Coordinate In-Flight Requests
            SF->>SF: Attach concurrent identical requests to shared Future
            alt Leader Request
                SF->>Router: Execute with Retries & Circuit Breaker
                Router->>LLM: Upstream POST /chat/completions
                LLM-->>Router: Model Output + Tokens
                Router-->>SF: InternalResponse
                SF->>CM: Store in Exact & Semantic Caches
                CM->>Redis: SET exact & HSET vector
                SF-->>Client: 200 OK (X-Cache: MISS)
            else Follower Request (Coalesced)
                SF-->>Client: 200 OK (X-Cache: HIT-EXACT)
            end
        end
    end
```

---

## 3. Core Subsystems

### 3.1 Two-Tier Caching Layer
1. **Tier 1 — Exact Match (`exact_store`):**
   - Normalizes messages (stripping trailing whitespace, lowercasing roles, normalizing JSON parameters).
   - Computes deterministic SHA-256 hash.
   - Key: `cg:v1:exact:{tenant_id}:{sha256}` with configurable TTL.
   - Lookup latency: **< 1 ms**.
2. **Tier 2 — Semantic Vector Match (`vector_store`):**
   - Embeds the final user prompt using dense sentence embeddings (`sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions).
   - Partitioned by 5 metadata filter tags: `tenant`, `model`, `sys_hash`, `params_hash`, `ctx_hash`.
   - Executes RediSearch KNN search with cosine similarity.
   - Bounded by threshold `0.92` to eliminate false positives on hard negatives.

### 3.2 Single-Flight Request Coalescing
- When a traffic spike sends 200 identical requests simultaneously on a cold cache, **SingleFlight** ensures only one leader request calls the upstream LLM provider.
- In-process coroutines attach to a shared `asyncio.Future`.
- Cross-process instances coordinate using short-lived distributed Redis locks (`cg:v1:lock:{sha256}`).
- Upstream LLM provider calls are reduced by **99.5%** on stampedes.

### 3.3 Resilience & Provider Fallback
- **Exponential Backoff with Full Jitter:** Prevents retry synchrony across instances.
- **Circuit Breaker:** Tracks consecutive failures per provider. Trips from `CLOSED` to `OPEN` on 5 consecutive failures, failing fast in < 1ms. Automatically moves to `HALF_OPEN` after a 30s recovery cooldown.
- **Fallback Chains:** Seamlessly falls back from `gpt-4o` to `claude-3-5-sonnet` to fallback models when primary providers experience outages.

### 3.4 Rate Limiting & Usage Tracking
- Atomic token-bucket rate limiting implemented via Redis Lua script with monotonic millisecond timestamps.
- Usage tracking records prompt tokens, completion tokens, estimated cost incurred, and **estimated cost saved** by cache hits.
