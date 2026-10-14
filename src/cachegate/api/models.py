"""GET /v1/models — list configured model aliases.

Returns an OpenAI-compatible model list response so that SDK clients can
discover which model aliases the gateway supports.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from cachegate.schemas.openai import ModelObject, ModelsListResponse

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", response_model=ModelsListResponse, summary="List available models")
async def list_models(request: Request) -> ModelsListResponse:
    """Return the list of model aliases configured in models.yaml."""
    provider_router = request.app.state.provider_router
    aliases = provider_router.list_aliases()
    model_objects = [ModelObject(id=alias) for alias in sorted(aliases)]
    return ModelsListResponse(data=model_objects)
