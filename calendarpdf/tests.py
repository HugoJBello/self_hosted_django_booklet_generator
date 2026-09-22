from datetime import date

import fitz
from django.test import SimpleTestCase

from .services import Event, _extract_column, make_pdf
from .summary import hour_totals


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
        self.assertIn(Event(date(2026, 9, 7), "10:00", "Mathematics I", "1T", "Room 14", "11:00"), events)
        self.assertIn(Event(date(2026, 9, 14), "12:00", "Statistics", "1A", "Computer Lab I-4", "13:00"), events)
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
            appendix = document[-1].get_text()
            self.assertIn("Consolidated weekly timetable", appendix)
            self.assertIn("Sunday", appendix)
            self.assertIn("2 dates", appendix)
            self.assertIn("Total scheduled hours: 5 h", appendix)
            self.assertIn("Laboratory: 2 h", appendix)
            self.assertIn("Theory: 2 h", appendix)
            self.assertIn("Classroom practice: 1 h", appendix)
        self.assertEqual(hour_totals(events)["Laboratory"], 120)
