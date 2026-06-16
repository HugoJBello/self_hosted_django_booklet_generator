# joinpdf/forms.py
from __future__ import annotations

from django import forms


class MultiFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        if data is None:
            return []
        if isinstance(data, (list, tuple)):
            return [forms.FileField.clean(self, d, initial) for d in data]
        return [forms.FileField.clean(self, data, initial)]


class JoinUploadForm(forms.Form):
    input_pdf = MultipleFileField(
        label="Subir PDF(s)",
        required=False,  # aquí permitimos POST de Join sin archivos
        help_text="Selecciona o arrastra uno o varios PDFs. Se irán acumulando en la lista.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["input_pdf"].widget.attrs.update(
            {
                "class": "form-control",
                "accept": "application/pdf,.pdf",
            }
        )


class JoinRunForm(forms.Form):
    preserve_parity = forms.BooleanField(
        label="Preservar paridad para impresión (capítulos empiezan en impar)",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    generate_cover = forms.BooleanField(
        label="Generar portada con indice",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
