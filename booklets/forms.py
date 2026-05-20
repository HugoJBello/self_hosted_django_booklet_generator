# booklets/forms.py
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


class BookletForm(forms.Form):
    input_pdf = MultipleFileField(
        label="Subir PDF(s)",
        required=True,
        help_text="Selecciona o arrastra uno o varios PDFs. Después podrás reordenarlos y configurar cada uno.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    processing_mode = forms.ChoiceField(
        label="Modo de generación",
        required=True,
        initial="separate",
        choices=[
            ("separate", "Generar booklets y ficheros separados"),
            ("combined", "Juntar booklets para una única impresión"),
        ],
        widget=forms.RadioSelect,
    )

    max_pages_per_split = forms.IntegerField(
        label="Máx. páginas por split",
        required=True,
        initial=40,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    preserve_file_parity = forms.BooleanField(
        label="Respetar la paridad de inicio de cada fichero al unificarlos",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["input_pdf"].widget.attrs.update(
            {
                "class": "form-control",
                "accept": "application/pdf,.pdf",
            }
        )
