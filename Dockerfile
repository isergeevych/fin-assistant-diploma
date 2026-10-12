# syntax=docker/dockerfile:1.7
# Multi-stage: builder ставит зависимости через uv, runtime получает только /app с готовым .venv.

########## builder ##########
FROM python:3.12-slim-bookworm AS builder

# Версия uv закреплена (та же, что локально, — формат uv.lock совместим)
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 1) Сначала только зависимости: слой пересобирается лишь при изменении pyproject/uv.lock,
#    а правки кода берут его из кеша — повторная сборка занимает секунды.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --extra tracing --extra agents

# 2) Потом код (+ миграции: их применяет одноразовый сервис migrate в compose)
COPY app ./app
COPY bot ./bot
COPY alembic ./alembic
COPY alembic.ini ./

########## runtime ##########
FROM python:3.12-slim-bookworm AS runtime

RUN useradd --create-home --uid 1000 appuser
COPY --from=builder --chown=appuser:appuser /app /app
# Каталог под named volume (docstore, кеш эмбеддингов): пустой том инициализируется из образа вместе с
# владельцем — иначе он создаётся root'ом, и non-root процесс не может в него писать (поймано в Б5.5).
RUN mkdir -p /app/var && chown appuser:appuser /app/var

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER appuser
EXPOSE 8000

# exec-форма (сигналы доходят до uvicorn) и 0.0.0.0 — иначе слушали бы только loopback контейнера
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
