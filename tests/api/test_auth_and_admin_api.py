"""API tests for Authentication, Admin endpoints, Rate Limiting, and Daily Budgets."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from cachegate.main import create_app
from cachegate.settings import settings

ADMIN_HEADERS = {"Authorization": f"Bearer {settings.admin_api_key}"}


class TestAuthAndAdminAPI:
    @pytest.mark.asyncio
    async def test_admin_requires_authentication(self) -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # Missing auth header -> 401
                resp = await client.get("/admin/keys")
                assert resp.status_code == 401

                # Invalid auth header -> 401
                resp = await client.get("/admin/keys", headers={"Authorization": "Bearer bad-key"})
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_key_lifecycle_and_client_auth(self) -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # 1. Create a key as admin
                create_payload = {
                    "name": "Production Customer A",
                    "tenant_id": "customer_a",
                    "rate_limit_rps": 10.0,
                    "rate_limit_burst": 20,
                }
                res = await client.post("/admin/keys", json=create_payload, headers=ADMIN_HEADERS)
                assert res.status_code == 200
                data = res.json()
                client_token = data["key"]
                key_id = data["id"]
                assert client_token.startswith("cg-")

                # 2. List keys as admin (secret token is not revealed)
                list_res = await client.get("/admin/keys", headers=ADMIN_HEADERS)
                assert list_res.status_code == 200
                keys_list = list_res.json()
                assert len(keys_list) >= 1
                assert "key" not in keys_list[0]
                assert keys_list[0]["id"] == key_id

                # 3. Call /v1/chat/completions using client token -> 200 OK
                body = {
                    "model": "mock-fast",
                    "messages": [{"role": "user", "content": "Hello authenticated gateway!"}],
                }
                chat_res = await client.post(
                    "/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {client_token}"},
                )
                assert chat_res.status_code == 200
                assert "x-ratelimit-remaining" in chat_res.headers

                # 4. Revoke key
                del_res = await client.delete(f"/admin/keys/{key_id}", headers=ADMIN_HEADERS)
                assert del_res.status_code == 200

                # 5. Calling with revoked key -> 401
                revoked_res = await client.post(
                    "/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {client_token}"},
                )
                assert revoked_res.status_code == 401

    @pytest.mark.asyncio
    async def test_rate_limiting_enforcement(self) -> None:
        """Exceeding burst capacity triggers HTTP 429 with Retry-After header."""
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # Create key with tiny burst of 2 requests
                create_payload = {
                    "name": "Throttled Key",
                    "rate_limit_rps": 1.0,
                    "rate_limit_burst": 2,
                }
                res = await client.post("/admin/keys", json=create_payload, headers=ADMIN_HEADERS)
                token = res.json()["key"]
                headers = {"Authorization": f"Bearer {token}"}
                body = {
                    "model": "mock-fast",
                    "messages": [{"role": "user", "content": "Rate limit test"}],
                }

                # First 2 requests succeed
                r1 = await client.post("/v1/chat/completions", json=body, headers=headers)
                assert r1.status_code == 200
                r2 = await client.post("/v1/chat/completions", json=body, headers=headers)
                assert r2.status_code == 200

                # 3rd request is throttled
                r3 = await client.post("/v1/chat/completions", json=body, headers=headers)
                assert r3.status_code == 429
                assert "retry-after" in r3.headers
                assert r3.json()["error"]["code"] == "rate_limit_exceeded"

    @pytest.mark.asyncio
    async def test_daily_budget_enforcement(self) -> None:
        """Exceeding daily budget limits triggers HTTP 429 budget exceeded."""
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # Key with $0.000001 budget
                create_payload = {
                    "name": "Budget Constrained Key",
                    "daily_budget_usd": 0.000001,
                }
                res = await client.post("/admin/keys", json=create_payload, headers=ADMIN_HEADERS)
                token = res.json()["key"]
                key_id = res.json()["id"]

                # Artificially record existing cost
                await app.state.usage_tracker.record_request(
                    key_id=key_id,
                    model="gpt-4o",
                    prompt_tokens=10000,
                    completion_tokens=10000,
                    is_hit=False,
                )

                # Subsequent call exceeds budget
                headers = {"Authorization": f"Bearer {token}"}
                body = {
                    "model": "mock-fast",
                    "messages": [{"role": "user", "content": "Budget test"}],
                }
                resp = await client.post("/v1/chat/completions", json=body, headers=headers)
                assert resp.status_code == 429
                assert "budget" in resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_admin_cache_flush_and_stats(self) -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # Flush cache
                flush_res = await client.delete(
                    "/admin/cache?all_entries=true", headers=ADMIN_HEADERS
                )
                assert flush_res.status_code == 200

                # Get stats
                stats_res = await client.get("/admin/stats", headers=ADMIN_HEADERS)
                assert stats_res.status_code == 200
                assert "total_requests" in stats_res.json()
