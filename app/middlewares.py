"""Middleware'lar: faqat shaxsiy chat va bitta foydalanuvchi so'rovlarini ketma-ket qayta ishlash."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import TelegramObject

from .config import Settings


class PrivateOnlyMiddleware(BaseMiddleware):
    """Guruh/kanaldagi va foydalanuvchisiz update'larni e'tiborsiz qoldiradi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat")
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return None
        if chat is not None and chat.type != "private":
            return None
        return await handler(event, data)


class PerUserLockMiddleware(BaseMiddleware):
    """Bir foydalanuvchining parallel kelgan update'lari navbat bilan bajariladi.
    Turli foydalanuvchilar bir-birini kutmaydi — ularni baza tranzaksiyasi ajratadi."""

    def __init__(self) -> None:
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        async with self._locks[user.id]:
            return await handler(event, data)


class IsAdmin(Filter):
    """Server tomonidagi admin tekshiruvi (.env dagi ADMIN_IDS)."""

    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and settings.is_admin(user.id)
