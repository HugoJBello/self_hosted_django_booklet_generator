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
        help_text="Puedes subir uno o varios PDFs (Ctrl/Shift). Se irán acumulando en la lista.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["input_pdf"].widget.attrs.update({"class": "form-control"})


class JoinRunForm(forms.Form):
    preserve_parity = forms.BooleanField(
        label="Preservar paridad para impresión (capítulos empiezan en impar)",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

