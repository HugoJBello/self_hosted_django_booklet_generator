"""Portable, descriptive names for files presented to users.

Storage paths remain opaque and unique.  These names are metadata used for
downloads and activity history, so two runs with the same source can never
overwrite or hide one another.
"""
import os
import re
import unicodedata


OPERATION_NAMES = {
    "booklets": "booklet", "joinpdf": "joined", "splitpdf": "split",
    "ocrpdf": "ocr", "diary": "diary", "calendarpdf": "calendar",
}
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def portable_stem(value, fallback="document", max_length=72):
    value = os.path.splitext(os.path.basename(str(value or "")))[0]
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value)
    value = re.sub(r"[\s._-]+", "-", value).strip(" .-_").lower()
    if not value or value in WINDOWS_RESERVED:
        value = fallback
    return value[:max_length].rstrip(" .-_") or fallback


def generated_pdf_name(*, source_names=(), tool, detail="", index=None, used_names=None):
    sources = [name for name in source_names if name]
    source = portable_stem(sources[0] if sources else tool)
    if len(sources) > 1:
        source = f"{source}-and-{len(sources) - 1}-more"
    operation = OPERATION_NAMES.get(tool, portable_stem(tool, "generated"))
    raw_detail = re.sub(r"^[0-9a-f]{20,}[_-]?", "", os.path.splitext(os.path.basename(str(detail or "")))[0], flags=re.I)
    if index is not None:
        raw_detail = re.sub(r"^\d+[\s_-]+", "", raw_detail)
    detail_stem = portable_stem(raw_detail, "", 48) if raw_detail else ""
    generic = {operation, "result", "output", f"{operation}-for-printing", "booklets-for-printing", "flipped-a4-booklets-for-printing"}
    parts = [source, operation]
    if index is not None:
        parts.append(f"{index:02d}")
    if detail_stem and detail_stem not in generic and detail_stem not in parts:
        parts.append(detail_stem)
    name = "_".join(parts)[:116].rstrip(" .-_") + ".pdf"
    if used_names is None:
        return name
    base = name[:-4]
    candidate, suffix = name, 2
    while candidate.casefold() in used_names:
        candidate = f"{base[:110]}_{suffix}.pdf"
        suffix += 1
    used_names.add(candidate.casefold())
    return candidate
