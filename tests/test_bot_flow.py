"""Bot oqimi testlari: update'lar haqiqiy aiogram Dispatcher orqali o'tadi,
Telegram API o'rniga FakeSession ishlaydi."""

from __future__ import annotations

import asyncio
import csv
import io

from app import texts
from app.managers import MANAGERS

from .conftest import ADMIN2_ID, ADMIN_ID, Harness

USER = 5001
USER2 = 5002


def result_numbers(h: Harness, chat_id: int) -> list[str]:
    return [t for t in h.session.texts_to(chat_id) if t.startswith("Dogovor raqami:")]


async def register_and_approve(h: Harness, uid: int, code: int) -> None:
    await h.send(uid, "/start")
    await h.press(uid, f"reg:{code}")
    reg = [r for r in h.db.pending_registrations() if r.telegram_id == uid][0]
    await h.press(ADMIN_ID, f"rega:{reg.id}")
    await h.flush()


# ------------------------------------------------------------------ ro'yxatdan o'tish


async def test_registration_requires_admin_approval(h: Harness):
    await h.send(USER, "/start")
    assert texts.WELCOME_CHOOSE in h.session.texts_to(USER)

    await h.press(USER, "reg:1")
    await h.flush()
    # Ikkala adminga ariza bordi, Telegram ID bilan.
    for admin in (ADMIN_ID, ADMIN2_ID):
        assert any("Ro‘yxatdan o‘tish arizasi" in t and str(USER) in t for t in h.session.texts_to(admin))

    # Tasdiqlanmaguncha raqam yo'q.
    await h.send(USER, texts.BTN_GET)
    assert texts.NOT_REGISTERED in h.session.texts_to(USER)
    assert h.db.counter_value() == 2729

    reg = h.db.pending_registrations()[0]
    await h.press(ADMIN_ID, f"rega:{reg.id}")
    await h.flush()
    assert any("tasdiqlandi" in t for t in h.session.texts_to(USER))

    # Qayta /start — avtomatik taniladi.
    h.session.clear()
    await h.send(USER, "/start")
    assert h.session.texts_to(USER) == [texts.welcome_manager(h.db.get_manager(1))]

    await h.send(USER, texts.BTN_GET)
    await h.flush()
    [msg] = result_numbers(h, USER)
    assert msg == (
        "Dogovor raqami: <code>2730</code>\nMenejer: Sanjar\nMenejer kodi: 1\n"
        "Sana: 06.11.2026\nVaqt: 14:00:00"
    )
    for admin in (ADMIN_ID, ADMIN2_ID):
        notes = [t for t in h.session.texts_to(admin) if t.startswith("Yangi dogovor raqami olindi")]
        assert notes == [
            "Yangi dogovor raqami olindi\nMenejer: Sanjar\nMenejer kodi: 1\n"
            "Dogovor raqami: <code>2730</code>\nSana: 06.11.2026\nVaqt: 14:00:00\n"
            f"Telegram ID: <code>{USER}</code>"
        ]


async def test_taken_profile_cannot_be_claimed(h: Harness):
    await register_and_approve(h, USER, 1)
    await h.send(USER2, "/start")
    offered = [b.callback_data for row in h.session.requests[-1].reply_markup.inline_keyboard for b in row]
    assert "reg:1" not in offered and len(offered) == 17  # band profil ro'yxatda yo'q
    await h.press(USER2, "reg:1")  # qo'lda yuborilgan callback
    assert h.db.pending_registrations() == []
    assert h.db.get_manager(1).telegram_id == USER


async def test_two_pending_requests_for_same_profile_only_first_wins(h: Harness):
    await h.send(USER, "/start")
    await h.press(USER, "reg:3")
    await h.send(USER2, "/start")
    await h.press(USER2, "reg:3")
    regs = h.db.pending_registrations()
    assert len(regs) == 2
    await h.press(ADMIN_ID, f"rega:{regs[0].id}")
    await h.press(ADMIN2_ID, f"rega:{regs[1].id}")
    assert h.db.get_manager(3).telegram_id == USER
    assert h.db.user_info(USER2).manager is None


# ------------------------------------------------------------------ raqam olish


async def test_manager_and_admin_share_sequence_via_bot(h: Harness, clock):
    h.bind(1, USER)
    h.bind(2, USER2)
    await h.send(USER, texts.BTN_GET)
    await h.send(USER2, texts.BTN_GET)
    await h.send(ADMIN_ID, texts.BTN_ADMIN_GET)
    await h.flush()
    clock.advance(10)
    await h.send(USER, texts.BTN_GET)
    await h.flush()

    numbers = [c.number for c in h.db.all_contracts()]
    assert numbers == [2730, 2731, 2732, 2733]
    admin_c = h.db.all_contracts()[2]
    assert admin_c.is_admin and admin_c.recipient_name == "Admin — o‘zimiz uchun" and admin_c.telegram_id == ADMIN_ID

    # Admin o'zi olgan raqam: unga faqat bitta natija xabari, ikkinchi adminga bildirishnoma.
    admin_msgs = [t for t in h.session.texts_to(ADMIN_ID) if "2732" in t]
    assert admin_msgs == [
        "Dogovor raqami: <code>2732</code>\nOluvchi: Admin — o‘zimiz uchun\nSana: 06.11.2026\nVaqt: 14:00:00"
    ]
    assert any(t.startswith("Yangi dogovor raqami olindi") and "2732" in t for t in h.session.texts_to(ADMIN2_ID))


