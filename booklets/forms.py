# booklets/forms.py
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


class BookletForm(forms.Form):
    input_pdf = MultipleFileField(
        label="Upload PDF(s)",
        required=False,
        help_text="Select or drag one or more PDFs. You can reorder and configure them before generating.",
        widget=MultiFileInput(attrs={"multiple": True}),
    )

    processing_mode = forms.ChoiceField(
        label="Generation mode",
        required=True,
        initial="separate",
        choices=[
            ("separate", "Generate separate booklet files"),
            ("combined", "Combine booklets into one print file"),
        ],
        widget=forms.RadioSelect,
    )

    max_pages_per_split = forms.IntegerField(
        label="Max pages per split",
        required=True,
        initial=40,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    preserve_file_parity = forms.BooleanField(
        label="Preserve each file's start parity",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["input_pdf"].widget.attrs.update(
            {
                "class": "form-control",
                "accept": "application/pdf,.pdf",
            }
        )
