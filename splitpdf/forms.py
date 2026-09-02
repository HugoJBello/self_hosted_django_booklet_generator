from __future__ import annotations

from django import forms


class SplitPdfForm(forms.Form):
    input_pdf = forms.FileField(
        label="Upload PDF",
        required=False,
        help_text="Upload a PDF with an embedded table of contents.",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "application/pdf,.pdf"}),
    )

    selected_level = forms.ChoiceField(
        label="Split level",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    apply_booklets = forms.BooleanField(
        label="Apply booklet layout to generated PDFs",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
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
        label="Max pages per booklet split",
        required=False,
        initial=40,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )

    margin_cm = forms.FloatField(
        label="Outer margin (cm)",
        required=False,
        initial=1.0,
        min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
    )

    side_by_side_prepare_for_portrait_printing = forms.BooleanField(
        label="Prepare for portrait printing",
        required=False,
        initial=True,
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
            ("vector", "Vector split"),
            ("raster", "Image split"),
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

    def __init__(self, *args, level_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_level"].choices = level_choices or []

    def clean_selected_level(self):
        value = self.cleaned_data.get("selected_level")
        if value in (None, ""):
            return None
        return int(value)

    def clean_max_pages_per_split(self):
        return self.cleaned_data.get("max_pages_per_split") or 40

    def clean_margin_cm(self):
        value = self.cleaned_data.get("margin_cm")
        if value is None:
            return 1.0
        return value

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
        cleaned_data["booklet_layout"] = cleaned_data.get("booklet_layout") or "side_by_side"
        return cleaned_data
