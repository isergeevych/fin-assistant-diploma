"""Лимиты на пользователя: частота, $ и токены в сутки, админы без лимитов, учёт реальной стоимости."""

from types import SimpleNamespace

import httpx
import pytest

from app.services.usage_limits import SpendMeter, UsageLimiter, UsageLimitError, current_meter, record_usage
from bot.handlers.errors import user_message_for


class FakeRedis:
    """Минимум команд Redis, которые использует UsageLimiter."""

    def __init__(self):
        self.data: dict = {}

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        return True

    async def hmget(self, key, *fields):
        h = self.data.get(key, {})
        return [h.get(f) for f in fields]

    async def hgetall(self, key):
        return {k: str(v) for k, v in self.data.get(key, {}).items()}

    async def scan_iter(self, match, count=100):
        for key in list(self.data):
            if key.startswith(match.rstrip("*")):
                yield key

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def hincrbyfloat(self, key, field, amount):
        self.ops.append((key, field, float(amount)))

    def hincrby(self, key, field, amount):
        self.ops.append((key, field, int(amount)))

    def expire(self, key, seconds):
        pass

    async def execute(self):
        for key, field, amount in self.ops:
            h = self.redis.data.setdefault(key, {})
            h[field] = h.get(field, 0) + amount


def _limiter(**kw) -> UsageLimiter:
    params = {"daily_usd": 1.0, "daily_tokens": 50_000, "rate_per_minute": 10, "exempt": {"admin"}} | kw
    return UsageLimiter(FakeRedis(), **params)


def _meter(usd=0.0, tokens=0) -> SpendMeter:
    return SpendMeter(usd=usd, tokens=tokens, calls=1)


async def test_rate_limit_eleventh_message_in_a_minute():
    limiter = _limiter()
    for _ in range(10):
        await limiter.check("u1")
    with pytest.raises(UsageLimitError) as e:
        await limiter.check("u1")
    assert e.value.code == "rate_limit" and "10 сообщений в минуту" in e.value.message


async def test_daily_dollar_limit():
    limiter = _limiter()
    await limiter.add("u1", _meter(usd=0.999))
    await limiter.check("u1")  # ещё не дотянул до $1
    await limiter.add("u1", _meter(usd=0.002))
    with pytest.raises(UsageLimitError) as e:
        await limiter.check("u1")
    assert e.value.code == "daily_limit" and e.value.message == "Лимит на сегодня исчерпан, обновится в 00:00 МСК."
    assert 0 < e.value.retry_after <= 86_400


async def test_daily_token_limit_hits_first():
    limiter = _limiter()
    await limiter.add("u1", _meter(usd=0.05, tokens=50_000))
    with pytest.raises(UsageLimitError):
        await limiter.check("u1")
    await limiter.check("u2")  # лимит у каждого свой


async def test_admin_is_exempt_but_spend_is_visible():
    limiter = _limiter()
    await limiter.add("admin", _meter(usd=5.0, tokens=999_999))
    for _ in range(30):
        await limiter.check("admin")  # ни частота, ни бюджет
    today = await limiter.today()
    assert today[0] == {"owner": "admin", "usd": 5.0, "tokens": 999_999, "messages": 1, "exempt": True}


async def test_zero_means_disabled():
    limiter = _limiter(daily_usd=0, daily_tokens=0, rate_per_minute=0)
    await limiter.add("u1", _meter(usd=100, tokens=10**7))
    for _ in range(50):
        await limiter.check("u1")


async def test_redis_down_is_fail_open():
    class DownRedis(FakeRedis):
        async def incr(self, key):
            raise ConnectionError("redis down")

    await UsageLimiter(DownRedis(), 1.0, 50_000, 10, set()).check("u1")  # не блокируем пользователя


def test_meter_uses_real_cost_and_falls_back_to_price_table():
    meter = SpendMeter()
    token = current_meter.set(meter)
    try:
        record_usage(SimpleNamespace(prompt_tokens=100, completion_tokens=20, cost=0.00042), "openai/gpt-5.4-mini")
        record_usage({"prompt_tokens": 1_000_000, "completion_tokens": 0}, "openai/gpt-5.4-mini")  # без cost
    finally:
        current_meter.reset(token)
    assert meter.calls == 2 and meter.tokens == 1_000_120
    assert meter.usd == pytest.approx(0.00042 + 0.75)  # $0,75 за 1M входных токенов по каталогу


def test_record_without_meter_is_noop():
    record_usage(SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=1.0), "m")  # вне сообщения — ничего


def test_bot_shows_server_message_for_limits():
    request = httpx.Request("POST", "http://app/chats/x/messages")
    response = httpx.Response(429, request=request, json={"detail": {"code": "daily_limit",
                                                                     "message": "Лимит на сегодня исчерпан, обновится в 00:00 МСК."}})
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    assert user_message_for(exc) == "Лимит на сегодня исчерпан, обновится в 00:00 МСК."
