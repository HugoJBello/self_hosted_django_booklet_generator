"""Read dated timetable cells and draw a weekday calendar."""
from __future__ import annotations

import calendar
import io
import math
import re
import subprocess
import unicodedata
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import fitz
from PIL import Image, ImageOps, ImageSequence


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DATE_RE = re.compile(r"(?<!\d)([0-3]?\d)\s*[/.-]\s*([01]?\d)\s*[/.-]\s*(20\d\d|\d\d)(?!\d)")
ISO_DATE_RE = re.compile(r"(?<!\d)(20\d\d)\s*[-/.]\s*([01]?\d)\s*[-/.]\s*([0-3]?\d)(?!\d)")
NAMED_DATE_RE = re.compile(r"(?<!\d)([0-3]?\d)\s+(?:de\s+)?([A-Za-zÁÉÍÓÚÜáéíóúü]+)\.?\s+(?:de\s+)?(20\d\d|\d\d)(?!\d)", re.I)
MONTH_ALIASES = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1,
    "feb": 2, "febrero": 2, "february": 2,
    "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "abril": 4, "apr": 4, "april": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6, "june": 6,
    "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10,
    "nov": 11, "noviembre": 11, "november": 11,
    "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
}
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:.h]\s*([0-5]\d)\s*(?:[-–—]|\ba\b|\bto\b)\s*([01]?\d|2[0-3])\s*[:.h]\s*([0-5]\d)(?!\d)", re.I)
HOUR_RANGE_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[-–—]\s*([01]?\d|2[0-3])(?!\d)")
START_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:.h]\s*([0-5]\d)(?!\d)", re.I)
SUBJECT_RE = re.compile(r"\b\d{4,6}(?:\s*\([^)]*\))?\s*[-–—:]\s*(.+)", re.I)
GROUP_RE = re.compile(r"\b(?:grupo|group|gr\.?)\s*[:;#-]?\s*([\w-]+(?:\s+[A-Za-z])?)", re.I)
ROOM_RE = re.compile(
    r"\b((?:aula|sala|laboratorio|lab(?:oratory)?|room|classroom|espacio|ubicaci[oó]n|location|venue)(?:\s+de)?)\b"
    r"\s*[:#-]?\s*([^)]*)",
    re.I,
)
TYPE_RE = re.compile(r"\b(?:tipo|type|actividad|activity|modalidad)\s*[:;=-]\s*(.+)", re.I)
DAY_NAMES = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
ALL_DAY_NAMES = set(DAY_NAMES) | {name.lower() for name in WEEKDAYS}
DAY_ABBREVIATIONS = {"lun": 0, "mar": 1, "mie": 2, "mier": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}


@dataclass(frozen=True, order=True)
class Event:
    day: date
    time: str
    subject: str
    group: str = ""
    room: str = ""
    end_time: str = ""
    kind: str = ""


