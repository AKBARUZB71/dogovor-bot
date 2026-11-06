"""Dispatcher yig'ish va botni ishga tushirish."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from . import texts
from .config import Settings, load_settings
from .db import Database
from .handlers import admin, user
from .managers import MANAGERS
from .middlewares import PerUserLockMiddleware, PrivateOnlyMiddleware
from .outbox import Outbox

log = logging.getLogger("dogovor_drop")


def build_dispatcher(db: Database, settings: Settings, outbox: Outbox) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["settings"] = settings
    dp["outbox"] = outbox

    dp.update.outer_middleware(PrivateOnlyMiddleware())
    dp.update.outer_middleware(PerUserLockMiddleware())

    # Tartib muhim: admin -> foydalanuvchi -> qolgan hammasi.
    dp.include_router(admin.create_router())
    dp.include_routers(*user.create_routers())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        log.error("Handler xatosi: %s", type(exc).__name__, exc_info=exc)
        update = event.update
        try:
            await asyncio.to_thread(
                db.audit, None, "error", f"update={update.update_id} {type(exc).__name__}: {exc}"
            )
        except Exception:
            log.exception("Audit yozib bo'lmadi")
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg is not None:
            try:
                await msg.answer(texts.ERROR)
            except Exception:
                pass
        return True

    return dp


class _RedactSecret(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret:
            msg = record.getMessage()
            if self.secret in msg:
                record.msg = msg.replace(self.secret, "***")
                record.args = ()
        return True


def setup_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactSecret(settings.bot_token))
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)


async def main() -> None:
    settings = load_settings()
    setup_logging(settings)

    db = Database(settings.db_path)
    db.init(MANAGERS, initial_last_number=settings.initial_last_number)
    log.info("Baza: %s, oxirgi raqam: %s", settings.db_path, db.counter_value())

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    outbox = Outbox(db, bot, max_attempts=settings.outbox_max_attempts)
    dp = build_dispatcher(db, settings, outbox)

    worker = asyncio.create_task(outbox.run())
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
