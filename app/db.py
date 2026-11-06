"""SQLite ma'lumotlar bazasi va barcha yozish amallari.

Asosiy kafolatlar:
* Raqam ajratish, egasini yozish va yuboriladigan xabarlarni navbatga qo'yish
  BITTA tranzaksiyada (BEGIN IMMEDIATE) bajariladi. Xato bo'lsa — hammasi rollback.
* contracts.number va contracts.request_key — UNIQUE.
* Hisoblagich faqat oshadi (trigger), contracts jadvalidan o'chirish taqiqlangan (trigger).
* Boshlang'ich qiymat faqat counter qatori umuman yo'q bo'lganda (yangi baza) qo'yiladi.

Har bir amal o'z ulanishini ochadi, shuning uchun metodlarni asyncio.to_thread orqali
parallel chaqirish xavfsiz: SQLite yozish blokirovkasi va busy_timeout ularni navbatga qo'yadi.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from . import texts
from .timeutil import parse_iso, to_iso, utcnow

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counter (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_number INTEGER NOT NULL CHECK (last_number >= 0)
);

CREATE TABLE IF NOT EXISTS managers (
    code        INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    telegram_id INTEGER UNIQUE,
    is_blocked  INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0, 1)),
    bound_at    TEXT
);

CREATE TABLE IF NOT EXISTS registrations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id  INTEGER NOT NULL,
    manager_code INTEGER NOT NULL REFERENCES managers(code),
    tg_full_name TEXT,
    tg_username  TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at   TEXT    NOT NULL,
    decided_at   TEXT,
    decided_by   INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_registrations_one_pending
    ON registrations(telegram_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS contracts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    number         INTEGER NOT NULL UNIQUE,
    issued_at      TEXT    NOT NULL,
    telegram_id    INTEGER NOT NULL,
    manager_code   INTEGER REFERENCES managers(code),
    recipient_name TEXT    NOT NULL,
    is_admin       INTEGER NOT NULL CHECK (is_admin IN (0, 1)),
    request_key    TEXT    NOT NULL UNIQUE,
    CHECK ((is_admin = 1 AND manager_code IS NULL) OR (is_admin = 0 AND manager_code IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_contracts_tg      ON contracts(telegram_id, is_admin, id);
CREATE INDEX IF NOT EXISTS ix_contracts_manager ON contracts(manager_code, id);
CREATE INDEX IF NOT EXISTS ix_contracts_issued  ON contracts(issued_at);

CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT    NOT NULL UNIQUE,
    kind            TEXT    NOT NULL,
    contract_number INTEGER REFERENCES contracts(number),
    chat_id         INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    markup          TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT    NOT NULL,
    last_error      TEXT,
    created_at      TEXT    NOT NULL,
    sent_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_due ON outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT    NOT NULL,
    actor_id INTEGER,
    action   TEXT    NOT NULL,
    details  TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_counter_no_decrease
BEFORE UPDATE ON counter
WHEN NEW.last_number < OLD.last_number
BEGIN
    SELECT RAISE(ABORT, 'counter cannot decrease');
END;

CREATE TRIGGER IF NOT EXISTS trg_contracts_no_delete
BEFORE DELETE ON contracts
BEGIN
    SELECT RAISE(ABORT, 'contracts cannot be deleted');
END;
"""


# ---------------------------------------------------------------- modellar


@dataclass(frozen=True)
class Contract:
    number: int
    issued_at: datetime
    telegram_id: int
    manager_code: Optional[int]
    recipient_name: str
    is_admin: bool

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Contract":
        return cls(
            number=r["number"],
            issued_at=parse_iso(r["issued_at"]),
            telegram_id=r["telegram_id"],
            manager_code=r["manager_code"],
            recipient_name=r["recipient_name"],
            is_admin=bool(r["is_admin"]),
        )


@dataclass(frozen=True)
class Manager:
    code: int
    name: str
    telegram_id: Optional[int]
    is_blocked: bool

    @property
    def is_free(self) -> bool:
        return self.telegram_id is None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Manager":
        return cls(r["code"], r["name"], r["telegram_id"], bool(r["is_blocked"]))


@dataclass(frozen=True)
class Registration:
    id: int
    telegram_id: int
    manager_code: int
    manager_name: str
    tg_full_name: Optional[str]
    tg_username: Optional[str]
    status: str

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Registration":
        return cls(
            r["id"], r["telegram_id"], r["manager_code"], r["manager_name"],
            r["tg_full_name"], r["tg_username"], r["status"],
        )