def _plain(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


def _ocr_image(image: Image.Image, target_width: int) -> bytes:
    image = ImageOps.autocontrast(image.convert("L"))
    scale = min(4, max(1, target_width / max(image.width, 1)),
                math.sqrt(12_000_000 / max(image.width * image.height, 1)))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ocr(image: Image.Image, psm: int = 6) -> str:
    # A larger raster improves recognition of the tiny type in web screenshots.
    try:
        run = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", str(psm)],
            input=_ocr_image(image, 1800), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=90, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise ValueError("OCR timed out or is unavailable. Try a smaller, clearer file.") from exc
    if run.returncode:
        raise ValueError("The timetable could not be read with OCR.")
    return run.stdout.decode("utf-8", errors="replace")


def _column_bounds(image: Image.Image) -> list[tuple[int, int, int]]:
    """Locate five to seven weekday columns, tolerating missed headings."""
    header = image.crop((0, 0, image.width, max(1, image.height // 2)))
    scale = min(2, math.sqrt(8_000_000 / max(header.width * header.height, 1)))
    if scale != 1:
        header = header.resize((max(1, round(header.width * scale)), max(1, round(header.height * scale))))
    try:
        run = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", "11", "tsv"],
            input=_ocr_image(header, header.width), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=90,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise ValueError("OCR timed out or is unavailable. Try a smaller, clearer file.") from exc
    centers = {}
    if run.returncode == 0:
        for line in run.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) < 12:
                continue
            word = _plain(cells[11]).strip(".,: ")
            names = DAY_NAMES + tuple(name.lower() for name in WEEKDAYS)
            weekday = (names.index(word) % 7 if word in names else DAY_ABBREVIATIONS.get(word))
            if weekday is not None:
                centers[weekday] = (int(cells[6]) + int(cells[8]) / 2) / scale
    if len(centers) >= 3:
        indices = sorted(centers)
        spacings = [(centers[b] - centers[a]) / (b - a) for a, b in zip(indices, indices[1:])]
        step = sorted(spacings)[len(spacings) // 2]
        count = max(5, max(indices) + 1)
        if step > 0 and 0.5 * image.width / 7 <= step <= image.width / 3:
            origin = sorted(centers[i] - (i + .5) * step for i in indices)[len(indices) // 2]
            edges = [max(0, min(image.width, origin + i * step)) for i in range(count + 1)]
        else:
            centers = {}
    if len(centers) < 3:
        count = 5
        edges = [image.width * (.05 + .95 * i / count) for i in range(count + 1)]
    return [(i, round(edges[i] + 2), round(edges[i + 1] - 2)) for i in range(count)]


def _clean_source_label(raw: str) -> str:
    """Remove OCR spacing noise without translating or renaming source text."""
    return re.sub(r"\s+", " ", raw).strip(" -–—:()")


def _subject_from_line(line: str, allow_fallback: bool) -> str | None:
    if DATE_RE.search(line) or ISO_DATE_RE.search(line) or NAMED_DATE_RE.search(line):
        return None
    match = SUBJECT_RE.search(line)
    if match:
        return _clean_source_label(match.group(1))
    plain = _plain(line)
    if not allow_fallback or len(line) < 5:
        return None
    if GROUP_RE.search(line) or ROOM_RE.search(line) or TYPE_RE.search(line):
        return None
    if any(token in plain for token in (
        "grupo", "group", "aula", "sala", "room", "laboratorio", "espacio", "ubicacion",
        "location", "venue", "tipo", "type",
        "actividad", "cuatrimestre", "semestre", "curso", "timetable", "hora de", "schedule"
    )) or plain.strip(" .:-") in ALL_DAY_NAMES:
        return None
    if DATE_RE.search(line) or ISO_DATE_RE.search(line) or START_RE.search(line):
        return None
    letters = [char for char in line if char.isalpha()]
    if len(letters) >= 3:
        return _clean_source_label(line)
    return None


def _clean_room(raw_label: str, raw_detail: str) -> str:
    """Keep the room wording from the upload while removing dates and OCR spacing noise."""
    label = re.sub(r"\s+", " ", raw_label).strip(" ()-:.")
    detail = re.sub(r"\s+", " ", raw_detail).strip(" ()-:.")
    detail = DATE_RE.sub("", detail)
    detail = ISO_DATE_RE.sub("", detail)
    detail = NAMED_DATE_RE.sub("", detail).strip(" ,-;.")
    return " ".join(part for part in (label, detail) if part)


def _normalise_type(raw: str) -> str:
    value = _plain(raw).strip(" ().:-")
    if any(term in value for term in ("laborat", "practica de lab", "computer lab")):
        return "Laboratory"
    if any(term in value for term in ("teori", "theor", "lecture", "magistral")):
        return "Theory"
    if any(term in value for term in ("seminar", "seminario")):
        return "Seminar"
    if any(term in value for term in ("taller", "workshop")):
        return "Workshop"
    if any(term in value for term in ("virtual", "online", "remote", "distancia")):
        return "Online"
    if any(term in value for term in ("examen", "exam", "evaluacion", "test")):
        return "Exam"
    if any(term in value for term in ("tutoria", "tutorial")):
        return "Tutorial"
    if any(term in value for term in ("practica", "practice", "exercise", "problema")):
        return "Classroom practice"
    return "Other"


def _line_dates(line: str, likely_year: int | None, weekday: int) -> set[date]:
    found = set()
    candidates = []
    for match in DATE_RE.finditer(line):
        candidates.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in ISO_DATE_RE.finditer(line):
        candidates.append((int(match.group(3)), int(match.group(2)), int(match.group(1))))
    for match in NAMED_DATE_RE.finditer(line):
        month = MONTH_ALIASES.get(_plain(match.group(2)))
        if month:
            candidates.append((int(match.group(1)), month, int(match.group(3))))
    for day, month, year in candidates:
        if year < 100:
            year += 2000
        try:
            event_day = date(year, month, day)
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
            found.add(event_day)
    return found


def _extract_column(text: str, weekday: int) -> set[Event]:
    events = set()
    years = [int(match.group(3)) for match in DATE_RE.finditer(text)]
    years.extend(int(match.group(1)) for match in ISO_DATE_RE.finditer(text))
    years.extend(int(match.group(3)) for match in NAMED_DATE_RE.finditer(text)
                 if _plain(match.group(2)) in MONTH_ALIASES)
    likely_year = max(set(years), key=lambda year: (years.count(year), year)) if years else None
    if likely_year is not None and likely_year < 100:
        likely_year += 2000
    subject = ""
    group = ""
    room = ""
    hour = ""
    end_hour = ""
    kind = ""
    previous_had_dates = False
    for raw in text.splitlines():
        line = raw.strip()
        new_subject = _subject_from_line(line, not subject or previous_had_dates)
        if new_subject:
            subject = new_subject
            group = ""
            room = ""
            hour = ""
            end_hour = ""
            kind = ""
            previous_had_dates = False
        group_match = GROUP_RE.search(line)
        if group_match:
            group = re.sub(r"\s+", "", group_match.group(1)).upper()
        room_match = ROOM_RE.search(line)
        if room_match and not new_subject and not TYPE_RE.search(line):
            room = _clean_room(room_match.group(1), room_match.group(2))
        type_match = TYPE_RE.search(line)
        if type_match and not new_subject:
            kind = _normalise_type(type_match.group(1))
        elif not new_subject and _plain(line).strip(" ().:-") in (
            "teoria", "practica", "laboratorio", "seminario", "taller", "online", "examen", "tutoria"
        ):
            kind = _normalise_type(line)
        time_match = TIME_RE.search(line)
        if time_match:
            hour = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
            end_hour = f"{int(time_match.group(3)):02d}:{time_match.group(4)}"
        elif (subject and not DATE_RE.search(line) and not ISO_DATE_RE.search(line)
              and not NAMED_DATE_RE.search(line)
              and not room_match and not group_match and not type_match and not new_subject):
            range_match = HOUR_RANGE_RE.search(line)
            if range_match:
                hour = f"{int(range_match.group(1)):02d}:00"
                end_hour = f"{int(range_match.group(2)):02d}:00"
            elif not hour:
                start = START_RE.search(line)
                if start:
                    hour = f"{int(start.group(1)):02d}:{start.group(2)}"
        if not subject or not hour:
            continue
        dates = _line_dates(line, likely_year, weekday)
        if dates:
            previous_had_dates = True
        for event_day in dates:
            events.add(Event(event_day, hour, subject, group, room, end_hour, kind))
    return events


def extract_events(data: bytes, filename: str) -> set[Event]:
    if Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".pdf"}:
        raise ValueError("Unsupported file type. Use PNG, JPG, WEBP, TIFF or PDF.")
    images = []
    if filename.lower().endswith(".pdf"):
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported.")
                if not doc.page_count:
                    raise ValueError("The PDF has no pages.")
                if doc.page_count > 30:
                    raise ValueError("A timetable PDF can contain up to 30 pages.")
                for page in doc:
                    if page.rect.width * page.rect.height * 4 > 50_000_000:
                        raise ValueError("A PDF page is too large to process safely.")
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
        except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError) as exc:
            raise ValueError("The PDF is invalid or damaged.") from exc
    else:
        try:
            image = Image.open(io.BytesIO(data))
            image.verify()
            image = Image.open(io.BytesIO(data))
            if getattr(image, "n_frames", 1) > 30:
                raise ValueError("An image can contain up to 30 frames.")
            for frame in ImageSequence.Iterator(image):
                if frame.width * frame.height > 50_000_000:
                    raise ValueError("The image exceeds 50 megapixels.")
                images.append(ImageOps.exif_transpose(frame).convert("RGB"))
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("The image is invalid or damaged.") from exc

    events = set()
    for image in images:
        # Header words locate columns regardless of image width, margins or ratio.
        # Timetables in this format have one hour column followed by weekdays.
        # OCR each day separately so dates remain attached to their subject.
        for weekday, x0, x1 in _column_bounds(image):
            if x1 <= x0:
                continue
            crop = image.crop((x0, 0, x1, image.height))
            events.update(_extract_column(_ocr(crop), weekday))
    return events


def extract_uploaded_timetables(files) -> set[Event]:
    """Validate and merge uploaded schedules for Calendar and Diary."""
    if len(files) > 20:
        raise ValueError("Select no more than 20 timetable files.")
    events = set()
    for uploaded in files:
        if uploaded.size > 25 * 1024 * 1024:
            raise ValueError(f"{uploaded.name}: exceeds 25 MB.")
        try:
            extracted = extract_events(uploaded.read(), uploaded.name)
        except ValueError as exc:
            raise ValueError(f"{uploaded.name}: {exc}") from exc
        if not extracted:
            raise ValueError(
                f"{uploaded.name}: no dated classes could be read. "
                "Check that the image is clear and contains dates, times and subjects."
            )
        events.update(extracted)
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
    from .presentation import format_period
    from .summary import add_weekly_summary, overlapping_events

    overlaps = overlapping_events(events)
    day_count = 7 if any(event.day.weekday() >= 5 for event in events) else 5
    for offset in range(month_count):
        year = first.year + (first.month - 1 + offset) // 12
        month = (first.month - 1 + offset) % 12 + 1
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
        row_heights = [max(80, 34 + 26 * max(
            (len(by_day[day]) for day in week[:day_count] if day.month == month), default=0
        )) for week in weeks]
        spare = max(0, 699 - sum(row_heights)) / len(row_heights)
        row_heights = [height + spare for height in row_heights]
        page_height = max(841.89, 112 + sum(row_heights) + 30)
        page = doc.new_page(width=1190.55, height=page_height)  # At least A3 landscape
        page.insert_font(fontname="dejavu", fontfile=str(FONT_PATH))
        page.insert_text((36, 46), f"{MONTHS[month]} {year}", fontsize=25, fontname="dejavu", color=(0.12, 0.22, 0.38))
        page.draw_circle(fitz.Point(965, 39), 3, fill=(0.83, 0.15, 0.18), color=None)
        page.insert_text((974, 43), "Overlapping classes", fontsize=9, fontname="dejavu")
        x0, y0, width = 36, 78, 1118
        col_width = width / day_count
        for col, name in enumerate(WEEKDAYS[:day_count]):
            page.draw_rect(fitz.Rect(x0 + col * col_width, y0, x0 + (col + 1) * col_width, 112), color=(0.78, 0.82, 0.88), fill=(0.94, 0.96, 0.99))
            page.insert_text((x0 + col * col_width + 9, 101), name, fontsize=13, fontname="dejavu")
        for row, week in enumerate(weeks):
            row_height = row_heights[row]
            for col, day in enumerate(week[:day_count]):
                x = x0 + col * col_width
                top = 112 + sum(row_heights[:row])
                rect = fitz.Rect(x, top, x + col_width, top + row_height)
                page.draw_rect(rect, color=(0.82, 0.85, 0.89), fill=(1, 1, 1) if day.month == month else (0.97, 0.97, 0.97))
                page.insert_text((x + 8, top + 18), str(day.day), fontsize=12, color=(0.2, 0.25, 0.31) if day.month == month else (0.65, 0.67, 0.7))
                daily = sorted(by_day[day]) if day.month == month else []
                available = row_height - 34
                line_height = min(27, available / max(len(daily), 1))
                for index, event in enumerate(daily):
                    y = top + 36 + index * line_height
                    label = "  ".join(part for part in (format_period(event), event.subject, event.group) if part)
                    dot_color = (0.83, 0.15, 0.18) if event in overlaps else (0.46, 0.29, 0.9)
                    page.draw_circle(fitz.Point(x + 11, y - 3), 2.4, fill=dot_color, color=None)
                    box = fitz.Rect(x + 19, y - 11, x + col_width - 5, y + max(5, line_height - 5))
                    size = 9
                    while font.text_length(label, fontsize=size) > box.width and size > 6:
                        size -= 0.5
                    if font.text_length(label, fontsize=size) > box.width:
                        while label and font.text_length(label + "…", fontsize=size) > box.width:
                            label = label[:-1]
                        label += "…"
                    page.insert_text((box.x0, y), label, fontsize=size, fontname="dejavu")
                    if event.room or event.kind:
                        room_label = " · ".join(part for part in (event.kind, event.room) if part)
                        room_size = 8 if line_height >= 22 else 6
                        while room_label and font.text_length(room_label, fontsize=room_size) > box.width:
                            room_label = room_label[:-1]
                        page.insert_text((box.x0, y + min(10, line_height / 2)), room_label,
                                         fontsize=room_size, fontname="dejavu", color=(0.35, 0.39, 0.46))
    add_weekly_summary(doc, events, FONT_PATH)
    output = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return output
