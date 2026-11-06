"""Saqlanadigan xabar navbati (outbox).

Raqam va oluvchi avval bazaga yoziladi, xabarlar esa shu tranzaksiyada outbox'ga qo'yiladi.
Bu worker ularni yuboradi; xato bo'lsa cheklangan marta, oshib boruvchi kutish bilan qayta uradi.
Yagona worker ishlagani uchun bitta yozuv bir vaqtda ikki marta yuborilmaydi.
Telegram javobi yo'qolsa (yuborildi, lekin tasdiq kelmadi) xabar takror borishi mumkin —
lekin raqam hech qachon qayta ajratilmaydi.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)

from .db import Database
from .keyboards import markup_from_key

log = logging.getLogger(__name__)


class Outbox:
    def __init__(
        self, db: Database, bot: Bot, *, max_attempts: int = 10,
        poll_interval: float = 5.0, batch_size: int = 30,
    ) -> None:
        self.db = db
        self.bot = bot
        self.max_attempts = max_attempts
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._wakeup = asyncio.Event()

    def kick(self) -> None:
        """Yangi yozuv qo'shildi — worker darhol uyg'onsin."""
        self._wakeup.set()

    async def run(self) -> None:
        log.info("Outbox worker ishga tushdi")
        while True:
            self._wakeup.clear()
            try:
                processed = await self.process_due()
                if processed >= self.batch_size:
                    continue  # navbatda yana bor
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Outbox ishlov berishda xato")
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def process_due(self) -> int:
        items = await asyncio.to_thread(self.db.outbox_due, self.batch_size)
        for item in items:
            try:
                await self.bot.send_message(
                    chat_id=item.chat_id, text=item.text, reply_markup=markup_from_key(item.markup)
                )
            except TelegramRetryAfter as e:
                await asyncio.to_thread(
                    self.db.outbox_mark_retry, item.id, f"RetryAfter {e.retry_after}s",
                    permanent=False, max_attempts=self.max_attempts, delay=float(e.retry_after),
                )
            except TelegramUnauthorizedError:
                # Token noto'g'ri — urinishni sarflamaymiz, keyinroq qayta uramiz.
                log.error("Telegram token rad etildi; outbox kutmoqda")
                raise
            except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as e:
                # Foydalanuvchi botni bloklagan / chat topilmadi — avtomatik qayta urinish foydasiz.
                # Foydalanuvchi keyin tugmani bossa, o'sha raqam qayta navbatga qo'yiladi.
                await asyncio.to_thread(
                    self.db.outbox_mark_retry, item.id, f"{type(e).__name__}: {e.message}",
                    permanent=True, max_attempts=self.max_attempts,
                )
                log.warning("Xabar yetkazilmadi (outbox=%s, chat=%s): %s", item.id, item.chat_id, e.message)
            except Exception as e:  # tarmoq, server xatolari va h.k.
                gave_up = await asyncio.to_thread(
                    self.db.outbox_mark_retry, item.id, f"{type(e).__name__}: {e}",
                    permanent=False, max_attempts=self.max_attempts,
                )
                log.warning(
                    "Xabar yuborishda xato (outbox=%s, chat=%s, %s): %s",
                    item.id, item.chat_id, "urinishlar tugadi" if gave_up else "qayta uriniladi",
                    type(e).__name__,
                )
            else:
                await asyncio.to_thread(self.db.outbox_mark_sent, item.id)
        return len(items)
