"""Write dated classes into existing weekly PDF boxes without changing their geometry."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

import fitz

from calendarpdf.services import Event, FONT_PATH
from calendarpdf.presentation import format_period
from calendarpdf.summary import overlapping_events


RED = (0.83, 0.15, 0.18)
BLUE = (0.30, 0.35, 0.70)
INK = (0.12, 0.16, 0.22)
SECONDARY = (0.35, 0.39, 0.46)


def _text(page: fitz.Page, font: fitz.Font, value: str, x: float, y: float,
          width: float, size: float, color=INK) -> None:
    """Keep each label within its allotted width, with an explicit ellipsis if needed."""
    while size > 5.4 and font.text_length(value, fontsize=size) > width:
        size -= .25
    if font.text_length(value, fontsize=size) > width:
        while value and font.text_length(value + "…", fontsize=size) > width:
            value = value[:-1]
        value += "…"
    page.insert_text((x, y), value, fontsize=size, fontname="diaryclasses", color=color)


def _draw_list(page: fitz.Page, area: fitz.Rect, events: list[Event],
               conflicts: set[Event], font: fitz.Font, *, compact: bool = False) -> None:
    if not events:
        return
    available = area.height / len(events)
    minimum = 12 if compact else 15
    if available < minimum:
        raise ValueError(
            f"Too many classes on {events[0].day:%d %b %Y} for the fixed diary box. "
            "Choose a shorter diary range or the single-page weekly layout."
        )
    row_height = min(38 if not compact else 26, available)
    font_size = min(8 if not compact else 7, max(5.5, (row_height - 3) / 2.5))
    for index, event in enumerate(events):
        y = area.y0 + index * row_height + font_size + 2
        dot = RED if event in conflicts else BLUE
        page.draw_circle(fitz.Point(area.x0 + 3, y - 2), 2, fill=dot, color=None)
        x = area.x0 + 9
        width = area.x1 - x
        time_label = format_period(event)
        details = " · ".join(part for part in (event.group, event.room, event.kind) if part)
        if not compact and len(events) <= 4 and row_height >= 29:
            _text(page, font, time_label, x, y, width, font_size, color=SECONDARY)
            _text(page, font, event.subject, x, y + font_size + 1, width, font_size)
            if details:
                _text(page, font, details, x, y + 2 * (font_size + 1), width, font_size - .5,
                      color=SECONDARY)
        else:
            _text(page, font, f"{time_label} {event.subject}", x, y, width, font_size)
            if details:
                _text(page, font, details, x, y + font_size + 1, width, font_size - .5,
                      color=SECONDARY)


def _single_blocks(page: fitz.Page) -> list[fitz.Rect]:
    blocks = [drawing["rect"] for drawing in page.get_drawings()
              if drawing.get("fill") and drawing["rect"].height > 240
              and 120 < drawing["rect"].width < 180]
    return sorted(blocks, key=lambda rect: (round(rect.y0 / 50), rect.x0))


def _existing_notes_bottom(page: fitz.Page, box: fitz.Rect) -> float:
    bottom = box.y0 + 28
    for block in page.get_text("blocks"):
        x0, y0, x1, y1 = block[:4]
        if box.x0 < x0 < box.x1 and box.y0 + 23 < y0 < box.y0 + 65:
            bottom = max(bottom, y1 + 5)
    return bottom


def _annotate_single(page: fitz.Page, monday: dt.date, by_day: dict,
                     conflicts: set[Event], font: fitz.Font) -> None:
    blocks = _single_blocks(page)
    if len(blocks) != 6:
        raise ValueError("The weekly diary boxes could not be located safely.")
    page.insert_font(fontname="diaryclasses", fontfile=str(FONT_PATH))
    for weekday, box in enumerate(blocks[:5]):
        day = monday + dt.timedelta(days=weekday)
        area = fitz.Rect(box.x0 + 5, _existing_notes_bottom(page, box),
                         box.x1 - 5, box.y1 - 24)
        _draw_list(page, area, by_day.get(day, []), conflicts, font)
    weekend = blocks[5]
    separators = [drawing["rect"].y0 for drawing in page.get_drawings()
                  if drawing["rect"].height < .5
                  and drawing["rect"].width > weekend.width - 15
                  and weekend.x0 <= drawing["rect"].x0 < drawing["rect"].x1 <= weekend.x1
                  and weekend.y0 + 50 < drawing["rect"].y0 < weekend.y1 - 50]
    if len(separators) != 1:
        raise ValueError("The Saturday and Sunday diary areas could not be located safely.")
    separator = separators[0]
    for weekday, top, bottom in ((5, weekend.y0 + 27, separator - 7),
                                 (6, separator + 27, weekend.y1 - 21)):
        day = monday + dt.timedelta(days=weekday)
        area = fitz.Rect(weekend.x0 + 5, top, weekend.x1 - 5, bottom)
        _draw_list(page, area, by_day.get(day, []), conflicts, font)


def _double_grid(page: fitz.Page) -> tuple[list[float], dict[int, tuple[float, float]]]:
    drawings = page.get_drawings()
    xs = sorted({round(item["rect"].x0, 1) for item in drawings
                 if item["rect"].width < .2 and item["rect"].height > 10
                 and item["rect"].y0 > 180})
    horizontal = sorted({round(item["rect"].y0, 1) for item in drawings
                         if item["rect"].height < .2 and item["rect"].width > 400
                         and item["rect"].y0 > 170})
    if len(xs) not in (5, 6):
        raise ValueError("The legacy diary columns could not be located safely.")
    rows = {}
    for word in page.get_text("words"):
        if word[0] >= xs[1] or word[1] < 170:
            continue
        value = str(word[4])
        if value not in {str(hour) for hour in range(7, 21)} | {"21-24"}:
            continue
        top_candidates = [position for position in horizontal if position <= word[1]]
        if not top_candidates:
            continue
        top = max(top_candidates)
        bottom_candidates = [position for position in horizontal if position > top + 1]
        if bottom_candidates:
            rows[21 if value == "21-24" else int(value)] = (top, min(bottom_candidates))
    if len(rows) < 14:
        raise ValueError("The legacy diary hour rows could not be located safely.")
    return xs, rows


def _annotate_double(page: fitz.Page, monday: dt.date, first_half: bool,
                     by_day: dict, conflicts: set[Event], font: fitz.Font) -> None:
    columns, rows = _double_grid(page)
    page.insert_font(fontname="diaryclasses", fontfile=str(FONT_PATH))
    days = range(3) if first_half else range(3, 7)
    for column, weekday in enumerate(days):
        day = monday + dt.timedelta(days=weekday)
        by_hour = defaultdict(list)
        for event in by_day.get(day, []):
            hour = int(event.time[:2])
            if hour < 7:
                raise ValueError(f"A class on {day:%d %b %Y} starts before the legacy diary's 07:00 row.")
            by_hour[min(hour, 21)].append(event)
        for hour, events in by_hour.items():
            if hour not in rows:
                raise ValueError(f"The {hour:02d}:00 legacy diary row is unavailable.")
            top, bottom = rows[hour]
            area = fitz.Rect(columns[column + 1] + 2, top + 2,
                             columns[column + 2] - 2, bottom - 2)
            _draw_list(page, area, events, conflicts, font, compact=True)


def add_classes_to_diary(source_pdf: str, output_pdf: str, events: set[Event],
                         start_date: dt.date, number_of_weeks: int,
                         calendar_mode: str) -> None:
    if not events:
        raise ValueError("No dated classes were provided for the diary.")
    by_day = defaultdict(list)
    for event in events:
        by_day[event.day].append(event)
    for entries in by_day.values():
        entries.sort()
    conflicts = overlapping_events(events)
    font = fitz.Font(fontfile=str(FONT_PATH))
    monday = start_date - dt.timedelta(days=start_date.weekday())

    with fitz.open(source_pdf) as document:
        original_geometry = [(page.rect.width, page.rect.height) for page in document]
        if calendar_mode == "single":
            pages = [page for page in document
                     if "Lunes" in page.get_text() and "Martes" in page.get_text()
                     and len(_single_blocks(page)) == 6]
            if len(pages) != number_of_weeks:
                raise ValueError("The weekly diary pages could not be identified safely.")
            for index, page in enumerate(pages):
                _annotate_single(page, monday + dt.timedelta(weeks=index), by_day, conflicts, font)
        else:
            pages = []
            for page in document:
                header_words = {word[4] for word in page.get_text("words") if word[1] < 120}
                if {"L", "M", "X"} <= header_words:
                    pages.append((page, True))
                elif {"J", "V", "S", "D"} <= header_words:
                    pages.append((page, False))
            if len(pages) != 2 * number_of_weeks:
                raise ValueError("The legacy weekly diary pages could not be identified safely.")
            for index, (page, first_half) in enumerate(pages):
                _annotate_double(page, monday + dt.timedelta(weeks=index // 2),
                                 first_half, by_day, conflicts, font)
        if original_geometry != [(page.rect.width, page.rect.height) for page in document]:
            raise RuntimeError("The diary page geometry changed unexpectedly.")
        document.save(output_pdf, garbage=4, deflate=True)
