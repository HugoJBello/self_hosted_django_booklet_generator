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
        label="Upload PDF(s)",
        required=False,
        help_text="Select or drag one or more PDFs. They will be added to the current list.",
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
        label="Preserve print parity (sections start on odd pages)",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    generate_cover = forms.BooleanField(
        label="Add cover index",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
