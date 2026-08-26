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

    booklet_layout = forms.ChoiceField(
        label="Booklet layout",
        required=False,
        initial="side_by_side",
        choices=[
            ("side_by_side", "Side-by-side booklet"),
            ("flipped_a4", "Flipped booklet"),
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

    flipped_a4 = forms.BooleanField(
        label="Flipped A4",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    flipped_a4_quality = forms.ChoiceField(
        label="Rendering quality",
        required=False,
        initial="medium",
        choices=[
            ("very_low", "Very low"),
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("super_high", "Super high"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    flipped_a4_split_mode = forms.ChoiceField(
        label="Page split method",
        required=False,
        initial="vector",
        choices=[
            ("vector", "Vector split (keeps text and shapes)"),
            ("raster", "Image split (most reliable fallback)"),
        ],
        widget=forms.RadioSelect,
    )

    flipped_a4_center_gap_cm = forms.FloatField(
        label="Middle page separation (cm)",
        required=False,
        initial=1.0,
        min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
    )

    flipped_a4_prepare_for_a5_printing = forms.BooleanField(
        label="Prepare for A5 printing",
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

    def clean_flipped_a4_quality(self):
        return self.cleaned_data.get("flipped_a4_quality") or "medium"

    def clean_flipped_a4_split_mode(self):
        return self.cleaned_data.get("flipped_a4_split_mode") or "vector"

    def clean_flipped_a4_center_gap_cm(self):
        value = self.cleaned_data.get("flipped_a4_center_gap_cm")
        if value is None:
            return 1.0
        return value

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("flipped_a4"):
            cleaned_data["booklet_layout"] = "flipped_a4"
        else:
            cleaned_data["booklet_layout"] = cleaned_data.get("booklet_layout") or "side_by_side"
        return cleaned_data
