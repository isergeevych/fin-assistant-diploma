"""Перевод ошибок backend в понятный пользователю текст — никаких traceback в чате."""

import httpx

from bot.services.backend_client import BackendStreamError


INPUT_REJECTED_TEXT = (
    "Похоже на попытку изменить мои правила — так я не работаю. "
    "Я помогаю с личными финансами: налоги и вычеты, вклады, кредиты, бюджет, защита от мошенников. "
    "Задайте вопрос по одной из этих тем."
)


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code") if isinstance(error, dict) else None


def user_message_for(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return "Сервис недоступен, попробуйте позже."
    if isinstance(exc, httpx.ReadTimeout):
        return "Ответ занимает слишком долго, попробуйте ещё раз."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 400:
            if _error_code(exc.response) == "input_rejected":  # П4: фильтр на входе — объясняем, а не «не могу обработать»
                return INPUT_REJECTED_TEXT
            return "Не могу обработать этот запрос. Попробуйте сформулировать иначе."
        if status == 403:
            return "Сообщение нарушает правила сервиса, я не могу на него ответить."
        if status == 401:
            return "Нет доступа к этой функции."
        if status == 404:
            return "Диалог не найден — нажмите /start."
        if status == 429:
            # лимиты на пользователя: сервер сам формулирует, что случилось и когда снова можно
            # («Лимит на сегодня исчерпан, обновится в 00:00 МСК.» / «не больше 10 сообщений в минуту…»)
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict) and detail.get("message"):
                return str(detail["message"])
            return "Слишком много запросов, подождите минуту."
        if status >= 500:
            return "Внутренняя ошибка сервиса, попробуйте позже."
    if isinstance(exc, BackendStreamError):
        if exc.code == "output_blocked":
            return "Не могу показать этот ответ. Попробуйте задать вопрос иначе."
        return "Сервис не смог ответить, попробуйте позже."
    return "Что-то пошло не так, попробуйте позже."
