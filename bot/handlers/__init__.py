"""Сборка роутеров. Порядок важен: команды первыми (иначе /cancel внутри сценария
попал бы в FSM), затем FSM, затем «любой текст»."""

from aiogram import Router

from bot.handlers import commands, fsm, media, text


def build_router() -> Router:
    root = Router(name="root")
    root.include_routers(commands.router, fsm.router, media.router, text.router)
    return root
