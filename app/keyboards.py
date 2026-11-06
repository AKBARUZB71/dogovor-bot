"""Klaviaturalar. Reply-klaviatura — asosiy menyu, inline — tanlov va tasdiqlash uchun."""

from __future__ import annotations

from typing import Iterable, Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import texts
from .db import Manager


def manager_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_GET)], [KeyboardButton(text=texts.BTN_MY)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_menu(*, with_manager_buttons: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=texts.BTN_ADMIN_GET)],
        [KeyboardButton(text=texts.BTN_LAST), KeyboardButton(text=texts.BTN_TODAY)],
        [KeyboardButton(text=texts.BTN_HISTORY), KeyboardButton(text=texts.BTN_REGS)],
        [KeyboardButton(text=texts.BTN_MANAGERS), KeyboardButton(text=texts.BTN_CSV)],
    ]
    if with_manager_buttons:
        rows.insert(0, [KeyboardButton(text=texts.BTN_GET), KeyboardButton(text=texts.BTN_MY)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def _grid(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def registration_choices(managers: Iterable[Manager]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{m.code} — {m.name}", callback_data=f"reg:{m.code}") for m in managers
    ]
    return InlineKeyboardMarkup(inline_keyboard=_grid(buttons))


def reg_decision(reg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Tasdiqlash", callback_data=f"rega:{reg_id}"),
            InlineKeyboardButton(text="Rad etish", callback_data=f"regr:{reg_id}"),
        ]]
    )


def _status_mark(m: Manager) -> str:
    if m.telegram_id is None:
        return " (bo‘sh)"
    if m.is_blocked:
        return " (blok)"
    return ""


def managers_list(managers: Iterable[Manager]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{m.code} — {m.name}{_status_mark(m)}", callback_data=f"mgr:{m.code}")
        for m in managers
    ]
    return InlineKeyboardMarkup(inline_keyboard=_grid(buttons))


def manager_card(m: Manager) -> InlineKeyboardMarkup:
    rows = []
    if m.telegram_id is not None:
        if m.is_blocked:
            rows.append([InlineKeyboardButton(text="Blokdan chiqarish", callback_data=f"unb:{m.code}")])
        else:
            rows.append([InlineKeyboardButton(text="Bloklash", callback_data=f"blk:{m.code}")])
    rows.append([InlineKeyboardButton(text="Telegram ID’ni almashtirish", callback_data=f"chid:{m.code}")])
    rows.append([InlineKeyboardButton(text="Tarix", callback_data=f"hist:{m.code}")])
    rows.append([InlineKeyboardButton(text="Ro‘yxatga qaytish", callback_data="mgrs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_choices(managers: Iterable[Manager]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{m.code} — {m.name}", callback_data=f"hist:{m.code}") for m in managers
    ]
    rows = _grid(buttons)
    rows.append([InlineKeyboardButton(text=texts.ADMIN_RECIPIENT, callback_data="hist:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def markup_from_key(key: Optional[str]):
    """Outbox'da saqlangan klaviatura kalitini obyektga aylantiradi."""
    if not key:
        return None
    if key == "manager_menu":
        return manager_menu()
    if key.startswith("reg:"):
        return reg_decision(int(key.split(":", 1)[1]))
    return None
