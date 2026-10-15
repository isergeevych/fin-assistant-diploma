"""
Б4.3 — фото, голос, аудио, документы. Бот ничего не распознаёт сам (в нём нет openai, pypdf,
python-docx): скачивает файл из Telegram и отдаёт в backend тем же send_message, что и текст.
"""

from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.common import ask_backend
from bot.services.backend_client import BackendClient

router = Router(name="media")

MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_DOC_BYTES = 10 * 1024 * 1024
DOC_EXTENSIONS = (".pdf", ".docx")


async def download(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buffer = BytesIO()
    await bot.download_file(file.file_path, destination=buffer)
    return buffer.getvalue()


@router.message(StateFilter(None), F.photo)
async def handle_photo(message: Message, bot: Bot, backend: BackendClient, settings: BotSettings) -> None:
    # Telegram присылает несколько размеров — берём самый крупный, который не больше 2 МБ
    fitting = [p for p in message.photo if (p.file_size or 0) <= MAX_PHOTO_BYTES]
    if not fitting:
        await message.answer("Фото слишком большое, пришлите поменьше (до 2 МБ).", parse_mode=None)
        return
    data = await download(bot, fitting[-1].file_id)
    await ask_backend(message, backend, message.caption or "Что на этом изображении?", settings,
                      media=data, mime="image/jpeg", filename="photo.jpg")


@router.message(StateFilter(None), F.voice)
async def handle_voice(message: Message, bot: Bot, backend: BackendClient, settings: BotSettings) -> None:
    data = await download(bot, message.voice.file_id)
    # ogg/opus уходит как есть: и Whisper, и аудио-модель через OpenRouter принимают его напрямую
    await ask_backend(message, backend, message.caption or "[голосовое сообщение]", settings,
                      media=data, mime="audio/ogg", filename="voice.ogg")


@router.message(StateFilter(None), F.audio)
async def handle_audio(message: Message, bot: Bot, backend: BackendClient, settings: BotSettings) -> None:
    if (message.audio.file_size or 0) > MAX_DOC_BYTES:
        await message.answer("Аудио слишком большое (до 10 МБ).", parse_mode=None)
        return
    data = await download(bot, message.audio.file_id)
    await ask_backend(message, backend, message.caption or "[аудио]", settings,
                      media=data, mime=message.audio.mime_type, filename=message.audio.file_name or "audio.mp3")


@router.message(StateFilter(None), F.document)
async def handle_document(message: Message, bot: Bot, backend: BackendClient, settings: BotSettings) -> None:
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith(DOC_EXTENSIONS):
        await message.answer("Я читаю документы PDF и DOCX.", parse_mode=None)
        return
    if (doc.file_size or 0) > MAX_DOC_BYTES:
        await message.answer("Документ слишком большой (до 10 МБ).", parse_mode=None)
        return
    data = await download(bot, doc.file_id)
    await ask_backend(message, backend, message.caption or "Разбери этот документ", settings,
                      media=data, mime=doc.mime_type, filename=doc.file_name or "document")
