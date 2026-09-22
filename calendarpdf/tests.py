from datetime import date
from unittest.mock import patch

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from .services import Event, _column_bounds, _extract_column, consolidate_events, make_pdf
from .summary import category, hour_totals, overlapping_events


class CalendarTests(SimpleTestCase):
    def test_dates_follow_their_own_subject(self):
        text = """41953 (445) - MATEMÁTICAS I
10:00-11:00
Grupo: 1T
(AULA 14)
07/09/26 14/09/26
41381 (555) - ESTADÍSTICA
12:00-13:00
Grupo: 1A
(AULA DE INFORMÁTICA I-4)
14/09/26
"""
        events = _extract_column(text, 0)
        self.assertIn(Event(date(2026, 9, 7), "10:00", "MATEMÁTICAS I", "1T", "AULA 14", "11:00"), events)
        self.assertIn(Event(date(2026, 9, 14), "12:00", "ESTADÍSTICA", "1A", "AULA DE INFORMÁTICA I-4", "13:00"), events)
        self.assertEqual(len(events), 3)

    def test_pdf_appends_one_consolidated_page_with_hours(self):
        events = {
            Event(date(2026, 9, 7), "10:00", "Mathematics I", "1T", "Room 14", "11:00"),
            Event(date(2026, 9, 14), "10:00", "Mathematics I", "1T", "Room 14", "11:00"),
            Event(date(2026, 10, 2), "08:00", "Statistics", "1L", "Computer Lab I-4", "10:00"),
            Event(date(2026, 10, 5), "12:00", "Statistics", "1A", "Room 07", "13:00"),
        }
        with fitz.open(stream=make_pdf(events), filetype="pdf") as document:
            self.assertEqual(document.page_count, 3)
            self.assertIn("September 2026", document[0].get_text())
            self.assertIn("Monday", document[0].get_text())
            self.assertIn("Mathematics I", document[0].get_text())
            self.assertIn("Room 14", document[0].get_text())
            self.assertIn("10h → 11h", document[0].get_text())
            appendix = document[-1].get_text()
            self.assertIn("Consolidated weekly timetable", appendix)
            self.assertIn("Sunday", appendix)
            self.assertIn("Dates: 07 Sep 2026, 14 Sep 2026", appendix)
            self.assertIn("10h → 11h", appendix)
            self.assertIn("Total scheduled hours: 5 h", appendix)
            self.assertIn("Laboratory: 2 h", appendix)
            self.assertIn("Theory: 2 h", appendix)
            self.assertIn("Classroom practice: 1 h", appendix)
        self.assertEqual(hour_totals(events)["Laboratory"], 120)

    def test_summary_lists_every_date_without_clipping(self):
        days = {date(2026, 9, day) for day in range(1, 29)}
        events = {Event(day, "09:30", "A subject with a long arbitrary name",
                        "Group-Z", "An unusually long classroom name", "12:00") for day in days}
        with fitz.open(stream=make_pdf(events), filetype="pdf") as document:
            appendix = document[-1].get_text()
            normalized_appendix = " ".join(appendix.split())
            for day in days:
                self.assertIn(day.strftime("%d %b %Y"), normalized_appendix)
            self.assertIn("9h30 → 12h", appendix)

    def test_variant_labels_dates_times_and_class_types(self):
        text = """Thursday
PHYSICS
8h30 a 10h00
GR. 2L
SALA DE ORDENADORES 3
Tipo: Seminario
2026-09-10
42142 - QUÍMICA
10-12
Grupo 1A
LABORATORIO QUÍMICA 2
10/09/2026
17 Sep 2026
"""
        events = _extract_column(text, 3)
        self.assertIn(Event(date(2026, 9, 10), "08:30", "PHYSICS", "2L",
                            "SALA DE ORDENADORES 3", "10:00", "Seminar"), events)
        self.assertIn(Event(date(2026, 9, 10), "10:00", "QUÍMICA", "1A",
                            "LABORATORIO QUÍMICA 2", "12:00"), events)
        self.assertIn(Event(date(2026, 9, 17), "10:00", "QUÍMICA", "1A",
                            "LABORATORIO QUÍMICA 2", "12:00"), events)
        self.assertEqual(category(next(event for event in events if event.subject == "PHYSICS")), "Seminar")
        self.assertEqual(hour_totals(events)["Seminar"], 90)
        self.assertEqual(hour_totals(events)["Laboratory"], 240)
        room_only = Event(date(2026, 9, 24), "10:00", "Diseño", room="AULA DE INFORMÁTICA I-4")
        self.assertEqual(category(room_only), "Laboratory")
        self.assertEqual(room_only.room, "AULA DE INFORMÁTICA I-4")

    def test_arbitrary_mixed_case_subject_and_room_are_kept_verbatim(self):
        text = """Taller de creación sonora avanzada
16:30-18:15
Grupo: Zeta-9
Espacio: Nave experimental «La Fábrica»
22/09/26
"""
        events = _extract_column(text, 1)
        self.assertIn(Event(date(2026, 9, 22), "16:30", "Taller de creación sonora avanzada",
                            "ZETA-9", "Espacio Nave experimental «La Fábrica»", "18:15"), events)

    def test_overlap_requires_shared_time_on_same_date(self):
        day = date(2026, 9, 7)
        first = Event(day, "09:00", "Physics", end_time="10:30")
        overlap = Event(day, "10:00", "Chemistry", end_time="11:00")
        adjacent = Event(day, "11:00", "Biology", end_time="12:00")
        next_day = Event(date(2026, 9, 8), "09:00", "Math", end_time="10:30")
        self.assertEqual(overlapping_events({first, overlap, adjacent, next_day}), {first, overlap})
        with fitz.open(stream=make_pdf({first, overlap, adjacent, next_day}), filetype="pdf") as document:
            red_dots = [drawing for drawing in document[0].get_drawings()
                        if drawing.get("fill") and abs(drawing["fill"][0] - 0.83) < .01
                        and abs(drawing["fill"][1] - 0.15) < .01]
            self.assertGreaterEqual(len(red_dots), 3)

    def test_overlapping_blocks_for_the_same_class_are_consolidated(self):
        day = date(2026, 10, 28)
        outer = Event(day, "12:00", "MATEMÁTICAS Y COMPUTACIÓN", "1L",
                      "AULA DE INFORMÁTICA 1-4", "14:00")
        contained = Event(day, "13:00", " matemáticas y computación ", "1l",
                          "AULA DE INFORMÁTICA 1-4", "14:00")
        other_group = Event(day, "13:00", "MATEMÁTICAS Y COMPUTACIÓN", "2L",
                            "AULA DE INFORMÁTICA 1-4", "14:00")
        result = consolidate_events({outer, contained, other_group})
        self.assertEqual(result, {outer, other_group})
        self.assertEqual(overlapping_events(result), {outer, other_group})

    def test_weekend_classes_are_visible_in_month(self):
        events = {Event(date(2026, 9, 12), "09:00", "Workshop", end_time="11:00")}
        with fitz.open(stream=make_pdf(events), filetype="pdf") as document:
            self.assertIn("Saturday", document[0].get_text())
            self.assertIn("Workshop", document[0].get_text())

    def test_detects_seven_day_header(self):
        names = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
        lines = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
        for index, name in enumerate(names):
            lines.append(f"5\t1\t1\t1\t1\t{index + 1}\t{200 + index * 300}\t20\t100\t20\t90\t{name}")
        result = type("Result", (), {"returncode": 0, "stdout": "\n".join(lines).encode()})()
        with patch("calendarpdf.services.subprocess.run", return_value=result):
            bounds = _column_bounds(Image.new("RGB", (1400, 300), "white"))
        self.assertEqual([weekday for weekday, _, _ in bounds], list(range(7)))

    def test_unreadable_file_reports_its_name(self):
        uploaded = SimpleUploadedFile("broken.png", b"not an image", content_type="image/png")
        response = self.client.post("/pdf_manager/calendar/", {"images": [uploaded]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "broken.png")
        self.assertContains(response, "invalid or damaged")

    def test_one_unreadable_timetable_blocks_incomplete_pdf(self):
        files = [SimpleUploadedFile(name, b"image", content_type="image/png")
                 for name in ("first.png", "second.png")]
        event = Event(date(2026, 9, 7), "10:00", "Mathematics")
        with patch("calendarpdf.services.extract_events", side_effect=({event}, set())):
            response = self.client.post("/pdf_manager/calendar/", {"images": files})
        self.assertContains(response, "second.png: no dated classes could be read")
