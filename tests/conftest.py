"""Test infratuzilmasi: haqiqiy SQLite fayl, soxta soat va Telegram API o'rniga FakeSession.

FakeSession botning Telegram'ga yuborgan barcha so'rovlarini yozib boradi, kerak bo'lsa
tarmoq xatosini simulyatsiya qiladi. Update'lar haqiqiy aiogram Dispatcher orqali o'tadi.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot import build_dispatcher
from app.config import Settings
from app.db import Database
from app.managers import MANAGERS
from app.outbox import Outbox

ADMIN_ID = 1000
ADMIN2_ID = 1001


class FakeClock:
    def __init__(self) -> None:
        # 09:00:00 UTC = 14:00:00 Asia/Tashkent
        self.now = datetime(2026, 11, 6, 9, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.fail_send = False
        self._ids = itertools.count(1)

    async def make_request(self, bot, method, timeout=None):
        if self.fail_send and isinstance(method, SendMessage):
            raise TelegramNetworkError(method=method, message="simulated network failure")
        self.requests.append(method)
        if isinstance(method, (SendMessage, SendDocument)):
            return Message(
                message_id=next(self._ids),
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None),
            )
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        raise NotImplementedError
        yield b""  # pragma: no cover

    async def close(self) -> None:
        pass

    # yordamchilar
    def texts_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, SendMessage) and m.chat_id == chat_id]

    def documents_to(self, chat_id: int) -> list[SendDocument]:
        return [m for m in self.requests if isinstance(m, SendDocument) and m.chat_id == chat_id]

    def clear(self) -> None:
        self.requests.clear()


def _user(uid: int) -> User:
    return User(id=uid, is_bot=False, first_name=f"User{uid}", username=f"user{uid}")


class Harness:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.session = FakeSession()
        self.bot = Bot(
            "42:TEST", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.outbox = Outbox(db, self.bot, max_attempts=settings.outbox_max_attempts)
        self.dp = build_dispatcher(db, settings, self.outbox)
        self._update_ids = itertools.count(1)

    def text_update(self, uid: int, text: str, *, chat_type: str = "private", chat_id: int | None = None) -> Update:
        uid_ = next(self._update_ids)
        return Update(
            update_id=uid_,
            message=Message(
                message_id=uid_,
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id if chat_id is not None else uid, type=chat_type),
                from_user=_user(uid),
                text=text,
            ),
        )

    def callback_update(self, uid: int, data: str) -> Update:
        uid_ = next(self._update_ids)
        msg = Message(
            message_id=100_000 + uid_,
            date=datetime.now(timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=User(id=42, is_bot=True, first_name="bot"),
            text="...",
        )
        return Update(
            update_id=uid_,
            callback_query=CallbackQuery(
                id=f"cb{uid_}", from_user=_user(uid), chat_instance="ci", message=msg, data=data
            ),
        )

    async def feed(self, update: Update):
        return await self.dp.feed_update(self.bot, update)

    async def send(self, uid: int, text: str) -> Update:
        upd = self.text_update(uid, text)
        await self.feed(upd)
        return upd

    async def press(self, uid: int, data: str) -> Update:
        upd = self.callback_update(uid, data)
        await self.feed(upd)
        return upd

    async def flush(self) -> int:
        """Worker kabi: navbatdagi muddati kelgan hamma yozuvlarni (bir necha paket) yuboradi."""
        total = 0
        while True:
            n = await self.outbox.process_due()
            total += n
            if n < self.outbox.batch_size:
                return total

    def bind(self, code: int, telegram_id: int) -> None:
        status, _ = self.db.change_telegram_id(code=code, new_telegram_id=telegram_id, admin_id=ADMIN_ID)
        assert status == "ok"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "dogovor.db"


@pytest.fixture
def db(db_path, clock) -> Database:
    d = Database(db_path)
    d.clock = clock
    d.init(MANAGERS, initial_last_number=2729)
    return d


@pytest.fixture
def settings(db_path) -> Settings:
    return Settings(
        bot_token="42:TEST",
        admin_ids=frozenset({ADMIN_ID, ADMIN2_ID}),
        db_path=db_path,
        debounce_seconds=5.0,
        outbox_max_attempts=3,
    )


@pytest.fixture
def h(db, settings) -> Harness:
    return Harness(db, settings)


def mark_all_sent(db: Database) -> None:
    with db.tx() as c:
        c.execute("UPDATE outbox SET status = 'sent' WHERE status != 'sent'")
