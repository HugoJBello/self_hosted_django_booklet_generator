"""A compact weekly view and hour totals for all uploaded timetables."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import fitz

if TYPE_CHECKING:
    from .services import Event


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CATEGORIES = ("Theory", "Classroom practice", "Laboratory", "Seminar", "Workshop", "Online", "Tutorial", "Exam", "Other")


def category(event: Event) -> str:
    group = event.group.upper()
    if event.kind:
        return event.kind
    if event.room.upper() == "ONLINE":
        return "Online"
    if "LAB" in event.room.upper() or group.endswith("L"):
        return "Laboratory"
    if group.endswith("T"):
        return "Theory"
    if group.endswith("A") or group.endswith("P"):
        return "Classroom practice"
    return "Other"


def duration_minutes(event: Event) -> int:
    """Use the printed end time, or one hour when OCR finds only a start."""
    try:
        start_h, start_m = map(int, event.time.split(":"))
        end_h, end_m = map(int, event.end_time.split(":"))
        minutes = (end_h * 60 + end_m) - (start_h * 60 + start_m)
        return minutes if 0 < minutes <= 8 * 60 else 60
    except (ValueError, AttributeError):
        return 60


def _start_minutes(event: Event) -> int:
    hour, minute = map(int, event.time.split(":"))
    return hour * 60 + minute


def overlapping_events(events: set[Event]) -> set[Event]:
    """Find dated sessions with a positive-length time intersection."""
    by_day: dict[date, list[Event]] = defaultdict(list)
    for event in events:
        by_day[event.day].append(event)
    overlaps = set()
    for daily in by_day.values():
        ordered = sorted(daily, key=_start_minutes)
        for index, event in enumerate(ordered):
            start = _start_minutes(event)
            end = start + duration_minutes(event)
            for other in ordered[index + 1:]:
                other_start = _start_minutes(other)
                if other_start >= end:
                    break
                if other_start + duration_minutes(other) > start:
                    overlaps.update((event, other))
    return overlaps


def hour_totals(events: set[Event]) -> dict[str, int]:
    totals = dict.fromkeys(CATEGORIES, 0)
    for event in events:
        label = category(event)
        totals[label] = totals.get(label, 0) + duration_minutes(event)
    return totals


def _hours(minutes: int) -> str:
    hours, remaining = divmod(minutes, 60)
    return f"{hours} h" + (f" {remaining} min" if remaining else "")


def _date_span(days: set[date]) -> str:
    ordered = sorted(days)
    first, last = ordered[0], ordered[-1]
    if first == last:
        return first.strftime("%d %b %Y")
    return f"{first:%d %b %Y} - {last:%d %b %Y}"


def _fit(page: fitz.Page, font: fitz.Font, label: str, x: float, y: float,
         width: float, size: float = 8, color=(0.18, 0.22, 0.28)) -> None:
    while label and font.text_length(label, fontsize=size) > width:
        label = label[:-1]
    page.insert_text((x, y), label, fontsize=size, fontname="dejavu", color=color)


def add_weekly_summary(doc: fitz.Document, events: set[Event], font_path: Path) -> None:
    """Append exactly one page, expanding its height when the timetable is dense."""
    grouped: dict[tuple, set[date]] = defaultdict(set)
    for event in events:
        key = (event.day.weekday(), event.time, event.end_time, event.subject, event.group, event.room, event.kind)
        grouped[key].add(event.day)

    by_slot: dict[str, dict[int, list[tuple[tuple, set[date]]]]] = defaultdict(lambda: defaultdict(list))
    for key, days in grouped.items():
        by_slot[key[1]][key[0]].append((key, days))
    for day_cells in by_slot.values():
        for entries in day_cells.values():
            entries.sort(key=lambda item: item[0][3:])

    starts = sorted(by_slot)
    row_heights = {
        start: max(51, 12 + 39 * max((len(entries) for entries in by_slot[start].values()), default=0))
        for start in starts
    }
    table_top = 133
    table_bottom = table_top + 32 + sum(row_heights.values())
    totals = hour_totals(events)
    used_categories = [name for name in CATEGORIES if totals[name]]
    used_categories.extend(sorted(name for name in totals if name not in CATEGORIES and totals[name]))
    footer_lines = max(1, (len(used_categories) + 2) // 3)
    page_height = max(841.89, table_bottom + 115 + footer_lines * 22)
    page = doc.new_page(width=1190.55, height=page_height)
    page.insert_font(fontname="dejavu", fontfile=str(font_path))
    font = fitz.Font(fontfile=str(font_path))
    page.insert_text((36, 46), "Consolidated weekly timetable", fontsize=24,
                     fontname="dejavu", color=(0.12, 0.22, 0.38))
    first = min(event.day for event in events)
    last = max(event.day for event in events)
    page.insert_text((36, 72), f"All uploaded schedules | {first:%d %b %Y} - {last:%d %b %Y}",
                     fontsize=10, fontname="dejavu", color=(0.35, 0.39, 0.46))
    page.insert_text((36, 106), "Repeated classes are combined by day and time; counts use actual dated sessions.",
                     fontsize=9, fontname="dejavu", color=(0.35, 0.39, 0.46))

    x0, time_width, day_width = 36, 76, (1118 - 76) / 7
    headers = ("Time",) + DAYS
    widths = (time_width,) + (day_width,) * 7
    x = x0
    for name, width in zip(headers, widths):
        page.draw_rect(fitz.Rect(x, table_top, x + width, table_top + 32),
                       color=(0.78, 0.82, 0.88), fill=(0.94, 0.96, 0.99))
        _fit(page, font, name, x + 6, table_top + 21, width - 12, 10)
        x += width

    y = table_top + 32
    for start in starts:
        row_height = row_heights[start]
        page.draw_rect(fitz.Rect(x0, y, x0 + time_width, y + row_height),
                       color=(0.82, 0.85, 0.89))
        _fit(page, font, start, x0 + 6, y + 20, time_width - 12, 10)
        for weekday in range(7):
            x = x0 + time_width + weekday * day_width
            page.draw_rect(fitz.Rect(x, y, x + day_width, y + row_height),
                           color=(0.82, 0.85, 0.89))
            for index, (key, days) in enumerate(by_slot[start].get(weekday, [])):
                _, start_time, end_time, subject, group, room, kind = key
                line_y = y + 15 + index * 39
                title = f"{subject} {group}".strip()
                _fit(page, font, title, x + 6, line_y, day_width - 12, 8)
                details = " | ".join(part for part in (
                    f"{start_time}-{end_time}" if end_time else start_time, room, kind
                ) if part)
                _fit(page, font, details, x + 6, line_y + 11, day_width - 12, 7)
                _fit(page, font, f"{len(days)} dates | {_date_span(days)}",
                     x + 6, line_y + 22, day_width - 12, 6.4,
                     color=(0.38, 0.42, 0.48))
        y += row_height

    y = table_bottom + 35
    page.insert_text((36, y), f"Total scheduled hours: {_hours(sum(totals.values()))}",
                     fontsize=15, fontname="dejavu", color=(0.12, 0.22, 0.38))
    y += 29
    for index, name in enumerate(used_categories):
        column, row = index % 3, index // 3
        _fit(page, font, f"{name}: {_hours(totals[name])}", 36 + column * 372,
             y + row * 22, 360, 10)
    page.insert_text((36, y + footer_lines * 22 + 5), "A missing or unreadable end time is counted as one hour.",
                     fontsize=8, fontname="dejavu", color=(0.38, 0.42, 0.48))
