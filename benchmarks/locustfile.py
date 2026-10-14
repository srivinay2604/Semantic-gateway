"""Locust load test suite for CacheGate (Phase 9).

Workload Scenarios:
- Scenario A: Baseline (no cache)
- Scenario B: Cache on with Zipf-distributed prompts and paraphrase variants
- Scenario C: Cold-start stampede (identical prompt bursts)
- Scenario D: Provider failure handling
- Scenario E: Rate limit throttling
"""

from __future__ import annotations

import random
from locust import HttpUser, between, task

PROMPT_POOL = [
    # Top frequent queries (Zipf head ~ 70% traffic)
    "How do I reset my account password?",
    "What are the steps to recover my forgotten password?",
    "Where can I download my billing invoice?",
    "How do I enable two-factor authentication (2FA)?",
    # Medium frequency (~20% traffic)
    "What payment methods do you accept?",
    "Can I invite external collaborators as guests?",
    "How do I configure custom domain SSL certificates?",
    # Long tail (~10% traffic)
    "Explain quantum computing in three sentences.",
    "Write a Python function to compute Fibonacci numbers.",
    "What is the difference between TCP and UDP sockets?",
]


class CacheGateUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self) -> None:
        self.client.headers.update({"Authorization": "Bearer test-key"})

    @task(7)
    def test_popular_chat_completion(self) -> None:
        """High-frequency prompt hitting exact or semantic cache."""
        prompt = random.choice(PROMPT_POOL[:4])
        self.client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [
                    {"role": "system", "content": "You are a customer support AI."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
            },
        )

    @task(2)
    def test_medium_chat_completion(self) -> None:
        """Medium-frequency prompt."""
        prompt = random.choice(PROMPT_POOL[4:7])
        self.client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            },
        )

    @task(1)
    def test_long_tail_chat_completion(self) -> None:
        """Long-tail prompt (cache misses)."""
        prompt = random.choice(PROMPT_POOL[7:])
        self.client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            },
        )
