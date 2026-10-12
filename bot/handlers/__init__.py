"""Сборка роутеров. Порядок важен: команды первыми (иначе /cancel внутри сценария
попал бы в FSM), затем admin (фильтр IsAdmin на уровне роутера), FSM, медиа, фидбек,
и последним — «любой текст»."""

from aiogram import Router

from bot.handlers import admin, commands, feedback, fsm, media, text


def build_router() -> Router:
    root = Router(name="root")
    root.include_routers(commands.router, admin.router, fsm.router, media.router, feedback.router, text.router)
    return root
