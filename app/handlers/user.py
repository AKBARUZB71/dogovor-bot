"""/start, ro'yxatdan o'tish va menejer tugmalari."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, Update

from .. import keyboards, texts
from ..config import Settings
from ..db import Database
from ..outbox import Outbox
from .common import RouterSpec, issue_and_reply

router = RouterSpec()
fallback_router = RouterSpec()


def create_routers() -> list[Router]:
    return [router.build("user"), fallback_router.build("fallback")]


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    uid = message.from_user.id
    info = await asyncio.to_thread(db.user_info, uid)
    active_manager = info.manager is not None and not info.manager.is_blocked

    if settings.is_admin(uid):
        await message.answer(
            texts.ADMIN_PANEL, reply_markup=keyboards.admin_menu(with_manager_buttons=active_manager)
        )
        return
    if info.manager is not None:
        if info.manager.is_blocked:
            await message.answer(texts.BLOCKED, reply_markup=ReplyKeyboardRemove())
        else:
            await message.answer(texts.welcome_manager(info.manager), reply_markup=keyboards.manager_menu())
        return
    if info.pending is not None:
        await message.answer(texts.PENDING, reply_markup=ReplyKeyboardRemove())
        return
    free = await asyncio.to_thread(db.free_managers)
    if not free:
        await message.answer(texts.NO_FREE_PROFILES, reply_markup=ReplyKeyboardRemove())
        return
    await message.answer(texts.WELCOME_CHOOSE, reply_markup=keyboards.registration_choices(free))


@router.callback_query(F.data.regexp(r"^reg:\d+$"))
async def choose_profile(
    callback: CallbackQuery, db: Database, settings: Settings, outbox: Outbox
) -> None:
    code = int(callback.data.split(":", 1)[1])
    user = callback.from_user
    status, reg = await asyncio.to_thread(
        db.create_registration,
        telegram_id=user.id,
        manager_code=code,
        full_name=user.full_name,
        username=user.username,
        admin_ids=settings.admin_ids,
    )
    outbox.kick()
    if status == "created":
        manager = await asyncio.to_thread(db.get_manager, code)
        text = texts.reg_sent(manager)
    elif status == "pending_exists":
        text = texts.PENDING
    elif status == "already_bound":
        text = texts.REG_ALREADY_BOUND
    else:  # taken / not_found
        await callback.answer(texts.REG_TAKEN, show_alert=True)
        free = await asyncio.to_thread(db.free_managers)
        if callback.message and free:
            await callback.message.edit_reply_markup(reply_markup=keyboards.registration_choices(free))
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(text)


@router.message(F.text == texts.BTN_GET)
async def get_number(
    message: Message, event_update: Update, db: Database, settings: Settings, outbox: Outbox
) -> None:
    await issue_and_reply(message, event_update, db, settings, outbox, as_admin=False)


@router.message(F.text == texts.BTN_MY)
async def my_numbers(message: Message, db: Database) -> None:
    info = await asyncio.to_thread(db.user_info, message.from_user.id)
    if info.manager is None:
        await message.answer(texts.NOT_REGISTERED)
        return
    if info.manager.is_blocked:
        await message.answer(texts.BLOCKED)
        return
    items = await asyncio.to_thread(db.manager_history, info.manager.code, 10)
    await message.answer(texts.my_history(items))


@fallback_router.message()
async def unknown_message(message: Message) -> None:
    await message.answer(texts.UNKNOWN)


@fallback_router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.NOT_ADMIN, show_alert=True)
