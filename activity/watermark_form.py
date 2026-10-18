from django import forms


class WatermarkFields(forms.Form):
    """Reusable controls for PDF producing forms."""

    watermark = forms.BooleanField(
        label="Add date/time watermark",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    watermark_filename = forms.BooleanField(
        label="Include file name",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
