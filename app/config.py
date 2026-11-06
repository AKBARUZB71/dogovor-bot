"""Sozlamalar .env fayli yoki muhit o'zgaruvchilaridan o'qiladi. Token kodga yozilmaydi."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    admin_ids: frozenset[int]
    db_path: Path = Path("data/dogovor.db")
    initial_last_number: int = 2729
    debounce_seconds: float = 5.0
    outbox_max_attempts: int = 10
    log_level: str = "INFO"

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


def parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in re.split(r"[,\s;]+", raw.strip()):
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"ADMIN_IDS faqat raqamlardan iborat bo'lishi kerak: {part!r}")
        ids.add(int(part))
    return frozenset(ids)


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file)
    token = os.getenv("BOT_TOKEN", "").strip()
    if not _TOKEN_RE.match(token):
        raise SystemExit("BOT_TOKEN .env faylida ko'rsatilmagan yoki noto'g'ri formatda.")
    admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    if not admin_ids:
        raise SystemExit("ADMIN_IDS .env faylida ko'rsatilmagan.")
    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=Path(os.getenv("DB_PATH", "data/dogovor.db")),
        initial_last_number=int(os.getenv("INITIAL_LAST_NUMBER", "2729")),
        debounce_seconds=float(os.getenv("DEBOUNCE_SECONDS", "5")),
        outbox_max_attempts=int(os.getenv("OUTBOX_MAX_ATTEMPTS", "10")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
