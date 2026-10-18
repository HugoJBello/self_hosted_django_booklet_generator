"""Small, optional footer on finished PDF files."""

import os
import tempfile
from pathlib import Path

import fitz
from django.utils import timezone

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def stamp_pdf(path: str, *, filename: str = "", generated_at=None,
              booklet: bool = False, flipped: bool = False) -> None:
    """Overlay a footer on the first and last physical pages, without resizing pages."""
    generated_at = timezone.localtime(generated_at or timezone.now())
    label = generated_at.strftime("%d/%m/%y %H:%M")
    basename = os.path.basename(filename)
    with fitz.open(path) as document:
        if not document.page_count:
            return
        font = fitz.Font(fontfile=str(FONT_PATH)) if FONT_PATH.exists() else fitz.Font("helv")
        font_name = "pdf_footer" if FONT_PATH.exists() else "helv"
        corners = ((0, True, flipped), (0, False, False)) if booklet else ((0, True, False), (document.page_count - 1, False, False))
        for page_number, right, top in corners:
            page = document[page_number]
            if font_name != "helv":
                page.insert_font(fontname=font_name, fontfile=str(FONT_PATH))
            width, height = page.rect.width, page.rect.height
            # 10 mm maximum height. The star sits closest to the physical corner.
            font_size = 6.5
            inset = 14.0  # About 5 mm from the trim edge for ordinary printers.
            baseline = inset + 12.0 if top else height - inset - 2.0
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
            if right:
                put(star, width - inset - star_width, baseline)
                text_right = width - inset - star_width - 3
                for line_index, line in enumerate(lines):
                    line_width = font.text_length(line, fontsize=font_size)
                    line_y = baseline + 7.5 * line_index if top else baseline - 7.5 * (len(lines) - 1 - line_index)
                    put(line, text_right - line_width, line_y)
            else:
                put(star, inset, baseline)
                text_left = inset + star_width + 3
                for line_index, line in enumerate(lines):
                    put(line, text_left, baseline - 7.5 * (len(lines) - 1 - line_index))

        directory = os.path.dirname(path) or "."
        descriptor, temporary = tempfile.mkstemp(suffix=".pdf", dir=directory)
        os.close(descriptor)
        try:
            document.save(temporary, garbage=4, deflate=True)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
