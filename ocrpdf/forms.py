# ocrpdf/forms.py
from __future__ import annotations

from django import forms


class MultiFileInput(forms.FileInput):
    """
    Widget that allows selecting multiple files.
    """
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    Field that accepts one or more files.
    Always returns a list of UploadedFile objects.
    """

    def clean(self, data, initial=None):
        # data can be an UploadedFile or a list/tuple of UploadedFile objects.
        if data is None:
            return []

        # In Python 3.11, no-arg super() can fail inside list comprehensions.
        # Call the base method explicitly.
        if isinstance(data, (list, tuple)):
            return [forms.FileField.clean(self, d, initial) for d in data]

        return [forms.FileField.clean(self, data, initial)]


class OcrPdfForm(forms.Form):
    input_pdf = MultipleFileField(
        label="Upload PDF(s)",
        required=True,
        help_text="Select one or more PDFs (Ctrl/Shift) to process.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    language = forms.CharField(
        label="OCR language (Tesseract)",
        required=False,
        initial="spa",
        help_text="Examples: spa, eng, spa+eng",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    optimize = forms.ChoiceField(
        label="Optimization",
        required=True,
        initial="2",
        choices=[
            ("0", "0 - none"),
            ("1", "1 - light"),
            ("2", "2 - medium"),
            ("3", "3 - maximum"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    deskew = forms.BooleanField(
        label="Deskew pages",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    rotate_pages = forms.BooleanField(
        label="Auto-rotate pages",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    force_ocr = forms.BooleanField(
        label="Force OCR even if the PDF already has text",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["input_pdf"].widget.attrs.update({"class": "form-control"})
