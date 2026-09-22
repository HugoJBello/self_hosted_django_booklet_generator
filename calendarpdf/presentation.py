"""Shared display formatting for timetable entries."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .services import Event


def format_clock(value: str) -> str:
    """Render a normalized OCR time compactly while preserving unusual values."""
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError):
        return value.strip() if value else "?"
    return f"{hour}h" if minute == 0 else f"{hour}h{minute:02d}"


def format_period(event: Event) -> str:
    """Return the complete class period, marking an unreadable end explicitly."""
    return format_period_values(event.time, event.end_time)


def format_period_values(start_time: str, end_time: str = "") -> str:
    """Return a period when an Event object is not available."""
    start = format_clock(start_time)
    end = format_clock(end_time) if end_time else "?"
    return f"{start} → {end}"


def format_dates(days: Iterable[date]) -> str:
    """List every occurrence date in a stable, unambiguous form."""
    return ", ".join(day.strftime("%d %b %Y") for day in sorted(set(days)))
