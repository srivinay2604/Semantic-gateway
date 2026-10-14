"""Placeholder — Phase 6 fills this in.

Admin endpoints:
  POST   /admin/keys       — create an API key
  GET    /admin/keys       — list keys (never return plaintext)
  DELETE /admin/keys/{id}  — revoke a key
  DELETE /admin/cache      — flush cache by tenant/model/all
  GET    /admin/stats      — hit rate, savings, provider health
"""

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


# Phase 6: implement admin endpoints
