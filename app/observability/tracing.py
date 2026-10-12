"""
Трейсинг LLM-вызовов в Arize Phoenix через OpenTelemetry + OpenInference.

setup_tracing() вызывается в lifespan ДО создания клиентов OpenAI: инструментор
патчит класс клиента, и уже созданные экземпляры могли бы остаться без трейсов.

Адрес коллектора — PHOENIX_COLLECTOR_ENDPOINT (в compose: http://phoenix:6006);
phoenix.otel сам добавляет путь /v1/traces. Если переменная не задана —
трейсинг выключен (локальные тесты не шлют спаны в никуда).

Внимание: в трейсы попадают полные промпты и ответы. Phoenix слушает только
127.0.0.1; для продакшена — OPENINFERENCE_HIDE_INPUTS/OUTPUTS=true.
"""

import os

import structlog

log = structlog.get_logger("tracing")
_instrumented = False


def setup_tracing(project_name: str = "diploma-fastapi") -> bool:
    global _instrumented
    if _instrumented:
        return True
    if not os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"):
        log.info("tracing.disabled", reason="PHOENIX_COLLECTOR_ENDPOINT не задан")
        return False

    from openinference.instrumentation.openai import OpenAIInstrumentor
    from phoenix.otel import register

    tracer_provider = register(project_name=project_name, batch=True, set_global_tracer_provider=True)
    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
    _instrumented = True
    log.info("tracing.enabled", project=project_name, endpoint=os.environ["PHOENIX_COLLECTOR_ENDPOINT"])
    return True
