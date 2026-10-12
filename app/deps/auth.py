"""
Проверка служебных токенов (заголовки X-Internal-Token / X-Admin-Token).
Сравнение — secrets.compare_digest (постоянное время): обычное `!=` из стартер-кода
курса позволяет подбирать токен посимвольно по времени ответа.
"""

import secrets

from fastapi import Header, HTTPException

from app.deps.providers import SettingsDep


def require_internal(settings: SettingsDep, x_internal_token: str = Header(default="")) -> None:
    expected = settings.internal_token.get_secret_value() if settings.internal_token else ""
    if not expected or not secrets.compare_digest(x_internal_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")
