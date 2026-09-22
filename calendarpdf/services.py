"""Read dated timetable cells and draw a weekday calendar."""
from __future__ import annotations

import calendar
import io
import re
import subprocess
import unicodedata
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import fitz
from PIL import Image, ImageOps


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
MONTHS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
SUBJECT_TRANSLATIONS = {
    "ESTADÍSTICA": "Statistics",
    "MATEMÁTICAS I": "Mathematics I",
    "MATEMÁTICAS Y COMPUTACIÓN": "Mathematics and Computing",
}
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DATE_RE = re.compile(r"(?<!\d)([0-3]?\d)\s*[/.-]\s*([01]?\d)\s*[/.-]\s*(20\d\d|\d\d)(?!\d)")
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)\s*[-–]\s*([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)")
START_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)(?!\d)")
SUBJECT_RE = re.compile(r"\b\d{4,6}\s*\([^)]*\)\s*[-–]\s*(.+)", re.I)
GROUP_RE = re.compile(r"grupo\s*:\s*([\w-]+)", re.I)
ROOM_RE = re.compile(r"\bAULA(?:\s+DE)?\s+([^)]*)", re.I)


@dataclass(frozen=True, order=True)
class Event:
    day: date
    time: str
    subject: str
    group: str = ""
    room: str = ""


def _ocr(image: Image.Image, psm: int = 6) -> str:
    # A larger raster improves recognition of the tiny type in web screenshots.
    scale = min(4, max(2, round(1800 / max(image.width, 1))))
    image = ImageOps.autocontrast(image.convert("L")).resize(
        (image.width * scale, image.height * scale), Image.Resampling.LANCZOS
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    run = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", str(psm)],
        input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=90, check=False,
    )
    if run.returncode:
        raise ValueError("The timetable could not be read with OCR.")
    return run.stdout.decode("utf-8", errors="replace")


