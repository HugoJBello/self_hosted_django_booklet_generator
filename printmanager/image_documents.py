from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path

import fitz
from PIL import Image, ImageColor, ImageOps
from pillow_heif import register_heif_opener
from django.conf import settings

register_heif_opener()

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif"}
PAPER_SIZES = {
    "a5": (419.53, 595.28),
    "a4": (595.28, 841.89),
    "a3": (841.89, 1190.55),
    "letter": (612.0, 792.0),
    "photo_4x6": (288.0, 432.0),
}
DEFAULT_IMAGE_OPTIONS = {
    "paper": "a4", "orientation": "auto", "fit": "contain",
    "margin_mm": 10.0, "horizontal": "center", "vertical": "center",
    "rotation": 0, "dpi": 300, "background": "#ffffff",
}


def is_supported_image(name):
    return Path(name or "").suffix.lower() in IMAGE_SUFFIXES


def normalize_image_options(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    options = dict(DEFAULT_IMAGE_OPTIONS)
    for key, allowed in {
        "paper": PAPER_SIZES,
        "orientation": {"auto", "portrait", "landscape"},
        "fit": {"contain", "cover", "stretch", "original"},
        "horizontal": {"left", "center", "right"},
        "vertical": {"top", "center", "bottom"},
    }.items():
        if raw.get(key) in allowed:
            options[key] = raw[key]
    try:
        options["margin_mm"] = min(50.0, max(0.0, float(raw.get("margin_mm", options["margin_mm"]))))
    except (TypeError, ValueError):
        pass
    try:
        rotation = int(raw.get("rotation", 0))
        options["rotation"] = rotation if rotation in {0, 90, 180, 270} else 0
    except (TypeError, ValueError):
        pass
    try:
        options["dpi"] = min(600, max(72, int(raw.get("dpi", options["dpi"]))))
    except (TypeError, ValueError):
        pass
    try:
        options["background"] = ImageColor.getrgb(str(raw.get("background", "#ffffff")))
        options["background"] = "#%02x%02x%02x" % options["background"]
    except (TypeError, ValueError):
        options["background"] = "#ffffff"
    return options


def image_layout(image_size, options):
    options = normalize_image_options(options)
    image_width, image_height = image_size
    if options["rotation"] in {90, 270}:
        image_width, image_height = image_height, image_width
    page_width, page_height = PAPER_SIZES[options["paper"]]
    orientation = options["orientation"]
    if orientation == "auto":
        orientation = "landscape" if image_width > image_height else "portrait"
    if orientation == "landscape" and page_width < page_height:
        page_width, page_height = page_height, page_width
    elif orientation == "portrait" and page_width > page_height:
        page_width, page_height = page_height, page_width
    margin = options["margin_mm"] * 72 / 25.4
    available_width = max(1.0, page_width - 2 * margin)
    available_height = max(1.0, page_height - 2 * margin)
    if options["fit"] == "stretch":
        draw_width, draw_height = available_width, available_height
    else:
        image_ratio = image_width / max(image_height, 1)
        area_ratio = available_width / available_height
        if options["fit"] == "cover":
            scale = available_height / image_height if image_ratio > area_ratio else available_width / image_width
        elif options["fit"] == "original":
            scale = min(72 / options["dpi"], available_width / image_width, available_height / image_height)
        else:
            scale = available_width / image_width if image_ratio > area_ratio else available_height / image_height
        draw_width, draw_height = image_width * scale, image_height * scale
    x = {"left": margin, "center": (page_width - draw_width) / 2, "right": page_width - margin - draw_width}[options["horizontal"]]
    y = {"top": margin, "center": (page_height - draw_height) / 2, "bottom": page_height - margin - draw_height}[options["vertical"]]
    return (page_width, page_height), (x, y, x + draw_width, y + draw_height), options


def convert_uploaded_image(uploaded, raw_options=None):
    directory = os.path.join(settings.MEDIA_ROOT, "print_uploads", uuid.uuid4().hex)
    os.makedirs(directory, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(uploaded.name).stem).strip(".-") or "image"
    output_path = os.path.join(directory, f"{stem}.pdf")
    uploaded.seek(0)
    source_name = os.path.basename(uploaded.name) or f"{stem}.png"
    source_path = os.path.join(directory, source_name)
    with open(source_path, "wb") as destination:
        for chunk in uploaded.chunks():
            destination.write(chunk)
    uploaded.seek(0)
    with Image.open(uploaded) as source:
        source = ImageOps.exif_transpose(source)
        if source.width * source.height > 100_000_000:
            raise ValueError("The image is too large to process safely.")
        page_size, rect, options = image_layout(source.size, raw_options)
        rotations = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
        if options["rotation"]:
            source = source.transpose(rotations[options["rotation"]])
        background = Image.new("RGB", source.size, ImageColor.getrgb(options["background"]))
        if source.mode in {"RGBA", "LA"}:
            background.paste(source, mask=source.getchannel("A"))
        else:
            background.paste(source.convert("RGB"))
        encoded = io.BytesIO()
        background.save(encoded, format="JPEG", quality=94, optimize=True)
    document = fitz.open()
    try:
        page = document.new_page(width=page_size[0], height=page_size[1])
        page.draw_rect(page.rect, color=None, fill=tuple(channel / 255 for channel in ImageColor.getrgb(options["background"])))
        page.insert_image(fitz.Rect(*rect), stream=encoded.getvalue(), keep_proportion=False)
        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()
    return output_path, f"{stem}.pdf", options, source_path, source_name
