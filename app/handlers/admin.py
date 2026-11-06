"""Admin paneli. Router darajasida IsAdmin filtri bor — har bir xabar va callback
server tomonida ADMIN_IDS bo'yicha tekshiriladi (tugmani yashirish yetarli emas)."""

from __future__ import annotations

import asyncio
import csv
import io

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, Update

from .. import keyboards, texts
from ..config import Settings
from ..db import Database
from ..middlewares import IsAdmin
from ..outbox import Outbox
from ..timeutil import fmt_date, fmt_time, local_day_bounds, utcnow
from .common import RouterSpec, chunk_lines, issue_and_reply

router = RouterSpec()


def create_router() -> Router:
    return router.build("admin", guard=IsAdmin())


class ChangeId(StatesGroup):
    waiting = State()


# ------------------------------------------------------------------ raqamlar


@router.message(F.text == texts.BTN_ADMIN_GET)
async def admin_get_number(
    message: Message, event_update: Update, db: Database, settings: Settings, outbox: Outbox
) -> None:
    await issue_and_reply(message, event_update, db, settings, outbox, as_admin=True)


@router.message(F.text == texts.BTN_LAST)
async def last_number(message: Message, db: Database) -> None:
    c = await asyncio.to_thread(db.last_contract)
    if c is None:
        await message.answer(texts.NO_CONTRACTS_YET)
        return
    await message.answer("Oxirgi berilgan raqam:\n\n" + texts.admin_notify(c).split("\n", 1)[1])


@router.message(F.text == texts.BTN_TODAY)
async def today_numbers(message: Message, db: Database) -> None:
    now = db.clock()
    start, end = local_day_bounds(now)
    items = await asyncio.to_thread(db.contracts_between, start, end)
    header = f"Bugungi raqamlar ({fmt_date(now)}): {len(items)} ta"
    lines = [texts.contract_line(c, with_date=False) for c in items]
    for chunk in chunk_lines(header, lines):
        await message.answer(chunk)


@router.message(F.text == texts.BTN_HISTORY)
async def history_menu(message: Message, db: Database) -> None:
    managers = await asyncio.to_thread(db.list_managers)
    await message.answer("Kimning tarixi kerak?", reply_markup=keyboards.history_choices(managers))


@router.callback_query(F.data.regexp(r"^hist:(\d+|admin)$"))
async def history_show(callback: CallbackQuery, db: Database) -> None:
    key = callback.data.split(":", 1)[1]
    if key == "admin":
        title = texts.ADMIN_RECIPIENT
        items = await asyncio.to_thread(db.admin_history, 20)
    else:
        manager = await asyncio.to_thread(db.get_manager, int(key))
        if manager is None:
            await callback.answer("Menejer topilmadi.", show_alert=True)
            return
        title = f"{manager.code} — {manager.name}"
        items = await asyncio.to_thread(db.manager_history, manager.code, 20)
    await callback.answer()
    if not items:
        await callback.message.answer(f"{title}: raqamlar yo‘q.")
        return
    lines = [texts.contract_line(c, with_recipient=False) for c in items]
    for chunk in chunk_lines(f"{title} — oxirgi {len(items)} ta raqam:", lines):
        await callback.message.answer(chunk)


@router.message(F.text == texts.BTN_CSV)
async def export_csv(message: Message, db: Database) -> None:
    items = await asyncio.to_thread(db.all_contracts)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Dogovor raqami", "Sana", "Vaqt (Toshkent)", "Oluvchi", "Menejer kodi", "Telegram ID"])
    for c in items:
        writer.writerow([
            c.number, fmt_date(c.issued_at), fmt_time(c.issued_at),
            texts.ADMIN_RECIPIENT if c.is_admin else c.recipient_name,
            c.manager_code if c.manager_code is not None else "",
            c.telegram_id,
        ])
    data = buf.getvalue().encode("utf-8-sig")  # BOM — Excel o'zbek harflarini to'g'ri ochadi
    filename = f"dogovorlar_{utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    await asyncio.to_thread(db.audit, message.from_user.id, "csv_export", f"rows={len(items)}")
    await message.answer_document(
        BufferedInputFile(data, filename=filename), caption=f"Jami: {len(items)} ta yozuv"
    )


# ------------------------------------------------------------------ arizalar


@router.message(F.text == texts.BTN_REGS)
async def registrations(message: Message, db: Database) -> None:
    pending = await asyncio.to_thread(db.pending_registrations)
    if not pending:
        await message.answer("Kutilayotgan ariza yo‘q.")
        return
    for reg in pending:
        await message.answer(texts.registration_request(reg), reply_markup=keyboards.reg_decision(reg.id))


