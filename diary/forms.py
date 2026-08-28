from __future__ import annotations

from django import forms
from django.utils import timezone


class DiaryForm(forms.Form):
    OUTPUT_PDF = "pdf"
    OUTPUT_SIDE_BY_SIDE = "side_by_side"
    OUTPUT_FLIPPED_A4 = "flipped_a4"

    start_date = forms.DateField(
        label="Start date",
        initial=timezone.localdate,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
    )
    number_of_weeks = forms.IntegerField(
        label="Number of weeks",
        initial=4,
        min_value=1,
        max_value=104,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "104"}),
    )
    calendar_mode = forms.ChoiceField(
        label="Weekly agenda type",
        initial="single",
        choices=[
            ("single", "Single-page weekly calendar"),
            ("double", "Legacy double-page weekly calendar"),
        ],
        widget=forms.RadioSelect,
    )
    include_progress_graph = forms.BooleanField(
        label="Include progress graph",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    include_constellation_map = forms.BooleanField(
        label="Include monthly constellation map",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    output_mode = forms.ChoiceField(
        label="Output",
        initial=OUTPUT_PDF,
        choices=[
            (OUTPUT_PDF, "Regular diary PDF"),
            (OUTPUT_SIDE_BY_SIDE, "Side-by-side booklet"),
            (OUTPUT_FLIPPED_A4, "Flipped booklet"),
        ],
        widget=forms.RadioSelect,
    )
    max_pages_per_split = forms.IntegerField(
        label="Max pages per split",
        initial=40,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )
    content_margin_cm = forms.FloatField(
        label="Content margin (cm)",
        initial=0.5,
        min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
    )
    side_by_side_prepare_for_portrait_printing = forms.BooleanField(
        label="Prepare side-by-side for portrait printing",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    flipped_a4_prepare_for_a5_printing = forms.BooleanField(
        label="Prepare flipped booklet for A5 printing",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    flipped_a4_center_gap_cm = forms.FloatField(
        label="Flipped booklet middle separation (cm)",
        required=False,
        initial=0.5,
        min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.1"}),
    )
    flipped_a4_split_mode = forms.ChoiceField(
        label="Flipped page split method",
        required=False,
        initial="vector",
        choices=[
            ("vector", "Vector split"),
            ("raster", "Image split"),
        ],
        widget=forms.RadioSelect,
    )
    flipped_a4_quality = forms.ChoiceField(
        label="Flipped rendering quality",
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

    def clean_flipped_a4_center_gap_cm(self):
        return self.cleaned_data.get("flipped_a4_center_gap_cm") if self.cleaned_data.get("flipped_a4_center_gap_cm") is not None else 0.5

    def clean_flipped_a4_split_mode(self):
        return self.cleaned_data.get("flipped_a4_split_mode") or "vector"

    def clean_flipped_a4_quality(self):
        return self.cleaned_data.get("flipped_a4_quality") or "medium"
