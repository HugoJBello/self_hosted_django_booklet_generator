from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import ephem
import matplotlib

from booklets.flipped_a4 import FlippedA4Quality, FlippedA4SplitMode, build_flipped_a4_booklets_pipeline
from booklets.services import SourcePdfSpec, build_booklets_pipeline

matplotlib.use("Agg")


DEFAULT_NUMBER_OF_WEEKS = 4
DEFAULT_CALENDAR_MODE = "single"
ASSET_DIR = Path(__file__).resolve().parent / "latex_assets"


@dataclass(frozen=True)
class DiaryJobResult:
    job_id: str
    output_pdf_path: str


def _read_asset(relative_path: str) -> str:
    return (ASSET_DIR / relative_path).read_text(encoding="utf-8")


def _load_dates(relative_path: str) -> dict[str, str]:
    with (ASSET_DIR / relative_path).open("r", encoding="utf-8") as file:
        return json.load(file)


def _moon_position(date: dt.datetime) -> decimal.Decimal:
    dec = decimal.Decimal
    diff = date - dt.datetime(2001, 1, 1)
    days = dec(diff.days) + (dec(diff.seconds) / dec(86400))
    lunations = dec("0.20439731") + (days * dec("0.03386319269"))
    return lunations % dec(1)


def _moon_phase_index(date: dt.date) -> int:
    position = _moon_position(dt.datetime.combine(date, dt.time()))
    return math.floor((position * decimal.Decimal(8)) + decimal.Decimal("0.5")) & 7


def _surround_day(date: dt.date) -> str:
    return "\\textbf{\\sffamily{" + date.strftime("%d") + "}} " + date.strftime("%b")


def _moon_image(date: dt.date) -> str:
    return f"\\moonPhaseIcon{{{_moon_phase_index(date)}}}"


def _date_text(date: dt.date, dates: dict[str, str]) -> str:
    return "\\small{" + dates.get(date.strftime("%d/%m/%Y"), "") + "}"


def _festivity_text(date: dt.date, dates: dict[str, str]) -> str:
    text = dates.get(date.strftime("%d/%m/%Y"), "")
    return "\\small{" + (f" {text}" if text else "") + "}"


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "$": "\\$",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "~": "\\textasciitilde{}",
        "^": "\\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _visible_planets(date: dt.date, latitude: float, longitude: float) -> list[tuple[str, str]]:
    observer = ephem.Observer()
    observer.lat = str(latitude)
    observer.lon = str(longitude)
    observer.elevation = 0

    planets = [
        ("\\planetMercuryIcon", "Merc.", ephem.Mercury),
        ("\\planetVenusIcon", "Ven.", ephem.Venus),
        ("\\planetMarsIcon", "Mar.", ephem.Mars),
        ("\\planetJupiterIcon", "Jup.", ephem.Jupiter),
        ("\\planetSaturnIcon", "Sat.", ephem.Saturn),
    ]
    local_utc_offset_hours = round(longitude / 15)
    local_sample_times = [
        dt.datetime.combine(date, dt.time(18, 0)),
        dt.datetime.combine(date, dt.time(20, 0)),
        dt.datetime.combine(date, dt.time(22, 0)),
        dt.datetime.combine(date + dt.timedelta(days=1), dt.time(5, 0)),
    ]
    utc_sample_times = [sample - dt.timedelta(hours=local_utc_offset_hours) for sample in local_sample_times]
    min_planet_altitude = math.radians(10)
    max_sun_altitude = math.radians(-6)
    visible: list[tuple[str, str, float]] = []

    for icon_macro, label, planet_cls in planets:
        best_altitude = None
        for sample_time in utc_sample_times:
            observer.date = sample_time
            sun = ephem.Sun(observer)
            planet = planet_cls(observer)
            planet_altitude = float(planet.alt)
            if float(sun.alt) <= max_sun_altitude and planet_altitude >= min_planet_altitude:
                if best_altitude is None or planet_altitude > best_altitude:
                    best_altitude = planet_altitude

        if best_altitude is not None:
            visible.append((icon_macro, label, best_altitude))

    return [
        (icon_macro, label)
        for icon_macro, label, _ in sorted(visible, key=lambda item: item[2], reverse=True)
    ]


def _visible_planet_names(date: dt.date, latitude: float, longitude: float) -> list[str]:
    return [label for _, label in _visible_planets(date, latitude, longitude)]


