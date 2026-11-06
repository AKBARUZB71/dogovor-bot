"""Bot matnlari (o'zbek tilida) va xabar formatlash."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from .timeutil import fmt_date, fmt_time

if TYPE_CHECKING:
    from .db import Contract, Manager, Registration

ADMIN_RECIPIENT = "Admin — o‘zimiz uchun"

# Tugmalar
BTN_GET = "Dogovor raqami olish"
BTN_MY = "Mening oxirgi raqamlarim"
BTN_ADMIN_GET = "O‘zimiz uchun dogovor raqami olish"
BTN_LAST = "Oxirgi berilgan raqam"
BTN_TODAY = "Bugungi raqamlar"
BTN_HISTORY = "Menejer bo‘yicha tarix"
BTN_REGS = "Arizalar"
BTN_MANAGERS = "Menejerlar"
BTN_CSV = "CSV eksport"

# Umumiy xabarlar
WELCOME_CHOOSE = (
    "Dogovor Drop botiga xush kelibsiz.\n"
    "Ro‘yxatdan o‘tish uchun ismingizni tanlang:"
)
NO_FREE_PROFILES = "Hozir bo‘sh menejer profili yo‘q. Admin bilan bog‘laning."
PENDING = "Arizangiz admin tomonidan ko‘rib chiqilmoqda. Tasdiqlangach xabar beramiz."
BLOCKED = "Profilingiz bloklangan. Admin bilan bog‘laning."
NOT_REGISTERED = "Raqam olish uchun ro‘yxatdan o‘tib, admin tasdig‘ini olishingiz kerak. /start yuboring."
NOT_ADMIN = "Ruxsat yo‘q."
UNKNOWN = "Menyudagi tugmalardan foydalaning yoki /start yuboring."
ERROR = "Xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring."
ADMIN_PANEL = "Admin paneli."
REG_TAKEN = "Bu profil band. Boshqa ismni tanlang yoki admin bilan bog‘laning."
REG_ALREADY_BOUND = "Siz allaqachon ro‘yxatdan o‘tgansiz. /start yuboring."
REG_REJECTED_USER = "Arizangiz rad etildi. Savollar bo‘lsa, admin bilan bog‘laning."
NO_CONTRACTS_YET = "Hali raqam berilmagan."
MY_EMPTY = "Sizda hali dogovor raqami yo‘q."


def welcome_manager(m: "Manager") -> str:
    return f"Xush kelibsiz, {escape(m.name)}."


def reg_sent(m: "Manager") -> str:
    return f"Ariza adminga yuborildi: {m.code} — {escape(m.name)}.\nTasdiqlangach xabar beramiz."


def reg_approved_user(m: "Manager") -> str:
    return (
        f"Arizangiz tasdiqlandi. Siz {escape(m.name)} (menejer kodi {m.code}) sifatida ro‘yxatdan o‘tdingiz.\n"
        f"Raqam olish uchun «{BTN_GET}» tugmasini bosing."
    )


def issue_result(c: "Contract") -> str:
    lines = [f"Dogovor raqami: <code>{c.number}</code>"]
    if c.is_admin:
        lines.append(f"Oluvchi: {escape(ADMIN_RECIPIENT)}")
    else:
        lines += [f"Menejer: {escape(c.recipient_name)}", f"Menejer kodi: {c.manager_code}"]
    lines += [f"Sana: {fmt_date(c.issued_at)}", f"Vaqt: {fmt_time(c.issued_at)}"]
    return "\n".join(lines)


def admin_notify(c: "Contract") -> str:
    lines = ["Yangi dogovor raqami olindi"]
    if c.is_admin:
        lines.append(f"Oluvchi: {escape(ADMIN_RECIPIENT)}")
    else:
        lines += [f"Menejer: {escape(c.recipient_name)}", f"Menejer kodi: {c.manager_code}"]
    lines += [
        f"Dogovor raqami: <code>{c.number}</code>",
        f"Sana: {fmt_date(c.issued_at)}",
        f"Vaqt: {fmt_time(c.issued_at)}",
        f"Telegram ID: <code>{c.telegram_id}</code>",
    ]
    return "\n".join(lines)


def debounced(c: "Contract") -> str:
    return "Bu raqam sizga hozirgina berilgan, yangi raqam ajratilmadi.\n\n" + issue_result(c)


def contract_line(c: "Contract", *, with_recipient: bool = True, with_date: bool = True) -> str:
    when = f"{fmt_date(c.issued_at)} {fmt_time(c.issued_at)}" if with_date else fmt_time(c.issued_at)
    line = f"<code>{c.number}</code> — {when}"
    if with_recipient:
        who = ADMIN_RECIPIENT if c.is_admin else f"{c.recipient_name} ({c.manager_code})"
        line += f" — {escape(who)}"
    return line


def my_history(items: list["Contract"]) -> str:
    if not items:
        return MY_EMPTY
    return "Oxirgi raqamlaringiz:\n" + "\n".join(contract_line(c, with_recipient=False) for c in items)


def registration_request(r: "Registration") -> str:
    username = f"@{escape(r.tg_username)}" if r.tg_username else "yo‘q"
    return (
        f"Ro‘yxatdan o‘tish arizasi #{r.id}\n"
        f"Profil: {r.manager_code} — {escape(r.manager_name)}\n"
        f"Telegram ID: <code>{r.telegram_id}</code>\n"
        f"Telegram ismi: {escape(r.tg_full_name or '-')}\n"
        f"Username: {username}"
    )


def manager_card(m: "Manager") -> str:
    if m.telegram_id is None:
        state = "bo‘sh (Telegram ID bog‘lanmagan)"
    elif m.is_blocked:
        state = "bloklangan"
    else:
        state = "faol"
    tg = f"<code>{m.telegram_id}</code>" if m.telegram_id else "-"
    return (
        f"Menejer: {escape(m.name)}\n"
        f"Menejer kodi: {m.code}\n"
        f"Telegram ID: {tg}\n"
        f"Holat: {state}"
    )