async def test_19_parallel_requests_through_dispatcher(h: Harness):
    for code, _ in MANAGERS:
        h.bind(code, 3000 + code)
    updates = [h.text_update(3000 + code, texts.BTN_GET) for code, _ in MANAGERS]
    updates.append(h.text_update(ADMIN_ID, texts.BTN_ADMIN_GET))
    await asyncio.gather(*(h.feed(u) for u in updates))
    await h.flush()

    contracts = h.db.all_contracts()
    assert sorted(c.number for c in contracts) == list(range(2730, 2749))
    assert len({c.telegram_id for c in contracts}) == 19
    # Har bir oluvchiga aynan bitta natija.
    for code, _ in MANAGERS:
        assert len(result_numbers(h, 3000 + code)) == 1


async def test_replayed_update_does_not_create_new_number(h: Harness):
    h.bind(1, USER)
    upd = h.text_update(USER, texts.BTN_GET)
    await h.feed(upd)
    await h.feed(upd)  # Telegram aynan shu update'ni qayta yubordi
    await h.flush()
    await h.feed(upd)
    await h.flush()
    assert [c.number for c in h.db.all_contracts()] == [2730]
    assert len(result_numbers(h, USER)) == 1


async def test_rapid_double_press_returns_last_number(h: Harness, clock):
    h.bind(1, USER)
    # Ikki bosish parallel keladi (natija hali yuborilmagan).
    await asyncio.gather(
        h.feed(h.text_update(USER, texts.BTN_GET)), h.feed(h.text_update(USER, texts.BTN_GET))
    )
    await h.flush()
    assert len(result_numbers(h, USER)) == 1
    # Natija yetkazilgandan keyin, 5 soniya ichida yana bosildi.
    clock.advance(2)
    await h.send(USER, texts.BTN_GET)
    await h.flush()
    assert [c.number for c in h.db.all_contracts()] == [2730]
    assert any(t.startswith("Bu raqam sizga hozirgina berilgan") and "2730" in t for t in h.session.texts_to(USER))
    # 5 soniyadan keyin — navbatdagi raqam.
    clock.advance(4)
    await h.send(USER, texts.BTN_GET)
    await h.flush()
    assert [c.number for c in h.db.all_contracts()] == [2730, 2731]


async def test_telegram_failure_does_not_reallocate_number(h: Harness, clock):
    h.bind(1, USER)
    h.session.fail_send = True
    await h.send(USER, texts.BTN_GET)
    await h.flush()
    assert result_numbers(h, USER) == []
    row = h.db.outbox_status("result:2730")
    assert row["status"] == "pending" and row["attempts"] == 1
    assert h.db.outbox_status(f"notify:2730:{ADMIN_ID}")["status"] == "pending"

    # Menejer javob olmadi va yana bosdi — yangi raqam ajratilmaydi.
    clock.advance(30)
    await h.send(USER, texts.BTN_GET)
    assert h.db.counter_value() == 2730

    # Aloqa tiklandi — aynan 2730 yetkaziladi, bir marta.
    h.session.fail_send = False
    await h.flush()
    assert result_numbers(h, USER) == [
        "Dogovor raqami: <code>2730</code>\nMenejer: Sanjar\nMenejer kodi: 1\nSana: 06.11.2026\nVaqt: 14:00:00"
    ]
    assert h.db.outbox_status("result:2730")["status"] == "sent"
    # Admin bildirishnomasi alohida holat bilan yetkazildi.
    assert h.db.outbox_status(f"notify:2730:{ADMIN_ID}")["status"] == "sent"

    clock.advance(10)
    await h.send(USER, texts.BTN_GET)
    await h.flush()
    assert [c.number for c in h.db.all_contracts()] == [2730, 2731]


async def test_outbox_gives_up_after_limit_and_user_can_trigger_redelivery(h: Harness, clock):
    h.bind(1, USER)
    h.session.fail_send = True
    await h.send(USER, texts.BTN_GET)
    for _ in range(5):
        await h.flush()
        clock.advance(700)
    row = h.db.outbox_status("result:2730")
    assert row["status"] == "failed" and row["attempts"] == h.settings.outbox_max_attempts
    with h.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'delivery_failed'").fetchone()[0] >= 1

    h.session.fail_send = False
    await h.send(USER, texts.BTN_GET)
    await h.flush()
    assert h.db.counter_value() == 2730
    assert len(result_numbers(h, USER)) == 1 and "2730" in result_numbers(h, USER)[0]


