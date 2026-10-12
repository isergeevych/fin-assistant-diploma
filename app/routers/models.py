from fastapi import APIRouter

from app.schemas.models import MODELS, ModelInfo

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[ModelInfo], summary="Доступные модели и цены ($ за 1M токенов)")
async def list_models() -> list[ModelInfo]:
    return MODELS
