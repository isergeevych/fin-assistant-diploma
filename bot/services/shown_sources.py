"""Б5.5 — какие источники бот показал под ответом (message_id → имена файлов). Нужны, чтобы 👍/👎 ушли
в backend вместе со списком показанных источников. Ограниченный LRU в памяти: бот не хранит историю,
а это лишь короткоживущая подсказка — без неё backend возьмёт источники из сохранённого ответа."""

from collections import OrderedDict

_MAX = 2000
_cache: OrderedDict[str, list[str]] = OrderedDict()


def remember(message_id: str, sources: list[dict]) -> None:
    _cache[message_id] = [s.get("file_name") or "" for s in sources]
    _cache.move_to_end(message_id)
    while len(_cache) > _MAX:
        _cache.popitem(last=False)


def recall(message_id: str) -> list[str] | None:
    return _cache.get(message_id)
