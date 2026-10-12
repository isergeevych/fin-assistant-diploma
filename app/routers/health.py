from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness: процесс жив", description="Не зависит от Redis и LLM — отвечает 200 всегда.")
async def health() -> dict:
    return {"status": "ok"}
