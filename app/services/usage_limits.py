"""
Лимиты на пользователя (время тестов): частота сообщений и суточный бюджет — деньги и токены.

  - частота: не больше USAGE_RATE_PER_MINUTE сообщений в минуту (окно — календарная минута);
  - сутки (по Москве, сброс в 00:00 МСК): не больше USAGE_DAILY_USD долларов и USAGE_DAILY_TOKENS токенов —
    срабатывает тот лимит, что наступит раньше;
  - админы (BOT_ADMIN_IDS) — без лимитов, но их траты тоже считаются (видно в статистике).

Стоимость — РЕАЛЬНАЯ из ответа OpenRouter (usage.cost), если её нет — по ценам каталога моделей.
Считаются все вызовы модели, сделанные ради одного сообщения: переписывание вопроса для поиска (RAG condense)
и сам ответ. Механика: на время обработки сообщения в contextvar кладётся SpendMeter, каждый LLM-вызов
добавляет в него usage (contextvar переходит и в потоки anyio — проверено), в конце сумма пишется в Redis.

Проверка — ДО вызова модели: если лимит уже исчерпан, сообщение не обрабатывается. Последнее сообщение может
немного перейти лимит (заранее стоимость ответа неизвестна) — для тестового лимита это приемлемо.
Redis недоступен → лимиты не применяются (fail-open, warning в лог); страховка сверху — лимит на сам ключ
в кабинете OpenRouter.
"""

import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from app.llm.pricing import cost_usd

log = structlog.get_logger("usage_limits")
MSK = ZoneInfo("Europe/Moscow")


@dataclass
class SpendMeter:
    usd: float = 0.0
    tokens: int = 0
    calls: int = 0

    def add(self, usage, model: str) -> None:
        if usage is None:
            return
        get = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
        prompt, completion = get("prompt_tokens", 0) or 0, get("completion_tokens", 0) or 0
        cost = get("cost")
        self.usd += float(cost) if cost is not None else cost_usd(model, prompt, completion)
        self.tokens += prompt + completion
        self.calls += 1


current_meter: ContextVar[SpendMeter | None] = ContextVar("current_meter", default=None)


def record_usage(usage, model: str) -> None:
    """Вызывается после каждого LLM-вызова; вне обработки сообщения пользователя (нет meter) — ничего."""
    meter = current_meter.get()
    if meter is not None:
        meter.add(usage, model)


class UsageLimitError(Exception):
    http_status = 429

    def __init__(self, code: str, message: str, retry_after: int):
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


def _seconds_to_midnight_msk(now: datetime) -> int:
    tomorrow = datetime(now.year, now.month, now.day, tzinfo=MSK).timestamp() + 86_400
    return max(int(tomorrow - now.timestamp()), 1)


class UsageLimiter:
    def __init__(self, redis, daily_usd: float, daily_tokens: int, rate_per_minute: int, exempt: set[str]):
        self.redis, self.daily_usd, self.daily_tokens = redis, daily_usd, daily_tokens
        self.rate_per_minute, self.exempt = rate_per_minute, exempt

    @staticmethod
    def day_key(owner: str, now: datetime | None = None) -> str:
        return f"usage:{(now or datetime.now(MSK)).strftime('%Y-%m-%d')}:{owner}"

    async def check(self, owner: str) -> None:
        """Перед обработкой сообщения. Бросает UsageLimitError, если превышена частота или суточный бюджет."""
        if owner in self.exempt or not (self.daily_usd or self.daily_tokens or self.rate_per_minute):
            return
        now = datetime.now(MSK)
        try:
            minute = int(time.time() // 60)
            rate_key = f"rate:{owner}:{minute}"
            count = await self.redis.incr(rate_key)
            if count == 1:
                await self.redis.expire(rate_key, 70)
            spent = await self.redis.hmget(self.day_key(owner, now), "usd", "tokens")
        except Exception as e:  # noqa: BLE001 — Redis недоступен: не блокируем пользователя
            log.warning("usage_limits.redis_unavailable", error=type(e).__name__)
            return
        if self.rate_per_minute and count > self.rate_per_minute:
            wait = 60 - int(time.time() % 60)
            raise UsageLimitError("rate_limit", f"Слишком часто: не больше {self.rate_per_minute} сообщений в "
                                                f"минуту. Подождите {wait} с.", wait)
        usd, tokens = float(spent[0] or 0), int(float(spent[1] or 0))
        if (self.daily_usd and usd >= self.daily_usd) or (self.daily_tokens and tokens >= self.daily_tokens):
            log.info("usage_limits.daily_exceeded", owner=owner, usd=round(usd, 4), tokens=tokens)
            raise UsageLimitError("daily_limit", "Лимит на сегодня исчерпан, обновится в 00:00 МСК.",
                                  _seconds_to_midnight_msk(now))

    async def add(self, owner: str, meter: SpendMeter) -> None:
        if meter.calls == 0:
            return
        key = self.day_key(owner)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hincrbyfloat(key, "usd", meter.usd)
                pipe.hincrby(key, "tokens", meter.tokens)
                pipe.hincrby(key, "messages", 1)
                pipe.expire(key, 3 * 86_400)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001
            log.warning("usage_limits.redis_unavailable", error=type(e).__name__, lost_usd=meter.usd)

    async def today(self) -> list[dict]:
        """Кто сколько потратил сегодня — для админской статистики."""
        prefix = self.day_key("")
        rows = []
        try:
            async for key in self.redis.scan_iter(match=prefix + "*", count=200):
                owner = key[len(prefix):]
                data = await self.redis.hgetall(key)
                rows.append({"owner": owner, "usd": round(float(data.get("usd", 0)), 4),
                             "tokens": int(float(data.get("tokens", 0))), "messages": int(data.get("messages", 0)),
                             "exempt": owner in self.exempt})
        except Exception as e:  # noqa: BLE001
            log.warning("usage_limits.redis_unavailable", error=type(e).__name__)
        return sorted(rows, key=lambda r: r["usd"], reverse=True)
