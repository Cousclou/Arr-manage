"""Fuseau horaire configurable pour l'application."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Paris"

_tz_var: ContextVar[ZoneInfo] = ContextVar("app_timezone", default=ZoneInfo(DEFAULT_TIMEZONE))


def normalize_timezone_name(value: str | None) -> str:
    raw = (value or DEFAULT_TIMEZONE).strip().replace("\\", "/")
    return raw or DEFAULT_TIMEZONE


def resolve_timezone(value: str | None) -> ZoneInfo:
    name = normalize_timezone_name(value)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def set_request_timezone(tz: ZoneInfo) -> None:
    _tz_var.set(tz)


def get_request_timezone() -> ZoneInfo:
    return _tz_var.get()


def now_local(tz: ZoneInfo | None = None) -> datetime:
    zone = tz or get_request_timezone()
    return datetime.now(timezone.utc).astimezone(zone)


def format_local(
    dt: datetime | None,
    fmt: str = "%d/%m/%Y %H:%M",
    tz: ZoneInfo | None = None,
) -> str:
    if not dt:
        return "-"
    zone = tz or get_request_timezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone).strftime(fmt)
