from pathlib import Path

from django import forms

from printmanager.forms import MultipleFileField, MultipleFileInput
from printmanager.image_documents import IMAGE_SUFFIXES, PAPER_SIZES

OFFICE_SUFFIXES = {".doc", ".docx", ".odt"}


class ConvertDocumentsForm(forms.Form):
    documents = MultipleFileField(
        label="Documents or images",
        widget=MultipleFileInput(attrs={
            "accept": ".doc,.docx,.odt,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.heic,.heif",
            "class": "form-control",
        }),
    )
    paper = forms.ChoiceField(choices=tuple((key, key.upper()) for key in PAPER_SIZES), initial="a4")
    orientation = forms.ChoiceField(choices=(("auto", "Automatic"), ("portrait", "Portrait"), ("landscape", "Landscape")), initial="auto")
    fit = forms.ChoiceField(choices=(("contain", "Fit without cropping"), ("cover", "Fill and crop"), ("stretch", "Stretch"), ("original", "Original size")), initial="contain")
    margin_mm = forms.DecimalField(min_value=0, max_value=50, initial=10, decimal_places=1, label="Image margin (mm)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean_documents(self):
        files = self.cleaned_data["documents"]
        if len(files) > 10:
            raise forms.ValidationError("Select no more than 10 files at a time.")
        allowed = OFFICE_SUFFIXES | IMAGE_SUFFIXES
        for uploaded in files:
            if Path(uploaded.name).suffix.lower() not in allowed:
                raise forms.ValidationError(f"{uploaded.name}: unsupported file type.")
            if uploaded.size > 25 * 1024 * 1024:
                raise forms.ValidationError(f"{uploaded.name}: exceeds 25 MB.")
        return files