async def test_my_numbers_shows_only_own_last_10(h: Harness, clock):
    h.bind(1, USER)
    h.bind(2, USER2)
    for _ in range(12):
        await h.send(USER, texts.BTN_GET)
        await h.send(USER2, texts.BTN_GET)
        await h.flush()
        clock.advance(6)
    h.session.clear()
    await h.send(USER, texts.BTN_MY)
    [msg] = h.session.texts_to(USER)
    mine = [c.number for c in h.db.manager_history(1, 10)]
    others = [c.number for c in h.db.manager_history(2, 100)]
    assert msg.count("<code>") == 10
    assert all(f"<code>{n}</code>" in msg for n in mine)
    assert not any(f"<code>{n}</code>" in msg for n in others)


# ------------------------------------------------------------------ huquqlar


async def test_stranger_and_blocked_cannot_get_number(h: Harness):
    await h.send(9999, texts.BTN_GET)
    await h.send(9999, texts.BTN_ADMIN_GET)
    h.bind(1, USER)
    await h.press(ADMIN_ID, "blk:1")
    await h.send(USER, texts.BTN_GET)
    assert texts.BLOCKED in h.session.texts_to(USER)
    assert h.db.counter_value() == 2729

    await h.press(ADMIN_ID, "unb:1")
    await h.send(USER, texts.BTN_GET)
    assert h.db.counter_value() == 2730


async def test_manager_cannot_perform_admin_actions(h: Harness):
    h.bind(1, USER)
    await h.send(USER2, "/start")
    await h.press(USER2, "reg:2")
    reg = h.db.pending_registrations()[0]
    h.session.clear()

    for text in (texts.BTN_ADMIN_GET, texts.BTN_CSV, texts.BTN_TODAY, texts.BTN_LAST, texts.BTN_REGS, texts.BTN_MANAGERS):
        await h.send(USER, text)
    for data in (f"rega:{reg.id}", "blk:1", "unb:1", "chid:1", "hist:1", "hist:admin", "mgr:1"):
        await h.press(USER, data)
    await h.send(USER, "7777")  # chid holati o'rnatilmagan bo'lishi kerak

    assert h.db.counter_value() == 2729
    assert h.db.pending_registrations()[0].status == "pending"
    assert h.db.get_manager(1).telegram_id == USER and not h.db.get_manager(1).is_blocked
    assert h.session.documents_to(USER) == []
    assert all(t in (texts.UNKNOWN,) for t in h.session.texts_to(USER))


async def test_group_chat_is_ignored(h: Harness):
    h.bind(1, USER)
    await h.feed(h.text_update(USER, texts.BTN_GET, chat_type="group", chat_id=-100))
    assert h.db.counter_value() == 2729
    assert h.session.requests == []


# ------------------------------------------------------------------ admin paneli


async def test_admin_change_telegram_id(h: Harness):
    h.bind(1, USER)
    h.bind(2, USER2)
    await h.press(ADMIN_ID, "chid:1")
    await h.send(ADMIN_ID, str(USER2))  # boshqa profilda band
    assert h.db.get_manager(1).telegram_id == USER
    await h.send(ADMIN_ID, "7777")
    assert h.db.get_manager(1).telegram_id == 7777

    await h.send(USER, texts.BTN_GET)  # eski ID endi ruxsatsiz
    assert h.db.counter_value() == 2729
    await h.send(7777, texts.BTN_GET)
    assert h.db.counter_value() == 2730
    with h.db.read() as c:
        row = c.execute("SELECT * FROM audit_log WHERE action = 'telegram_id_changed' ORDER BY id DESC").fetchone()
    assert f"old={USER}" in row["details"] and "new=7777" in row["details"]


async def test_admin_reports_and_csv(h: Harness, clock):
    h.bind(1, USER)
    await h.send(USER, texts.BTN_GET)
    await h.send(ADMIN_ID, texts.BTN_ADMIN_GET)
    await h.flush()
    h.session.clear()

    await h.send(ADMIN_ID, texts.BTN_LAST)
    await h.send(ADMIN_ID, texts.BTN_TODAY)
    await h.press(ADMIN_ID, "hist:1")
    await h.press(ADMIN_ID, "hist:admin")
    last, today, hist1, hist_admin = h.session.texts_to(ADMIN_ID)
    assert "<code>2731</code>" in last and "Admin — o‘zimiz uchun" in last
    assert "2 ta" in today and "<code>2730</code>" in today and "<code>2731</code>" in today
    assert "<code>2730</code>" in hist1 and "2731" not in hist1
    assert "<code>2731</code>" in hist_admin

    await h.send(ADMIN_ID, texts.BTN_CSV)
    [doc] = h.session.documents_to(ADMIN_ID)
    content = doc.document.data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0][0] == "Dogovor raqami"
    assert rows[1] == ["2730", "06.11.2026", "14:00:00", "Sanjar", "1", str(USER)]
    assert rows[2] == ["2731", "06.11.2026", "14:00:00", "Admin — o‘zimiz uchun", "", str(ADMIN_ID)]
