# booklets/forms.py
from __future__ import annotations

from django import forms


class BookletForm(forms.Form):
    input_pdf = forms.FileField(
        label="Subir PDF",
        required=True,
        help_text="Selecciona un PDF para procesar.",
    )

    max_pages_per_split = forms.IntegerField(
        label="Máx. páginas por split",
        required=True,
        initial=40,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    same_page_parity = forms.ChoiceField(
        label="Paridad de inicio (same_page_parity)",
        required=True,
        initial="true",
        choices=[
            ("true", "true (splits empiezan en impar 1-based / índice 0 par)"),
            ("false", "false (añade página en blanco al principio)"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    margin = forms.FloatField(
        label="Margen externo (cm)",
        required=True,
        initial=1.0,
        min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
    )

    add_watermark = forms.BooleanField(
        label="Añadir marca de agua (*)",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Estilado Bootstrap para el input file
        self.fields["input_pdf"].widget.attrs.update({"class": "form-control"})