def _planet_text(date: dt.date, latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""

    planets = _visible_planets(date, latitude, longitude)
    if not planets:
        return ""
    items = "".join(
        f"\\planetItem{{{icon_macro}}}{{{_latex_escape(label)}}}"
        for icon_macro, label in planets
    )
    return "\\scriptsize{" + items + "}"


def _generate_graph(output_dir: str, initial_date: dt.date, number_of_weeks: int) -> None:
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots()
    fig.canvas.draw()
    ax.axis([0, number_of_weeks, 0, 25])
    plt.xticks(range(0, number_of_weeks))
    plt.yticks(range(0, 25))

    ticks = []
    current_date = initial_date
    for _ in range(number_of_weeks):
        week_start = current_date - dt.timedelta(days=current_date.weekday())
        week_end = week_start + dt.timedelta(days=6)
        current_date = week_end + dt.timedelta(days=1)
        ticks.append(current_date.strftime("%b %d, %Y"))

    plt.grid()
    plt.xticks(rotation=70)
    ax.set_xticklabels(ticks)
    plt.savefig(os.path.join(output_dir, "graph.png"), bbox_inches="tight")
    plt.close(fig)


def _generate_intro(work_dir: str, start_date: dt.date, number_of_weeks: int, include_progress_graph: bool) -> str:
    if include_progress_graph:
        _generate_graph(work_dir, start_date, number_of_weeks)
        intro = _read_asset("text_blocks/intro.tex")
        return intro.replace("DATE1", start_date.strftime("%b %d, %Y")).replace("WEEKS", str(number_of_weeks))

    return f"\n\\section*{{Periodo {start_date.strftime('%b %d, %Y')}, {number_of_weeks} semanas }}\n\n\\newpage\n"


def _generate_tasks_page(start_date: dt.date, end_date: dt.date) -> str:
    table = _read_asset("text_blocks/tasks.tex")
    return table.replace("DATE1", start_date.strftime("%b %d, %Y")).replace("DATE2", end_date.strftime("%b %d, %Y"))


def _generate_calendar_intro(start_date: dt.date, number_of_weeks: int, include_constellation_map: bool) -> str:
    if not include_constellation_map:
        return ""

    intro = _read_asset("text_blocks/calendar_intro.tex")
    return (
        intro.replace("DATE1", start_date.strftime("%b %d, %Y"))
        .replace("WEEKS", str(number_of_weeks))
        .replace("MONTH", start_date.strftime("%m"))
    )


def _generate_calendar_page(
    start_date: dt.date,
    calendar_mode: str,
    important_dates: dict[str, str],
    festivities: dict[str, str],
    latitude: float | None,
    longitude: float | None,
) -> str:
    template = "text_blocks/calendar.tex" if calendar_mode == "double" else "text_blocks/calendar_single.tex"
    table = _read_asset(template)
    days = {
        "L": start_date,
        "M": start_date + dt.timedelta(days=1),
        "X": start_date + dt.timedelta(days=2),
        "J": start_date + dt.timedelta(days=3),
        "V": start_date + dt.timedelta(days=4),
        "S": start_date + dt.timedelta(days=5),
        "D": start_date + dt.timedelta(days=6),
    }

    table = table.replace("MES", start_date.strftime("%b"))
    for suffix, date in days.items():
        table = table.replace(f"DATE{suffix}", _surround_day(date))
        table = table.replace(f"MOON{suffix}", _moon_image(date))
        table = table.replace(f"PLANET{suffix}", _planet_text(date, latitude, longitude))
        table = table.replace(f"SPECIAL{suffix}", _date_text(date, important_dates))
        table = table.replace(f"FESTIVO{suffix}", _festivity_text(date, festivities))
    return table


def _write_diary_tex(
    work_dir: str,
    start_date: dt.date,
    number_of_weeks: int,
    calendar_mode: str,
    include_progress_graph: bool,
    include_constellation_map: bool,
    latitude: float | None,
    longitude: float | None,
) -> str:
    important_dates = _load_dates("fechas/fechas_importantes_uva.json")
    festivities = _load_dates("fechas/festivos.json")
    monday = start_date - dt.timedelta(days=start_date.weekday())

    chunks = [
        _read_asset("text_blocks/initial.tex"),
        _generate_intro(work_dir, monday, number_of_weeks, include_progress_graph),
    ]

    current_date = start_date
    for _ in range(number_of_weeks):
        week_start = current_date - dt.timedelta(days=current_date.weekday())
        week_end = week_start + dt.timedelta(days=6)
        current_date = week_end + dt.timedelta(days=1)
        chunks.append(_generate_tasks_page(week_start, week_end) + "\n\\newpage")

    chunks.append(_generate_calendar_intro(monday, number_of_weeks, include_constellation_map))

    current_date = start_date
    for _ in range(number_of_weeks):
        week_start = current_date - dt.timedelta(days=current_date.weekday())
        week_end = week_start + dt.timedelta(days=6)
        current_date = week_end + dt.timedelta(days=1)
        chunks.append(
            _generate_calendar_page(
                week_start,
                calendar_mode,
                important_dates,
                festivities,
                latitude,
                longitude,
            )
            + "\n\\newpage"
        )

    chunks.append("\\end{document}")
    tex_path = os.path.join(work_dir, "result.tex")
    Path(tex_path).write_text("".join(chunks), encoding="utf-8")
    return tex_path


def _compile_latex(work_dir: str, tex_path: str) -> str:
    command = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", os.path.basename(tex_path)]
    for _ in range(2):
        completed = subprocess.run(
            command,
            cwd=work_dir,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            tail = "\n".join(completed.stdout.splitlines()[-25:])
            raise RuntimeError(f"LaTeX compilation failed:\n{tail}")

    pdf_path = os.path.join(work_dir, "result.pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError("LaTeX did not create result.pdf.")
    return pdf_path


def generate_diary_pdf(
    start_date: dt.date,
    number_of_weeks: int,
    calendar_mode: str,
    final_output_dir: str,
    include_progress_graph: bool = False,
    include_constellation_map: bool = False,
    include_visible_planets: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> DiaryJobResult:
    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)
    final_pdf = os.path.join(final_output_dir, f"{job_id}_diary.pdf")

    with tempfile.TemporaryDirectory(prefix=f"diary_{job_id}_") as tmp:
        shutil.copytree(ASSET_DIR / "moon_phases", os.path.join(tmp, "moon_phases"))
        shutil.copytree(ASSET_DIR / "constellations", os.path.join(tmp, "constellations"))
        tex_path = _write_diary_tex(
            tmp,
            start_date,
            number_of_weeks,
            calendar_mode,
            include_progress_graph,
            include_constellation_map,
            latitude if include_visible_planets else None,
            longitude if include_visible_planets else None,
        )
        generated_pdf = _compile_latex(tmp, tex_path)
        shutil.copy2(generated_pdf, final_pdf)

    return DiaryJobResult(job_id=job_id, output_pdf_path=final_pdf)


def build_diary_pipeline(
    start_date: dt.date,
    number_of_weeks: int,
    calendar_mode: str,
    include_progress_graph: bool,
    include_constellation_map: bool,
    output_mode: str,
    final_output_dir: str,
    max_pages_per_split: int = 40,
    content_margin_cm: float = 0.5,
    side_by_side_prepare_for_portrait_printing: bool = True,
    flipped_a4_prepare_for_a5_printing: bool = True,
    flipped_a4_center_gap_cm: float = 0.5,
    flipped_a4_split_mode: FlippedA4SplitMode = "vector",
    flipped_a4_quality: FlippedA4Quality = "medium",
    include_visible_planets: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> DiaryJobResult:
    diary = generate_diary_pdf(
        start_date=start_date,
        number_of_weeks=number_of_weeks,
        calendar_mode=calendar_mode,
        include_progress_graph=include_progress_graph,
        include_constellation_map=include_constellation_map,
        include_visible_planets=include_visible_planets,
        latitude=latitude,
        longitude=longitude,
        final_output_dir=final_output_dir,
    )

    if output_mode == "pdf":
        return diary

    specs = [
        SourcePdfSpec(
            input_pdf_path=diary.output_pdf_path,
            same_page_parity=True,
            margin_cm=content_margin_cm,
            add_watermark=False,
        )
    ]

    if output_mode == "side_by_side":
        result = build_booklets_pipeline(
            specs=specs,
            max_pages_per_split=max_pages_per_split,
            final_output_dir=final_output_dir,
            preserve_file_parity=True,
            generate_cover=False,
            prepare_for_portrait_printing=side_by_side_prepare_for_portrait_printing,
        )
    elif output_mode == "flipped_a4":
        result = build_flipped_a4_booklets_pipeline(
            specs=specs,
            max_pages_per_split=max_pages_per_split,
            final_output_dir=final_output_dir,
            preserve_file_parity=True,
            generate_cover=False,
            render_quality=flipped_a4_quality,
            center_gap_cm=flipped_a4_center_gap_cm,
            split_mode=flipped_a4_split_mode,
            prepare_for_a5_printing=flipped_a4_prepare_for_a5_printing,
        )
    else:
        raise ValueError(f"Unsupported diary output mode: {output_mode}")

    return DiaryJobResult(job_id=result.job_id, output_pdf_path=result.output_pdf_path)
