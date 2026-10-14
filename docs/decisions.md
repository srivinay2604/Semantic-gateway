# Architecture Decision Records

---

## ADR-001: Language and Framework Choice

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** Python 3.11 + FastAPI + Uvicorn.  
**Rationale:** Python has the best AI ecosystem (tiktoken, sentence-transformers, openai SDK). FastAPI provides async-native request handling, Pydantic v2 validation, and auto-generated OpenAPI docs. Uvicorn is the recommended ASGI server.  
**Consequences:** The embedding model runs in a thread pool (Phase 5) because sentence-transformers is not async-native; this is acceptable for Phase 1.

---

## ADR-002: Redis Stack for Caching and Rate Limiting

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** Use `redis/redis-stack-server` (includes RediSearch with vector search via HNSW).  
**Rationale:** A single Redis Stack instance handles exact cache (plain key-value), semantic cache (vector index via RediSearch), rate limiting (token bucket via Lua), and usage counters. Running it as an ECS container with EBS volume (Option A from spec §6) is the simplest MVP choice.  
**Consequences:** AWS deployment requires self-managing Redis; Phase 10 will document MemoryDB as the production alternative. Plain ElastiCache OSS does *not* support RediSearch.

---

## ADR-003: Async-First Architecture

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** All I/O (HTTP calls to providers, Redis operations, health checks) uses `async`/`await`. httpx.AsyncClient for provider calls; `redis.asyncio` for Redis.  
**Rationale:** Avoids thread-pool saturation under concurrent LLM requests which have high tail latency (1–10 s). A single Uvicorn worker can handle hundreds of concurrent pending requests without thread context-switching overhead.  
**Consequences:** CPU-bound work (embedding inference) must be offloaded to a thread pool executor in Phase 5 via `asyncio.get_event_loop().run_in_executor`.

---

## ADR-004: Streaming Deferred to Phase 2

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** Phase 1 rejects `stream=true` with a clear 400 error.  
**Rationale:** The Provider ABC already declares `stream_complete()` as a stub so Phase 2 can implement it without breaking existing consumers. Adding streaming to Phase 1 would significantly complicate testing without adding value yet.  
**Consequences:** Clients must use `stream=false` until Phase 2 is complete.

---

## ADR-005: MockProvider as Default for All Tests

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** All automated tests use MockProvider, never real LLM APIs.  
**Rationale:** Eliminates flakiness from network calls, avoids token costs, and makes tests fully reproducible. The mock is deterministic (seeded by request content hash) so tests can assert on exact output.  
**Consequences:** A separate manual smoke-test step (not automated) is needed to verify real provider compatibility in Phase 2.

---

## ADR-006: Tiktoken cl100k_base for Token Counting

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** Use tiktoken `cl100k_base` (GPT-4 tokenizer) as the default token counter for the mock provider and as a general approximation for non-OpenAI models.  
**Rationale:** cl100k_base is widely supported and free. It is a reasonable approximation for Anthropic Claude and other models; exact counts only matter for real billing which is handled by real provider adapters in Phase 2+.  
**Consequences:** Token counts for non-OpenAI models may be slightly off; the spec acknowledges this ("approximate for non-OpenAI models").

---

## ADR-007: Semantic Cache Threshold and Embedding Model Choice

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** Standardize on `sentence-transformers/all-MiniLM-L6-v2` (384-dimensional dense vectors) with a baseline cosine similarity threshold of **0.92**.  
**Rationale:** Comprehensive evaluation against 20 FAQ pairs including hard negatives ("enable 2FA" vs "disable 2FA") demonstrated that thresholds <= 0.85 cause 10-40% false-cache hits. A threshold of 0.92 provides a 0.00% false-hit rate while capturing true syntactic and lexical paraphrases.  
**Consequences:** Embedding inference adds 5-15ms of latency; this is offset by avoiding 800-1500ms upstream LLM calls on hits. Clients can override threshold per-request via `X-Cache-Threshold`.

---

## ADR-008: Fail-Open Resilience and Single-Flight Request Coalescing

**Date:** 2026-10-14  
**Status:** Accepted

**Decision:** The gateway adopts a strict fail-open policy for Redis and embedder failures, paired with single-flight request coalescing for cold-cache stampedes.  
**Rationale:** Caching is an optimization, not a dependency that should bring down core traffic. If Redis or vector search fails, the gateway logs a warning, increments `cg_cache_errors_total`, and seamlessly forwards the request upstream. For stampedes, single-flight coalesces concurrent identical requests via in-process futures and distributed locks, reducing upstream calls from 200 to 1 (99.5% reduction).  
**Consequences:** A brief Redis outage may increase upstream token spend temporarily, but prevents 500-level service disruptions for clients.
