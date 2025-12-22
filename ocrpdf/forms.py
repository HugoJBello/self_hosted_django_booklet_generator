# ocrpdf/forms.py
from __future__ import annotations

from django import forms


class MultiFileInput(forms.FileInput):
    """
    Widget que permite seleccionar múltiples ficheros.
    """
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    Campo que acepta uno o varios ficheros.
    Devuelve siempre una lista de UploadedFile.
    """

    def clean(self, data, initial=None):
        # data puede ser UploadedFile o lista/tupla de UploadedFile
        if data is None:
            return []

        # OJO: en Python 3.11, super() sin args dentro de list comprehensions puede fallar.
        # Por eso llamamos explícitamente al método base.
        if isinstance(data, (list, tuple)):
            return [forms.FileField.clean(self, d, initial) for d in data]

        return [forms.FileField.clean(self, data, initial)]


class OcrPdfForm(forms.Form):
    input_pdf = MultipleFileField(
        label="Subir PDF(s)",
        required=True,
        help_text="Selecciona uno o varios PDFs (Ctrl/Shift) para procesar.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    language = forms.CharField(
        label="Idioma OCR (tesseract)",
        required=False,
        initial="spa",
        help_text="Ej: spa, eng, spa+eng",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    optimize = forms.ChoiceField(
        label="Optimización",
        required=True,
        initial="2",
        choices=[
            ("0", "0 – sin optimizar"),
            ("1", "1 – ligera"),
            ("2", "2 – media"),
            ("3", "3 – máxima"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    deskew = forms.BooleanField(
        label="Enderezar páginas (deskew)",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    rotate_pages = forms.BooleanField(
        label="Auto-rotación de páginas",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    force_ocr = forms.BooleanField(
        label="Forzar OCR aunque el PDF ya tenga texto",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Igual que en BookletForm
        self.fields["input_pdf"].widget.attrs.update({"class": "form-control"})