@dataclass(frozen=True)
class IssueResult:
    """status: new | duplicate_update | redeliver | debounced | forbidden"""

    status: str
    contract: Optional[Contract] = None
    reason: Optional[str] = None  # forbidden uchun: not_registered | blocked | not_admin


@dataclass(frozen=True)
class UserInfo:
    manager: Optional[Manager]
    pending: Optional[Registration]


@dataclass(frozen=True)
class OutboxItem:
    id: int
    chat_id: int
    text: str
    markup: Optional[str]
    kind: str
    attempts: int


def backoff_seconds(attempt: int) -> float:
    """1-urinishdan keyin 5s, keyin 10, 20, 40 ... maksimal 10 daqiqa."""
    return float(min(5 * 2 ** max(attempt - 1, 0), 600))


_REG_SELECT = """
SELECT r.*, m.name AS manager_name
FROM registrations r JOIN managers m ON m.code = r.manager_code
"""


class Database:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 15000) -> None:
        self.path = str(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.clock: Callable[[], datetime] = utcnow  # testlarda almashtiriladi

    # ------------------------------------------------------------ ulanish

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,  # tranzaksiyalarni o'zimiz boshqaramiz
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Yozish tranzaksiyasi. BEGIN IMMEDIATE darhol yozish blokirovkasini oladi,
        shuning uchun bir vaqtda faqat bitta tranzaksiya hisoblagichni o'qib-yangilaydi."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ yaratish

    def init(self, managers: Iterable[tuple[int, str]], *, initial_last_number: int) -> None:
        """Sxemani yaratadi. Boshlang'ich raqam faqat yangi (counter qatori yo'q) bazada qo'llanadi;
        restart yoki yangilanishda hisoblagich hech qachon qayta o'rnatilmaydi."""
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
        finally:
            conn.close()
        now = to_iso(self.clock())
        with self.tx() as c:
            created = c.execute(
                "INSERT OR IGNORE INTO counter (id, last_number) "
                "SELECT 1, COALESCE(MAX(number), ?) FROM contracts",
                (initial_last_number,),
            ).rowcount
            c.executemany(
                "INSERT OR IGNORE INTO managers (code, name) VALUES (?, ?)", list(managers)
            )
            c.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)", (now,))
            if created:
                self._audit(c, None, "db_created", f"initial_last_number={initial_last_number}")

    # ------------------------------------------------------------ yordamchi

    def _audit(self, c: sqlite3.Connection, actor_id: Optional[int], action: str, details: str = "") -> None:
        c.execute(
            "INSERT INTO audit_log (at, actor_id, action, details) VALUES (?, ?, ?, ?)",
            (to_iso(self.clock()), actor_id, action, details[:1000]),
        )

    def audit(self, actor_id: Optional[int], action: str, details: str = "") -> None:
        with self.tx() as c:
            self._audit(c, actor_id, action, details)

    def _enqueue(
        self, c: sqlite3.Connection, *, dedup_key: str, kind: str, chat_id: int, text: str,
        markup: Optional[str] = None, contract_number: Optional[int] = None,
    ) -> None:
        now = to_iso(self.clock())
        c.execute(
            "INSERT OR IGNORE INTO outbox (dedup_key, kind, contract_number, chat_id, text, markup,"
            " next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dedup_key, kind, contract_number, chat_id, text, markup, now, now),
        )

    # ------------------------------------------------------------ raqam ajratish

    def issue_contract(
        self,
        *,
        telegram_id: int,
        request_key: str,
        as_admin: bool,
        admin_ids: Iterable[int],
        debounce_seconds: float = 5.0,
        now: Optional[datetime] = None,
    ) -> IssueResult:
        admin_ids = frozenset(admin_ids)
        now = now or self.clock()
        with self.tx() as c:
            # 1) Aynan shu Telegram update qayta kelgan bo'lsa — mavjud natija.
            row = c.execute("SELECT * FROM contracts WHERE request_key = ?", (request_key,)).fetchone()
            if row:
                return IssueResult("duplicate_update", Contract.from_row(row))

            # 2) Huquqni tranzaksiya ichida tekshirish.
            if as_admin:
                if telegram_id not in admin_ids:
                    return IssueResult("forbidden", reason="not_admin")
                manager_code, name = None, texts.ADMIN_RECIPIENT
            else:
                m = c.execute(
                    "SELECT code, name, is_blocked FROM managers WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
                if m is None:
                    return IssueResult("forbidden", reason="not_registered")
                if m["is_blocked"]:
                    return IssueResult("forbidden", reason="blocked")
                manager_code, name = m["code"], m["name"]

            # 3) Oxirgi raqam hali yetkazilmagan bo'lsa — yangi raqam emas, o'shani qayta yuborish.
            #    5 soniya ichidagi takroriy bosish — oxirgi natijani qaytarish.
            last = c.execute(
                "SELECT * FROM contracts WHERE telegram_id = ? AND is_admin = ? ORDER BY id DESC LIMIT 1",
                (telegram_id, int(as_admin)),
            ).fetchone()
            if last is not None:
                last_c = Contract.from_row(last)
                res = c.execute(
                    "SELECT id, status FROM outbox WHERE dedup_key = ?", (f"result:{last_c.number}",)
                ).fetchone()
                if res is not None and res["status"] != "sent":
                    c.execute(
                        "UPDATE outbox SET status = 'pending', next_attempt_at = ?,"
                        " attempts = CASE WHEN status = 'failed' THEN 0 ELSE attempts END WHERE id = ?",
                        (to_iso(now), res["id"]),
                    )
                    return IssueResult("redeliver", last_c)
                if 0 <= (now - last_c.issued_at).total_seconds() < debounce_seconds:
                    return IssueResult("debounced", last_c)

            # 4) Umumiy hisoblagichdan keyingi raqam.
            c.execute("UPDATE counter SET last_number = last_number + 1 WHERE id = 1")
            number = c.execute("SELECT last_number FROM counter WHERE id = 1").fetchone()[0]
            c.execute(
                "INSERT INTO contracts (number, issued_at, telegram_id, manager_code, recipient_name,"
                " is_admin, request_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (number, to_iso(now), telegram_id, manager_code, name, int(as_admin), request_key),
            )
            contract = Contract(number, parse_iso(to_iso(now)), telegram_id, manager_code, name, as_admin)

            # 5) Xabarlarni o'sha tranzaksiyada navbatga qo'yish (avval saqlash, keyin yuborish).
            self._enqueue(
                c, dedup_key=f"result:{number}", kind="result", chat_id=telegram_id,
                text=texts.issue_result(contract), contract_number=number,
            )
            for admin_id in sorted(admin_ids):
                if admin_id == telegram_id:
                    continue  # oluvchi adminga natija yetarli, bir xil ikkinchi xabar kerak emas
                self._enqueue(
                    c, dedup_key=f"notify:{number}:{admin_id}", kind="admin_notify", chat_id=admin_id,
                    text=texts.admin_notify(contract), contract_number=number,
                )
            return IssueResult("new", contract)

    # ------------------------------------------------------------ foydalanuvchilar

    def user_info(self, telegram_id: int) -> UserInfo:
        with self.read() as c:
            m = c.execute("SELECT * FROM managers WHERE telegram_id = ?", (telegram_id,)).fetchone()
            r = c.execute(
                _REG_SELECT + " WHERE r.telegram_id = ? AND r.status = 'pending'", (telegram_id,)
            ).fetchone()
        return UserInfo(Manager.from_row(m) if m else None, Registration.from_row(r) if r else None)

    def get_manager(self, code: int) -> Optional[Manager]:
        with self.read() as c:
            r = c.execute("SELECT * FROM managers WHERE code = ?", (code,)).fetchone()
        return Manager.from_row(r) if r else None

    def list_managers(self) -> list[Manager]:
        with self.read() as c:
            return [Manager.from_row(r) for r in c.execute("SELECT * FROM managers ORDER BY code")]

    def free_managers(self) -> list[Manager]:
        return [m for m in self.list_managers() if m.is_free]

    def create_registration(
        self, *, telegram_id: int, manager_code: int, full_name: Optional[str],
        username: Optional[str], admin_ids: Iterable[int],
    ) -> tuple[str, Optional[Registration]]:
        """status: created | already_bound | pending_exists | taken | not_found"""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM managers WHERE telegram_id = ?", (telegram_id,)).fetchone():
                return "already_bound", None
            pending = c.execute(
                _REG_SELECT + " WHERE r.telegram_id = ? AND r.status = 'pending'", (telegram_id,)
            ).fetchone()
            if pending:
                return "pending_exists", Registration.from_row(pending)
            m = c.execute("SELECT * FROM managers WHERE code = ?", (manager_code,)).fetchone()
            if m is None:
                return "not_found", None
            if m["telegram_id"] is not None:
                return "taken", None
            cur = c.execute(
                "INSERT INTO registrations (telegram_id, manager_code, tg_full_name, tg_username, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (telegram_id, manager_code, full_name, username, to_iso(self.clock())),
            )
            reg = Registration.from_row(
                c.execute(_REG_SELECT + " WHERE r.id = ?", (cur.lastrowid,)).fetchone()
            )
            for admin_id in sorted(frozenset(admin_ids)):
                self._enqueue(
                    c, dedup_key=f"regreq:{reg.id}:{admin_id}", kind="reg_request", chat_id=admin_id,
                    text=texts.registration_request(reg), markup=f"reg:{reg.id}",
                )
            self._audit(c, telegram_id, "registration_request", f"reg={reg.id} code={manager_code}")
            return "created", reg

    def pending_registrations(self) -> list[Registration]:
        with self.read() as c:
            rows = c.execute(_REG_SELECT + " WHERE r.status = 'pending' ORDER BY r.id").fetchall()
        return [Registration.from_row(r) for r in rows]

    def decide_registration(
        self, *, reg_id: int, approve: bool, admin_id: int
    ) -> tuple[str, Optional[Registration]]:
        """status: approved | rejected | taken | user_bound | already_decided | not_found"""
        now = to_iso(self.clock())
        with self.tx() as c:
            row = c.execute(_REG_SELECT + " WHERE r.id = ?", (reg_id,)).fetchone()
            if row is None:
                return "not_found", None
            reg = Registration.from_row(row)
            if reg.status != "pending":
                return "already_decided", reg

            status = "approved" if approve else "rejected"
            if approve:
                m = c.execute("SELECT telegram_id FROM managers WHERE code = ?", (reg.manager_code,)).fetchone()
                if m["telegram_id"] is not None:
                    status = "taken"
                elif c.execute("SELECT 1 FROM managers WHERE telegram_id = ?", (reg.telegram_id,)).fetchone():
                    status = "user_bound"

            final = "approved" if status == "approved" else "rejected"
            c.execute(
                "UPDATE registrations SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                (final, now, admin_id, reg_id),
            )
            if status == "approved":
                c.execute(
                    "UPDATE managers SET telegram_id = ?, is_blocked = 0, bound_at = ? WHERE code = ?",
                    (reg.telegram_id, now, reg.manager_code),
                )
                manager = Manager(reg.manager_code, reg.manager_name, reg.telegram_id, False)
                self._enqueue(
                    c, dedup_key=f"regres:{reg_id}", kind="reg_result", chat_id=reg.telegram_id,
                    text=texts.reg_approved_user(manager), markup="manager_menu",
                )
            else:
                self._enqueue(
                    c, dedup_key=f"regres:{reg_id}", kind="reg_result", chat_id=reg.telegram_id,
                    text=texts.REG_REJECTED_USER,
                )
            self._audit(
                c, admin_id, f"registration_{status}",
                f"reg={reg_id} code={reg.manager_code} telegram_id={reg.telegram_id}",
            )
            return status, reg

    def set_blocked(self, *, code: int, blocked: bool, admin_id: int) -> Optional[Manager]:
        with self.tx() as c:
            r = c.execute("SELECT * FROM managers WHERE code = ?", (code,)).fetchone()
            if r is None:
                return None
            c.execute("UPDATE managers SET is_blocked = ? WHERE code = ?", (int(blocked), code))
            self._audit(
                c, admin_id, "manager_blocked" if blocked else "manager_unblocked",
                f"code={code} telegram_id={r['telegram_id']}",
            )
            r = c.execute("SELECT * FROM managers WHERE code = ?", (code,)).fetchone()
            return Manager.from_row(r)

    def change_telegram_id(
        self, *, code: int, new_telegram_id: Optional[int], admin_id: int
    ) -> tuple[str, Optional[Manager]]:
        """new_telegram_id=None — profilni bo'shatish. status: ok | not_found | id_in_use"""
        now = to_iso(self.clock())
        with self.tx() as c:
            r = c.execute("SELECT * FROM managers WHERE code = ?", (code,)).fetchone()
            if r is None:
                return "not_found", None
            if new_telegram_id is not None:
                other = c.execute(
                    "SELECT code FROM managers WHERE telegram_id = ? AND code != ?", (new_telegram_id, code)
                ).fetchone()
                if other:
                    return "id_in_use", Manager.from_row(r)
                c.execute(
                    "UPDATE managers SET telegram_id = ?, bound_at = ? WHERE code = ?",
                    (new_telegram_id, now, code),
                )
                # Yangi ID egasining kutilayotgan arizalari endi kerak emas.
                c.execute(
                    "UPDATE registrations SET status = 'rejected', decided_at = ?, decided_by = ?"
                    " WHERE telegram_id = ? AND status = 'pending'",
                    (now, admin_id, new_telegram_id),
                )
            else:
                c.execute(
                    "UPDATE managers SET telegram_id = NULL, is_blocked = 0, bound_at = NULL WHERE code = ?",
                    (code,),
                )
            self._audit(
                c, admin_id, "telegram_id_changed",
                f"code={code} old={r['telegram_id']} new={new_telegram_id}",
            )
            r = c.execute("SELECT * FROM managers WHERE code = ?", (code,)).fetchone()
            return "ok", Manager.from_row(r)

    # ------------------------------------------------------------ tarix

    def counter_value(self) -> int:
        with self.read() as c:
            return c.execute("SELECT last_number FROM counter WHERE id = 1").fetchone()[0]

    def last_contract(self) -> Optional[Contract]:
        with self.read() as c:
            r = c.execute("SELECT * FROM contracts ORDER BY id DESC LIMIT 1").fetchone()
        return Contract.from_row(r) if r else None

    def manager_history(self, code: int, limit: int = 10) -> list[Contract]:
        with self.read() as c:
            rows = c.execute(
                "SELECT * FROM contracts WHERE manager_code = ? ORDER BY id DESC LIMIT ?", (code, limit)
            ).fetchall()
        return [Contract.from_row(r) for r in rows]

    def admin_history(self, limit: int = 20) -> list[Contract]:
        with self.read() as c:
            rows = c.execute(
                "SELECT * FROM contracts WHERE is_admin = 1 ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Contract.from_row(r) for r in rows]

    def contracts_between(self, start_iso: str, end_iso: str) -> list[Contract]:
        with self.read() as c:
            rows = c.execute(
                "SELECT * FROM contracts WHERE issued_at >= ? AND issued_at < ? ORDER BY id",
                (start_iso, end_iso),
            ).fetchall()
        return [Contract.from_row(r) for r in rows]

    def all_contracts(self) -> list[Contract]:
        with self.read() as c:
            rows = c.execute("SELECT * FROM contracts ORDER BY id").fetchall()
        return [Contract.from_row(r) for r in rows]

    # ------------------------------------------------------------ outbox

    def outbox_due(self, limit: int = 30) -> list[OutboxItem]:
        with self.read() as c:
            rows = c.execute(
                "SELECT id, chat_id, text, markup, kind, attempts FROM outbox"
                " WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?",
                (to_iso(self.clock()), limit),
            ).fetchall()
        return [OutboxItem(r["id"], r["chat_id"], r["text"], r["markup"], r["kind"], r["attempts"]) for r in rows]

    def outbox_mark_sent(self, item_id: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE outbox SET status = 'sent', sent_at = ?, attempts = attempts + 1, last_error = NULL"
                " WHERE id = ?",
                (to_iso(self.clock()), item_id),
            )

    def outbox_mark_retry(
        self, item_id: int, error: str, *, permanent: bool, max_attempts: int,
        delay: Optional[float] = None,
    ) -> bool:
        """Muvaffaqiyatsiz urinishni yozadi. Urinishlar tugagan bo'lsa True qaytaradi."""
        now = self.clock()
        with self.tx() as c:
            r = c.execute("SELECT attempts, kind, chat_id FROM outbox WHERE id = ?", (item_id,)).fetchone()
            if r is None:
                return True
            attempts = r["attempts"] + 1
            if permanent or attempts >= max_attempts:
                c.execute(
                    "UPDATE outbox SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                    (attempts, error[:500], item_id),
                )
                self._audit(
                    c, None, "delivery_failed",
                    f"outbox={item_id} kind={r['kind']} chat={r['chat_id']} attempts={attempts} error={error[:300]}",
                )
                return True
            wait = delay if delay is not None else backoff_seconds(attempts)
            c.execute(
                "UPDATE outbox SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
                (attempts, error[:500], to_iso(now + timedelta(seconds=wait)), item_id),
            )
            return False

    def outbox_status(self, dedup_key: str) -> Optional[sqlite3.Row]:
        with self.read() as c:
            return c.execute("SELECT * FROM outbox WHERE dedup_key = ?", (dedup_key,)).fetchone()
