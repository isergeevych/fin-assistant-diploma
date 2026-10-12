"""
Б6.2 — ReAct-агент с лимитами и self-reflection (Reflexion-light). agent_naive.py не тронут — это baseline.

Отличия от наивного агента:
  - ReAct-промпт: одно предложение «что и зачем» перед действием, ровно ОДИН tool за шаг
    (parallel_tool_calls=False), остановка, как только данных достаточно, честный отказ без выдумок;
  - tools описаны по чек-листу: имя-глагол, description отвечает на 4 вопроса (что делает / когда звать /
    когда НЕ звать / что вернёт), строгая схема (strict, additionalProperties: false);
  - два жёстких лимита: max_iterations и timeout_per_iteration_sec (LLM-вызов + tool) — по превышению
    явное «Превышен лимит итераций» / «Timeout», а не зависание;
  - critic после каждого observation: OK или REVISE: <причина>. REVISE → фидбек уходит system-припиской в
    следующую итерацию, генерация поднимается до премиум-модели. Ревизий ≤ max_revisions за ЗАПУСК
    (счётчик не сбрасывается между итерациями);
  - учёт токенов по каждому вызову, включая critic: итог в результате и в structlog.

    python -m app.services.agent_react "<задача>"
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import openai
import structlog
from openai import OpenAI

from app.core.config import get_settings
from app.services.agent_naive import get_current_time, search_knowledge_base, send_telegram_message

log = structlog.get_logger("agent_react")

SYSTEM = (
    "Ты — агент Финпомощника. Сам выбирай инструменты и их порядок, при необходимости разбивая задачу на подзадачи.\n"
    "- Перед каждым вызовом инструмента одним предложением поясни, что и зачем делаешь.\n"
    "- Вызывай ровно один инструмент за шаг и опирайся на его результат.\n"
    "- Как только данных достаточно — дай финальный ответ без вызова инструментов.\n"
    "- Если задачу можно решить без инструментов (арифметика, общий вопрос, приветствие) — отвечай сразу.\n"
    "- Не выдумывай данные: используй только то, что вернули инструменты. Если инструмент вернул «ничего не "
    "найдено» или подходящего инструмента нет — прямо скажи, что сделать нельзя."
)

CRITIC = (
    "Ты — критик шага агента. Дан вопрос пользователя, пояснение агента к шагу, вызванный инструмент и его результат.\n"
    "Ответь ровно одной строкой: «OK», если шаг осмыслен и результат можно использовать, или "
    "«REVISE: <причина в одном предложении>», если инструмент выбран неверно, аргументы не соответствуют задаче, "
    "результат пустой/не по теме, а агент, судя по пояснению, собирается на него опереться, или действие "
    "необратимо и не было явно запрошено."
)


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TOOLS = [
    _tool("search_knowledge_base",
          "Что делает: ищет в базе знаний Финпомощника (налоги и вычеты, вклады и кредиты, бюджет, мошенничество, FAQ "
          "приложения) и возвращает один самый релевантный фрагмент с именем файла. Когда звать: перед любым "
          "фактическим утверждением на эти темы. Когда НЕ звать: арифметика, приветствия, вопросы не о финансах. "
          "Что вернёт: «[файл] текст» или «В базе знаний ничего не найдено…» — тогда не выдумывай ответ.",
          {"query": {"type": "string", "description": "Самостоятельный поисковый запрос на русском, 3–12 слов"}},
          ["query"]),
    _tool("get_current_time",
          "Что делает: возвращает текущие дату и время в часовом поясе (ISO 8601). Когда звать: задача зависит от "
          "сегодняшней даты — сроки, «сколько дней осталось». Когда НЕ звать: дата в задаче уже названа. "
          "Что вернёт: строку вида 2026-10-13T02:30:00+03:00.",
          {"timezone": {"type": "string", "description": "IANA-зона, например Europe/Moscow"}}, ["timezone"]),
    _tool("send_telegram_message",
          "Что делает: отправляет текст клиенту в Telegram-чат — действие НЕОБРАТИМО. Когда звать: только если "
          "пользователь явно попросил отправить и назвал chat_id. Когда НЕ звать: для черновика, «на проверку», "
          "если chat_id не назван или текст похож на мошеннический. Что вернёт: «Сообщение отправлено в <chat_id>».",
          {"chat_id": {"type": "string", "description": "Числовой id чата, как назвал пользователь"},
           "text": {"type": "string", "description": "Готовый текст сообщения"}}, ["chat_id", "text"]),
]
DISPATCH = {"search_knowledge_base": search_knowledge_base, "get_current_time": get_current_time,
            "send_telegram_message": send_telegram_message}


def _add_usage(total: dict, usage) -> dict:
    step = {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens, "total": usage.total_tokens}
    for k, v in step.items():
        total[k] += v
    return step


def run_react_with_reflection(question: str, max_iterations: int = 10, timeout_per_iteration_sec: float = 15.0,
                              max_revisions: int = 2, model_main: str | None = None, model_premium: str | None = None,
                              model_critic: str | None = None) -> dict:
    if not 1 <= max_iterations < 30:
        raise ValueError("max_iterations должен быть в диапазоне 1–29")
    s = get_settings()
    client = OpenAI(base_url=s.agent_base_url or s.llm.base_url, api_key=(s.agent_api_key or s.llm.api_key).get_secret_value(),
                    timeout=timeout_per_iteration_sec, max_retries=0)  # ретраи съели бы бюджет итерации
    model_main = model_main or s.agent_model or s.llm.default_model
    model_premium = model_premium or s.agent_premium_model
    model_critic = model_critic or model_main
    extra = {"extra_body": {"reasoning_effort": s.agent_reasoning_effort}} if s.agent_reasoning_effort else {}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    usage_total = {"prompt": 0, "completion": 0, "total": 0}
    revisions_used, trace, model = 0, [], model_main
    pool = ThreadPoolExecutor(max_workers=1)

    def finish(answer: str | None, iterations: int, stop: str, error: str | None = None) -> dict:
        pool.shutdown(wait=False, cancel_futures=True)
        log.info("agent_react.done", stop=stop, iterations=iterations, revisions=revisions_used, **usage_total)
        result = {"answer": answer, "iterations": iterations, "revisions_used": revisions_used, "stop_reason": stop,
                  "usage": usage_total, "trace": trace}
        return {**result, "error": error} if error else result

    for it in range(1, max_iterations + 1):
        started = time.monotonic()
        try:
            resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS, tool_choice="auto",
                                                  parallel_tool_calls=False, **extra)
        except openai.APITimeoutError:
            return finish(None, it, "timeout", f"Timeout: итерация {it} дольше {timeout_per_iteration_sec} с (LLM)")
        msg = resp.choices[0].message
        step_usage = _add_usage(usage_total, resp.usage)
        messages.append(msg.model_dump(exclude_none=True))
        entry = {"iteration": it, "model": model, "thought": msg.content, "tool_name": None, "tool_args": None,
                 "observation": None, "critic": None, "usage": step_usage}
        if not msg.tool_calls:
            entry["latency_ms"] = round((time.monotonic() - started) * 1000)
            trace.append(entry)
            log.info("agent_react.step", **{k: entry[k] for k in ("iteration", "model", "latency_ms")}, final=True)
            return finish(msg.content, it, "answer")
        call = msg.tool_calls[0]  # parallel_tool_calls=False — один вызов за шаг
        fn = DISPATCH.get(call.function.name)
        remaining = timeout_per_iteration_sec - (time.monotonic() - started)
        try:
            if fn is None:
                observation = f"Ошибка: инструмента {call.function.name} нет. Доступны: {', '.join(DISPATCH)}."
            else:
                future = pool.submit(fn, **json.loads(call.function.arguments or "{}"))
                observation = str(future.result(timeout=max(remaining, 0.1)))
        except FutureTimeout:
            return finish(None, it, "timeout", f"Timeout: итерация {it} дольше {timeout_per_iteration_sec} с (tool)")
        except Exception as e:  # noqa: BLE001 — ошибка tool — это observation для модели
            observation = f"Ошибка выполнения {call.function.name}: {type(e).__name__}: {e}"
        messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
        entry.update(tool_name=call.function.name, tool_args=call.function.arguments, observation=observation[:300],
                     latency_ms=round((time.monotonic() - started) * 1000))

        critic_prompt = (f"Вопрос: {question}\nПояснение агента: {msg.content or '—'}\nИнструмент: {call.function.name}"
                         f"({call.function.arguments})\nРезультат: {observation[:1500]}")
        critic = client.chat.completions.create(model=model_critic, messages=[
            {"role": "system", "content": CRITIC}, {"role": "user", "content": critic_prompt}], **extra)
        verdict = (critic.choices[0].message.content or "OK").strip()
        entry["critic"] = {"verdict": verdict, "usage": _add_usage(usage_total, critic.usage)}
        model = model_main
        if verdict.upper().startswith("REVISE") and revisions_used < max_revisions:
            revisions_used += 1
            model = model_premium  # премиум-шаг генерации после замечания критика
            messages.append({"role": "system", "content": f"Замечание критика к прошлому шагу: {verdict}. "
                                                          "Учти его и скорректируй план."})
        trace.append(entry)
        log.info("agent_react.step", iteration=it, tool=call.function.name, latency_ms=entry["latency_ms"],
                 tokens=step_usage["total"], critic=verdict[:80], revisions=revisions_used)
    return finish(None, max_iterations, "max_iterations", f"Превышен лимит итераций ({max_iterations})")


if __name__ == "__main__":
    out = run_react_with_reflection(" ".join(sys.argv[1:]))
    print(out["answer"] or out["error"])
    print(json.dumps({k: out[k] for k in ("iterations", "revisions_used", "stop_reason", "usage")}, ensure_ascii=False))
