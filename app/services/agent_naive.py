"""Б6.1 — наивный agent loop на Chat Completions, без фреймворков. CLI: python -m app.services.agent_naive "<задача>" --trace"""
import json, logging, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

from app.core.config import get_settings

log = logging.getLogger("agent_naive")
SYSTEM = ("Ты — агент Финпомощника. Решай задачу пользователя с помощью инструментов. Факты о финансах, налогах и "
          "приложении бери только из search_knowledge_base. Если инструмента для действия нет — честно скажи об этом.")
_rag = None


def search_knowledge_base(query: str) -> str:
    global _rag
    if _rag is None:
        from app.services.rag import RAGService
        _rag = RAGService(get_settings()).build()
    chunks, top = _rag.retrieve(query)
    if not _rag.passes_guard(chunks, top):  # тот же score-guard, что у RAG: мусорный top-1 модели не отдаём
        return "В базе знаний ничего не найдено по этому запросу."
    return f"[{chunks[0]['file_name']}] {chunks[0]['text']}"


def get_current_time(timezone: str = "Europe/Moscow") -> str:
    return datetime.now(ZoneInfo(timezone)).isoformat()


def send_telegram_message(chat_id: str, text: str) -> str:
    print(f"[TELEGRAM → {chat_id}] {text}")  # заглушка: никаких реальных вызовов Telegram API
    return f"Сообщение отправлено в {chat_id}"


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required, "additionalProperties": False}}}


TOOLS = [
    _fn("search_knowledge_base", "Ищет в базе знаний Финпомощника (налоги, вычеты, вклады, кредиты, мошенничество, "
        "приложение) и возвращает самый релевантный фрагмент. Вызывай перед любым фактическим ответом на эти темы.",
        {"query": {"type": "string", "description": "Самостоятельный поисковый запрос на русском"}}, ["query"]),
    _fn("get_current_time", "Возвращает текущие дату и время в часовом поясе (ISO 8601). Вызывай, когда задача "
        "зависит от сегодняшней даты: сроки, дедлайны, «сколько осталось».",
        {"timezone": {"type": "string", "description": "IANA-зона, по умолчанию Europe/Moscow"}}, []),
    _fn("send_telegram_message", "Отправляет текстовое сообщение клиенту в Telegram-чат. Вызывай только когда "
        "пользователь явно попросил отправить сообщение и указал chat_id; это действие необратимо.",
        {"chat_id": {"type": "string"}, "text": {"type": "string"}}, ["chat_id", "text"]),
]
DISPATCH = {"search_knowledge_base": search_knowledge_base, "get_current_time": get_current_time,
            "send_telegram_message": send_telegram_message}  # allowlist: только эти функции, никаких eval/getattr


def run_agent(task: str, max_steps: int = 6) -> dict:
    s = get_settings()
    client = OpenAI(base_url=s.agent_base_url or s.llm.base_url,
                    api_key=(s.agent_api_key or s.llm.api_key).get_secret_value())
    extra = {"extra_body": {"reasoning_effort": s.agent_reasoning_effort}} if s.agent_reasoning_effort else {}
    messages, trace = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}], []
    for step in range(1, max_steps + 1):
        started = time.perf_counter()
        resp = client.chat.completions.create(model=s.agent_model or s.llm.default_model, messages=messages,
                                              tools=TOOLS, **extra)
        msg, usage = resp.choices[0].message, resp.usage
        messages.append(msg.model_dump(exclude_none=True))
        # токены и время LLM — только на первой записи шага: при параллельных tool_calls иначе сумма по trace завышена
        base = {"step": step, "llm_input_tokens": usage.prompt_tokens, "llm_output_tokens": usage.completion_tokens}
        if not msg.tool_calls:
            trace.append({**base, "tool_name": None, "tool_args": None, "tool_result": None,
                          "duration_ms": round((time.perf_counter() - started) * 1000)})
            return {"answer": msg.content, "steps": step, "trace": trace}
        for i, call in enumerate(msg.tool_calls):
            if i:
                base, started = {"step": step, "llm_input_tokens": 0, "llm_output_tokens": 0}, time.perf_counter()
            fn = DISPATCH.get(call.function.name)
            try:
                result = fn(**json.loads(call.function.arguments or "{}")) if fn else \
                    f"Ошибка: инструмента {call.function.name} нет. Доступны: {', '.join(DISPATCH)}."
            except Exception as e:  # noqa: BLE001 — ошибку отдаём модели, цикл продолжается
                result = f"Ошибка выполнения {call.function.name}: {type(e).__name__}: {e}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
            trace.append({**base, "tool_name": call.function.name, "tool_args": call.function.arguments,
                          "tool_result": str(result)[:200], "duration_ms": round((time.perf_counter() - started) * 1000)})
            log.info("step=%s tool=%s args=%s", step, call.function.name, call.function.arguments)
    return {"answer": None, "steps": max_steps, "trace": trace, "error": f"max_steps={max_steps} исчерпан"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = run_agent(" ".join(a for a in sys.argv[1:] if a != "--trace"))
    print(out["answer"] or f"ОСТАНОВКА: {out['error']}")
    if "--trace" in sys.argv:
        print(json.dumps(out["trace"], ensure_ascii=False, indent=2))
