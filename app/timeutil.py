"""Vaqt bilan ishlash. Bazada hamma vaqt UTC ISO-8601 ko'rinishida saqlanadi,
foydalanuvchiga esa Asia/Tashkent bo'yicha ko'rsatiladi."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    # Bir xil format (UTC, mikrosoniya) — satrlarni leksik solishtirish to'g'ri ishlaydi.
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def fmt_date(dt: datetime) -> str:
    return local(dt).strftime("%d.%m.%Y")


def fmt_time(dt: datetime) -> str:
    return local(dt).strftime("%H:%M:%S")


def local_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    """Toshkent bo'yicha bugungi kunning [boshlanishi, oxiri) chegaralari (UTC ISO)."""
    now = now or utcnow()
    start = local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return to_iso(start), to_iso(end)