_DECISION_TEXT = {
    "approved": "tasdiqlandi",
    "rejected": "rad etildi",
    "taken": "rad etildi: profil boshqa foydalanuvchiga bog‘langan",
    "user_bound": "rad etildi: bu Telegram ID boshqa profilga bog‘langan",
}


@router.callback_query(F.data.regexp(r"^reg[ar]:\d+$"))
async def decide(callback: CallbackQuery, db: Database, outbox: Outbox) -> None:
    approve = callback.data.startswith("rega:")
    reg_id = int(callback.data.split(":", 1)[1])
    status, reg = await asyncio.to_thread(
        db.decide_registration, reg_id=reg_id, approve=approve, admin_id=callback.from_user.id
    )
    outbox.kick()
    if status == "not_found":
        await callback.answer("Ariza topilmadi.", show_alert=True)
        return
    if status == "already_decided":
        await callback.answer("Bu ariza allaqachon ko‘rib chiqilgan.", show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"Ariza #{reg.id} ({reg.manager_code} — {reg.manager_name}, "
            f"ID <code>{reg.telegram_id}</code>) {_DECISION_TEXT[status]}."
        )


# ------------------------------------------------------------------ menejerlar


@router.message(F.text == texts.BTN_MANAGERS)
async def managers(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    items = await asyncio.to_thread(db.list_managers)
    await message.answer("Menejerni tanlang:", reply_markup=keyboards.managers_list(items))


@router.callback_query(F.data == "mgrs")
async def managers_back(callback: CallbackQuery, db: Database) -> None:
    items = await asyncio.to_thread(db.list_managers)
    await callback.answer()
    await callback.message.edit_text("Menejerni tanlang:", reply_markup=keyboards.managers_list(items))


@router.callback_query(F.data.regexp(r"^mgr:\d+$"))
async def manager_card(callback: CallbackQuery, db: Database) -> None:
    m = await asyncio.to_thread(db.get_manager, int(callback.data.split(":", 1)[1]))
    if m is None:
        await callback.answer("Menejer topilmadi.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(texts.manager_card(m), reply_markup=keyboards.manager_card(m))


@router.callback_query(F.data.regexp(r"^(blk|unb):\d+$"))
async def block_toggle(callback: CallbackQuery, db: Database) -> None:
    action, code = callback.data.split(":", 1)
    m = await asyncio.to_thread(
        db.set_blocked, code=int(code), blocked=(action == "blk"), admin_id=callback.from_user.id
    )
    if m is None:
        await callback.answer("Menejer topilmadi.", show_alert=True)
        return
    await callback.answer("Bloklandi." if m.is_blocked else "Blokdan chiqarildi.")
    await callback.message.edit_text(texts.manager_card(m), reply_markup=keyboards.manager_card(m))


@router.callback_query(F.data.regexp(r"^chid:\d+$"))
async def change_id_start(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    m = await asyncio.to_thread(db.get_manager, int(callback.data.split(":", 1)[1]))
    if m is None:
        await callback.answer("Menejer topilmadi.", show_alert=True)
        return
    await state.set_state(ChangeId.waiting)
    await state.update_data(code=m.code)
    await callback.answer()
    await callback.message.answer(
        f"{m.code} — {m.name} uchun yangi Telegram user ID’ni yuboring.\n"
        "Profilni bo‘shatish uchun 0 yuboring. Bekor qilish uchun «bekor» deb yozing."
    )


@router.message(StateFilter(ChangeId.waiting), F.text)
async def change_id_finish(message: Message, state: FSMContext, db: Database) -> None:
    raw = message.text.strip()
    if raw.lower() == "bekor":
        await state.clear()
        await message.answer("Bekor qilindi.")
        return
    if not raw.isdigit():
        await message.answer("Telegram ID faqat raqamlardan iborat bo‘lishi kerak. Qayta yuboring yoki «bekor».")
        return
    code = (await state.get_data())["code"]
    new_id = int(raw) or None
    status, m = await asyncio.to_thread(
        db.change_telegram_id, code=code, new_telegram_id=new_id, admin_id=message.from_user.id
    )
    if status == "id_in_use":
        await message.answer("Bu Telegram ID boshqa menejer profiliga bog‘langan. Boshqa ID yuboring yoki «bekor».")
        return
    await state.clear()
    if status == "not_found":
        await message.answer("Menejer topilmadi.")
        return
    await message.answer("Saqlandi.\n\n" + texts.manager_card(m), reply_markup=keyboards.manager_card(m))
