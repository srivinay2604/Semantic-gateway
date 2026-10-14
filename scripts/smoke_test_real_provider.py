#!/usr/bin/env python3
"""Manual smoke test script for real providers (OpenAI / Anthropic).

Acceptance criterion for Phase 2:
Run this script with real API keys to verify end-to-end integration with upstream APIs.

Usage:
    export OPENAI_API_KEY="sk-..."
    python scripts/smoke_test_real_provider.py --provider openai

    export ANTHROPIC_API_KEY="sk-ant-..."
    python scripts/smoke_test_real_provider.py --provider anthropic
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure src/ is on sys.path when running script directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cachegate.providers.anthropic import AnthropicProvider
from cachegate.providers.openai import OpenAIProvider
from cachegate.schemas.internal import InternalMessage, InternalRequest


async def smoke_test_openai() -> bool:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY is not set. Skipping OpenAI real smoke test.")
        return False

    print("\n--- Testing OpenAI Provider (gpt-4o-mini) ---")
    provider = OpenAIProvider(model="gpt-4o-mini", api_key=api_key)

    req = InternalRequest(
        request_id="smoke-openai-1",
        model_alias="gpt-4o-mini",
        resolved_provider="openai/gpt-4o-mini",
        messages=[
            InternalMessage(
                role="user",
                content="Say 'CacheGate OpenAI smoke test passed' in exactly 6 words.",
            )
        ],
        max_tokens=50,
    )

    print("[1/2] Testing non-streaming completion...")
    resp = await provider.complete(req)
    print(f"  Response: {resp.content.strip()}")
    print(f"  Tokens: prompt={resp.usage.prompt_tokens}, completion={resp.usage.completion_tokens}")
    print(f"  Latency: {resp.provider_latency_ms:.1f}ms")

    print("[2/2] Testing SSE streaming completion...")
    stream_tokens = []
    async for chunk in provider.stream_complete(req):
        if chunk != "[DONE]":
            stream_tokens.append(chunk)
    print(f"  Received {len(stream_tokens)} raw SSE chunks followed by [DONE].")
    print("  [OK] OpenAI smoke test passed!")
    return True


async def smoke_test_anthropic() -> bool:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[WARN] ANTHROPIC_API_KEY is not set. Skipping Anthropic real smoke test.")
        return False

    print("\n--- Testing Anthropic Provider (claude-3-haiku-20240307) ---")
    provider = AnthropicProvider(model="claude-3-haiku-20240307", api_key=api_key)

    req = InternalRequest(
        request_id="smoke-anthropic-1",
        model_alias="claude-haiku",
        resolved_provider="anthropic/claude-3-haiku-20240307",
        messages=[
            InternalMessage(
                role="user",
                content="Say 'CacheGate Anthropic smoke test passed' in exactly 6 words.",
            )
        ],
        max_tokens=50,
    )

    print("[1/2] Testing non-streaming completion...")
    resp = await provider.complete(req)
    print(f"  Response: {resp.content.strip()}")
    print(f"  Tokens: prompt={resp.usage.prompt_tokens}, completion={resp.usage.completion_tokens}")
    print(f"  Latency: {resp.provider_latency_ms:.1f}ms")

    print("[2/2] Testing SSE streaming completion...")
    stream_tokens = []
    async for chunk in provider.stream_complete(req):
        if chunk != "[DONE]":
            stream_tokens.append(chunk)
    print(f"  Received {len(stream_tokens)} translated OpenAI chunks followed by [DONE].")
    print("  [OK] Anthropic smoke test passed!")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test real LLM providers")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "both"],
        default="both",
        help="Provider to test (default: both)",
    )
    args = parser.parse_args()

    executed = 0
    if args.provider in ("openai", "both"):
        if await smoke_test_openai():
            executed += 1
    if args.provider in ("anthropic", "both"):
        if await smoke_test_anthropic():
            executed += 1

    if executed == 0:
        print("\nNo real API keys were provided in the environment.")
        print("    OPENAI_API_KEY=sk-... \\")
        print("      python scripts/smoke_test_real_provider.py --provider openai")
        print("    ANTHROPIC_API_KEY=sk-ant-... \\")
        print("      python scripts/smoke_test_real_provider.py --provider anthropic")


if __name__ == "__main__":
    asyncio.run(main())
