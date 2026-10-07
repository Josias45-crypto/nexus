"""Hora local de NEXUS (desfase fijo NEXUS_TZ_OFFSET, sin horario de verano)."""

from datetime import datetime, timedelta, timezone

from config import settings

LOCAL_TZ = timezone(timedelta(hours=settings.TZ_OFFSET))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Hora local sin zona (naive), como la usa el intérprete de fechas."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def local_to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)


def utc_to_local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(LOCAL_TZ).replace(tzinfo=None)
