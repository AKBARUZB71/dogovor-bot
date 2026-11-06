"""Menejer va admin uchun umumiy yordamchi funksiyalar."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from aiogram import Router
from aiogram.types import Message, Update

from .. import texts
from ..config import Settings
from ..db import Database, IssueResult
from ..outbox import Outbox

_FORBIDDEN_TEXT = {
    "not_registered": texts.NOT_REGISTERED,
    "blocked": texts.BLOCKED,
    "not_admin": texts.NOT_ADMIN,
}


class RouterSpec:
    """Handlerlar ro'yxati. aiogram Router faqat bitta dispatcher'ga ulanadi,
    shuning uchun har bir dispatcher (ishga tushirish, testlar) uchun yangi Router yig'iladi."""

    def __init__(self) -> None:
        self._items: list[tuple[str, tuple[Any, ...], Callable[..., Any]]] = []

    def _register(self, kind: str, filters: tuple[Any, ...]):
        def deco(func: Callable[..., Any]) -> Callable[..., Any]:
            self._items.append((kind, filters, func))
            return func

        return deco

    def message(self, *filters: Any):
        return self._register("message", filters)

    def callback_query(self, *filters: Any):
        return self._register("callback_query", filters)

    def build(self, name: str, *, guard: Any = None) -> Router:
        router = Router(name=name)
        if guard is not None:
            router.message.filter(guard)
            router.callback_query.filter(guard)
        for kind, filters, func in self._items:
            getattr(router, kind).register(func, *filters)
        return router


def request_key(update: Update) -> str:
    """Telegram update_id bot uchun yagona — qayta kelgan update shu kalit bilan aniqlanadi."""
    return f"upd:{update.update_id}"


async def issue_and_reply(
    message: Message, event_update: Update, db: Database, settings: Settings, outbox: Outbox,
    *, as_admin: bool,
) -> IssueResult:
    res = await asyncio.to_thread(
        db.issue_contract,
        telegram_id=message.from_user.id,
        request_key=request_key(event_update),
        as_admin=as_admin,
        admin_ids=settings.admin_ids,
        debounce_seconds=settings.debounce_seconds,
    )
    if res.status == "forbidden":
        await message.answer(_FORBIDDEN_TEXT.get(res.reason or "", texts.NOT_ADMIN))
    elif res.status == "debounced":
        # Natija avval yetkazilgan; takroriy bosishga o'sha raqamni ko'rsatamiz.
        await message.answer(texts.debounced(res.contract))
    # new / redeliver / duplicate_update — natija outbox orqali (bir marta navbatda) yuboriladi.
    outbox.kick()
    return res


def chunk_lines(header: str, lines: list[str], limit: int = 3800) -> list[str]:
    """Uzun ro'yxatni Telegram limitiga sig'adigan xabarlarga bo'ladi."""
    chunks: list[str] = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    chunks.append(current)
    return chunks
