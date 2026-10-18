"""Small, optional footer on finished PDF files."""

import os
import tempfile
from pathlib import Path

import fitz
from django.utils import timezone

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def numbered_sections(ranges: tuple[tuple[int, int], ...], *, parent: int | None = None) -> list[tuple[int, int, str]]:
    """Build stable sN labels, nesting pieces under a parent output when needed."""
    return [
        (start, end, f"s{parent}.{index}" if parent is not None and len(ranges) > 1
         else f"s{parent}" if parent is not None else f"s{index}")
        for index, (start, end) in enumerate(ranges, start=1)
    ]


def stamp_pdf(path: str, *, filename: str = "", generated_at=None,
              section_marks: tuple[tuple[int, int, str], ...] | list[tuple[int, int, str]] = ()) -> None:
    """Overlay a footer on the first and last physical pages, without resizing pages."""
    generated_at = timezone.localtime(generated_at or timezone.now())
    label = generated_at.strftime("%d/%m/%y %H:%M")
    basename = os.path.basename(filename)
    with fitz.open(path) as document:
        if not document.page_count:
            return
        font = fitz.Font(fontfile=str(FONT_PATH)) if FONT_PATH.exists() else fitz.Font("helv")
        font_name = "pdf_footer" if FONT_PATH.exists() else "helv"
        footer_width = 0.0
        for first, last, _ in section_marks:
            if first < 0 or last < first or last >= document.page_count:
                raise ValueError("Invalid watermark section range.")
        corners = (tuple(corner for first, last, _ in section_marks
                         for corner in ((first, True), (last, False)))
                   if section_marks else ((0, True), (document.page_count - 1, False)))
        for page_number, right in corners:
            page = document[page_number]
            if font_name != "helv":
                page.insert_font(fontname=font_name, fontfile=str(FONT_PATH))
            width, height = page.rect.width, page.rect.height
            # 10 mm maximum height. The star sits closest to the physical corner.
            font_size = 6.5
            inset = 14.0  # About 5 mm from the trim edge for ordinary printers.
            baseline = height - inset - 2.0
            available = min(width * 0.44, 220.0)
            lines = [label]
            if basename:
                name = basename
                while name and font.text_length(name, fontsize=font_size) > available:
                    name = name[:-4] + "..." if len(name) > 4 else ""
                lines.append(name)
            def put(text, visual_x, visual_y):
                point = fitz.Point(visual_x, visual_y) * page.derotation_matrix
                page.insert_text(point, text, fontsize=font_size, fontname=font_name,
                                 color=(0.32, 0.32, 0.32), rotate=(-page.rotation) % 360,
                                 overlay=True)

            star = "*"
            star_width = font.text_length(star, fontsize=font_size)
            footer_width = star_width + 3 + max(font.text_length(line, fontsize=font_size) for line in lines)
            if right:
                put(star, width - inset - star_width, baseline)
                text_right = width - inset - star_width - 3
                for line_index, line in enumerate(lines):
                    line_width = font.text_length(line, fontsize=font_size)
                    put(line, text_right - line_width, baseline - 7.5 * (len(lines) - 1 - line_index))
            else:
                put(star, inset, baseline)
                text_left = inset + star_width + 3
                for line_index, line in enumerate(lines):
                    put(line, text_left, baseline - 7.5 * (len(lines) - 1 - line_index))

        for first, last, section_label in section_marks:
            for page_number, right in ((first, True),) if first == last else ((first, True), (last, False)):
                page = document[page_number]
                if font_name != "helv":
                    page.insert_font(fontname=font_name, fontfile=str(FONT_PATH))
                label_width = font.text_length(section_label, fontsize=5.8)
                # Keep the section index on the footer line, beside the general mark.
                # This preserves the original footer's height and avoids added margins.
                neighbor_width = footer_width + 8.0 if (page_number, right) in corners else 0.0
                visual_x = (page.rect.width - 14.0 - neighbor_width - label_width
                            if right else 14.0 + neighbor_width)
                visual_y = page.rect.height - 16.0
                point = fitz.Point(visual_x, visual_y) * page.derotation_matrix
                page.insert_text(point, section_label, fontsize=5.8, fontname=font_name,
                                 color=(0.42, 0.42, 0.42), rotate=(-page.rotation) % 360,
                                 overlay=True)

        directory = os.path.dirname(path) or "."
        descriptor, temporary = tempfile.mkstemp(suffix=".pdf", dir=directory)
        os.close(descriptor)
        try:
            document.save(temporary, garbage=4, deflate=True)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
