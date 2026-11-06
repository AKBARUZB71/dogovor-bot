"""Baza darajasidagi testlar: ketma-ketlik, parallellik, rollback, restart, cheklovlar."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.db import Database
from app.managers import MANAGERS

from .conftest import ADMIN2_ID, ADMIN_ID, mark_all_sent

ADMINS = frozenset({ADMIN_ID, ADMIN2_ID})


def bind_all(db: Database) -> dict[int, int]:
    """18 menejerga 2001..2018 Telegram ID bog'laydi; {code: telegram_id}."""
    ids = {}
    for code, _ in MANAGERS:
        tg = 2000 + code
        assert db.change_telegram_id(code=code, new_telegram_id=tg, admin_id=ADMIN_ID)[0] == "ok"
        ids[code] = tg
    return ids


def issue(db: Database, tg: int, key: str, *, as_admin: bool = False):
    return db.issue_contract(telegram_id=tg, request_key=key, as_admin=as_admin, admin_ids=ADMINS)


def test_first_number_is_2730(db):
    bind_all(db)
    res = issue(db, 2001, "k1")
    assert res.status == "new"
    assert res.contract.number == 2730
    assert db.counter_value() == 2730


def test_managers_and_admin_share_one_sequence(db, clock):
    bind_all(db)
    sanjar, nodir = 2001, 2002
    got = []
    got.append(issue(db, sanjar, "k1").contract)
    got.append(issue(db, nodir, "k2").contract)
    got.append(issue(db, ADMIN_ID, "k3", as_admin=True).contract)
    mark_all_sent(db)
    clock.advance(10)
    got.append(issue(db, sanjar, "k4").contract)

    assert [c.number for c in got] == [2730, 2731, 2732, 2733]
    assert [c.recipient_name for c in got] == ["Sanjar", "Nodir", "Admin — o‘zimiz uchun", "Sanjar"]
    assert got[2].is_admin and got[2].manager_code is None and got[2].telegram_id == ADMIN_ID
    assert got[0].manager_code == 1 and got[1].manager_code == 2


def test_19_parallel_requests_get_unique_consecutive_numbers(db):
    ids = bind_all(db)
    barrier = threading.Barrier(19)
    results, errors = [], []

    def worker(tg: int, as_admin: bool, key: str) -> None:
        try:
            barrier.wait()
            # Har bir thread o'z ulanishi bilan — haqiqiy parallel yozish raqobati.
            results.append(issue(db, tg, key, as_admin=as_admin))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(tg, False, f"m{code}")) for code, tg in ids.items()]
    threads.append(threading.Thread(target=worker, args=(ADMIN_ID, True, "admin")))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    numbers = sorted(r.contract.number for r in results)
    assert all(r.status == "new" for r in results)
    assert numbers == list(range(2730, 2749))
    assert db.counter_value() == 2748
    assert len(db.all_contracts()) == 19


def test_same_request_key_does_not_create_new_number(db):
    bind_all(db)
    first = issue(db, 2001, "upd:777")
    again = issue(db, 2001, "upd:777")
    assert first.status == "new" and again.status == "duplicate_update"
    assert again.contract.number == first.contract.number == 2730
    assert db.counter_value() == 2730
    assert len(db.all_contracts()) == 1


def test_rapid_repeat_returns_last_number_then_allows_new(db, clock):
    bind_all(db)
    first = issue(db, 2001, "a")
    mark_all_sent(db)
    clock.advance(2)
    second = issue(db, 2001, "b")
    assert second.status == "debounced" and second.contract.number == 2730
    clock.advance(4)  # birinchisidan 6 soniya o'tdi
    third = issue(db, 2001, "c")
    assert third.status == "new" and third.contract.number == 2731
    assert first.contract.number == 2730


def test_restart_continues_and_initial_value_not_reapplied(db_path, clock):
    db1 = Database(db_path)
    db1.clock = clock
    db1.init(MANAGERS, initial_last_number=2729)
    bind_all(db1)
    assert issue(db1, 2001, "a").contract.number == 2730
    assert issue(db1, 2002, "b").contract.number == 2731

    # "Restart": yangi obyekt, yangi init — hatto boshqa boshlang'ich qiymat bilan ham.
    db2 = Database(db_path)
    db2.clock = clock
    db2.init(MANAGERS, initial_last_number=1)
    assert db2.counter_value() == 2731
    assert issue(db2, 2003, "c").contract.number == 2732
    assert [c.number for c in db2.all_contracts()] == [2730, 2731, 2732]


def test_transaction_failure_does_not_consume_number(db):
    bind_all(db)
    # Raqam ajratilib, contracts ga yozilgandan KEYIN (outbox bosqichida) xato chiqaramiz.
    with db.tx() as c:
        c.execute(
            "CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox "
            "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        issue(db, 2001, "x")
    assert db.counter_value() == 2729
    assert db.all_contracts() == []

    with db.tx() as c:
        c.execute("DROP TRIGGER fail_outbox")
    assert issue(db, 2001, "y").contract.number == 2730


def test_contract_number_is_unique_and_history_protected(db):
    bind_all(db)
    issue(db, 2001, "a")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute(
                "INSERT INTO contracts (number, issued_at, telegram_id, manager_code, recipient_name,"
                " is_admin, request_key) VALUES (2730, 'x', 1, 1, 'X', 0, 'other')"
            )
    with pytest.raises(sqlite3.DatabaseError):
        with db.tx() as c:
            c.execute("UPDATE counter SET last_number = 100 WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError):
        with db.tx() as c:
            c.execute("DELETE FROM contracts")
    assert db.counter_value() == 2730


def test_stranger_blocked_and_non_admin_are_forbidden(db):
    bind_all(db)
    assert issue(db, 999999, "a").status == "forbidden"
    assert issue(db, 999999, "a").reason == "not_registered"
    db.set_blocked(code=1, blocked=True, admin_id=ADMIN_ID)
    res = issue(db, 2001, "b")
    assert res.status == "forbidden" and res.reason == "blocked"
    res = issue(db, 2002, "c", as_admin=True)  # oddiy menejer admin sifatida
    assert res.status == "forbidden" and res.reason == "not_admin"
    assert db.counter_value() == 2729


def test_admin_notifications_not_duplicated_to_issuing_admin(db):
    res = issue(db, ADMIN_ID, "a", as_admin=True)
    n = res.contract.number
    assert db.outbox_status(f"result:{n}")["chat_id"] == ADMIN_ID
    assert db.outbox_status(f"notify:{n}:{ADMIN_ID}") is None
    assert db.outbox_status(f"notify:{n}:{ADMIN2_ID}") is not None


def test_one_telegram_id_per_profile(db):
    assert db.change_telegram_id(code=1, new_telegram_id=5000, admin_id=ADMIN_ID)[0] == "ok"
    assert db.change_telegram_id(code=2, new_telegram_id=5000, admin_id=ADMIN_ID)[0] == "id_in_use"
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("UPDATE managers SET telegram_id = 5000 WHERE code = 3")
