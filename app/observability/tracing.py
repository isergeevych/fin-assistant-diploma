"""
Трейсинг в Arize Phoenix через OpenTelemetry + OpenInference (Б3.6, LlamaIndex — Б5.6).

setup_tracing() вызывается в lifespan ДО создания клиентов OpenAI: инструментор патчит класс клиента.
Адрес коллектора — PHOENIX_COLLECTOR_ENDPOINT (в compose: http://phoenix:6006; phoenix.otel сам добавит
/v1/traces). Без переменной трейсинг выключен (тесты не шлют спаны в никуда).

Б5.6: с PHOENIX_LLAMAINDEX_TRACING=true добавляется LlamaIndexInstrumentor (extra `tracing`) на ТОТ ЖЕ
tracer provider — в Phoenix видно дерево query → retrieve (чанки и similarity) → LLM (промпт, usage).

Внимание: в трейсы попадают полные промпты и ответы. Phoenix слушает только 127.0.0.1;
для продакшена — OPENINFERENCE_HIDE_INPUTS/OUTPUTS=true.
"""

import os

import structlog

log = structlog.get_logger("tracing")
_tracer_provider = None


def setup_tracing(project_name: str = "diploma-fastapi", llamaindex: bool = False) -> bool:
    global _tracer_provider
    if not os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"):
        log.info("tracing.disabled", reason="PHOENIX_COLLECTOR_ENDPOINT не задан")
        return False
    if _tracer_provider is None:
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from phoenix.otel import register

        _tracer_provider = register(project_name=project_name, batch=True, set_global_tracer_provider=True)
        OpenAIInstrumentor().instrument(tracer_provider=_tracer_provider)
        log.info("tracing.enabled", project=project_name, endpoint=os.environ["PHOENIX_COLLECTOR_ENDPOINT"])
    if llamaindex:
        try:
            from openinference.instrumentation.llama_index import LlamaIndexInstrumentor

            LlamaIndexInstrumentor().instrument(tracer_provider=_tracer_provider)
            log.info("tracing.llamaindex_enabled")
        except ImportError:
            log.warning("tracing.llamaindex_unavailable", reason="extra `tracing` не установлен")
    return True