def _column_bounds(image: Image.Image) -> list[tuple[int, int]]:
    """Find weekday headings; use equal columns when a heading is unreadable."""
    header = image.crop((0, 0, image.width, max(1, image.height // 2)))
    scale = 2
    header = header.resize((header.width * scale, header.height * scale))
    buffer = io.BytesIO()
    header.save(buffer, format="PNG")
    run = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", "11", "tsv"],
        input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
    )
    centers = {}
    if run.returncode == 0:
        for line in run.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) < 12:
                continue
            word = unicodedata.normalize("NFKD", cells[11]).encode("ascii", "ignore").decode().lower().strip(".,: ")
            names = ("lunes", "martes", "miercoles", "jueves", "viernes")
            if word in names:
                centers[names.index(word)] = (int(cells[6]) + int(cells[8]) / 2) / scale
    if len(centers) == 5:
        positions = [centers[i] for i in range(5)]
        edges = [max(0, positions[0] - (positions[1] - positions[0]) / 2)]
        edges.extend((positions[i] + positions[i + 1]) / 2 for i in range(4))
        edges.append(min(image.width, positions[4] + (positions[4] - positions[3]) / 2))
    else:
        left = image.width * .05
        step = (image.width - left) / 5
        edges = [left + i * step for i in range(6)]
    return [(round(edges[i] + 2), round(edges[i + 1] - 2)) for i in range(5)]


def _extract_column(text: str, weekday: int) -> set[Event]:
    events = set()
    years = [int(match.group(3)) for match in DATE_RE.finditer(text)]
    likely_year = max(set(years), key=years.count) if years else None
    if likely_year is not None and likely_year < 100:
        likely_year += 2000
    subject = ""
    group = ""
    room = ""
    hour = ""
    for raw in text.splitlines():
        line = raw.strip()
        match = SUBJECT_RE.search(line)
        if match:
            subject = re.sub(r"\s+", " ", match.group(1)).strip(" -")
            subject = re.sub(r"(?i)^MATEM[ÁA]TICAS\s+[1|I]$", "MATEMÁTICAS I", subject)
            subject = SUBJECT_TRANSLATIONS.get(subject.upper(), subject)
            group = ""
            room = ""
            hour = ""
        group_match = GROUP_RE.search(line.replace("Grupo;", "Grupo:"))
        if group_match:
            group = group_match.group(1).upper()
            if group == "11" and subject == "Mathematics I":
                group = "1T"
        room_match = ROOM_RE.search(line)
        if room_match:
            room_text = room_match.group(1).strip(" ()")
            if "INFORM" in room_text.upper():
                lab = re.sub(r"(?i)^INFORM[ÁA]TICA\s*", "", room_text)
                room = "Computer Lab " + re.sub(r"^1(?=-\d+$)", "I", lab)
            else:
                room = "Room " + room_text
        time_match = TIME_RE.search(line)
        if time_match:
            hour = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        elif not hour and subject:
            start = START_RE.search(line)
            if start:
                hour = f"{int(start.group(1)):02d}:{start.group(2)}"
        if not subject or not hour:
            continue
        for part in DATE_RE.finditer(line):
            year = int(part.group(3))
            if year < 100:
                year += 2000
            try:
                event_day = date(year, int(part.group(2)), int(part.group(1)))
            except ValueError:
                continue
            if event_day.weekday() != weekday and likely_year and year != likely_year:
                try:
                    corrected = event_day.replace(year=likely_year)
                    if corrected.weekday() == weekday:
                        event_day = corrected
                except ValueError:
                    pass
            if event_day.weekday() == weekday:
                events.add(Event(event_day, hour, subject, group, room))
    return events


def extract_events(data: bytes, filename: str) -> set[Event]:
    images = []
    if filename.lower().endswith(".pdf"):
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.page_count > 30:
                raise ValueError("A timetable PDF can contain up to 30 pages.")
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    else:
        try:
            image = Image.open(io.BytesIO(data))
            image.verify()
            image = Image.open(io.BytesIO(data))
            if image.width * image.height > 50_000_000:
                raise ValueError("The image exceeds 50 megapixels.")
            images.append(ImageOps.exif_transpose(image).convert("RGB"))
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("The image is invalid or damaged.") from exc

    events = set()
    for image in images:
        # Header words locate columns regardless of image width, margins or ratio.
        # Timetables in this format have one hour column followed by five days.
        # OCR each day separately so dates remain attached to their subject.
        for weekday, (x0, x1) in enumerate(_column_bounds(image)):
            crop = image.crop((x0, 0, x1, image.height))
            events.update(_extract_column(_ocr(crop), weekday))
    return events


def make_pdf(events: set[Event]) -> bytes:
    if not events:
        raise ValueError("No classes with a subject, time and valid dates were found.")
    first = min(event.day.replace(day=1) for event in events)
    last = max(event.day.replace(day=1) for event in events)
    month_count = (last.year - first.year) * 12 + last.month - first.month + 1
    if month_count > 36:
        raise ValueError("The dates span more than 36 months. Check the source images.")
    by_day = defaultdict(list)
    for event in events:
        by_day[event.day].append(event)

    doc = fitz.open()
    font = fitz.Font(fontfile=str(FONT_PATH))
    for offset in range(month_count):
        year = first.year + (first.month - 1 + offset) // 12
        month = (first.month - 1 + offset) % 12 + 1
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
        page = doc.new_page(width=1190.55, height=841.89)  # A3 landscape
        page.insert_font(fontname="dejavu", fontfile=str(FONT_PATH))
        page.insert_text((36, 46), f"{MONTHS[month]} {year}", fontsize=25, fontname="dejavu", color=(0.12, 0.22, 0.38))
        x0, y0, width, bottom = 36, 78, 1118, 811
        col_width = width / 5
        row_height = (bottom - 112) / len(weeks)
        for col, name in enumerate(WEEKDAYS):
            page.draw_rect(fitz.Rect(x0 + col * col_width, y0, x0 + (col + 1) * col_width, 112), color=(0.78, 0.82, 0.88), fill=(0.94, 0.96, 0.99))
            page.insert_text((x0 + col * col_width + 9, 101), name, fontsize=13, fontname="dejavu")
        for row, week in enumerate(weeks):
            for col, day in enumerate(week[:5]):
                x = x0 + col * col_width
                top = 112 + row * row_height
                rect = fitz.Rect(x, top, x + col_width, top + row_height)
                page.draw_rect(rect, color=(0.82, 0.85, 0.89), fill=(1, 1, 1) if day.month == month else (0.97, 0.97, 0.97))
                page.insert_text((x + 8, top + 18), str(day.day), fontsize=12, color=(0.2, 0.25, 0.31) if day.month == month else (0.65, 0.67, 0.7))
                daily = sorted(by_day[day]) if day.month == month else []
                available = row_height - 34
                line_height = min(27, available / max(len(daily), 1))
                for index, event in enumerate(daily):
                    y = top + 36 + index * line_height
                    label = "  ".join(part for part in (event.time, event.subject, event.group) if part)
                    page.draw_circle(fitz.Point(x + 11, y - 3), 2.4, fill=(0.46, 0.29, 0.9), color=None)
                    box = fitz.Rect(x + 19, y - 11, x + col_width - 5, y + max(5, line_height - 5))
                    size = 9
                    while font.text_length(label, fontsize=size) > box.width and size > 6:
                        size -= 0.5
                    if font.text_length(label, fontsize=size) > box.width:
                        while label and font.text_length(label + "…", fontsize=size) > box.width:
                            label = label[:-1]
                        label += "…"
                    page.insert_text((box.x0, y), label, fontsize=size, fontname="dejavu")
                    if event.room:
                        room_label = event.room
                        room_size = 8 if line_height >= 22 else 6
                        while room_label and font.text_length(room_label, fontsize=room_size) > box.width:
                            room_label = room_label[:-1]
                        page.insert_text((box.x0, y + min(10, line_height / 2)), room_label,
                                         fontsize=room_size, fontname="dejavu", color=(0.35, 0.39, 0.46))
    output = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return output
