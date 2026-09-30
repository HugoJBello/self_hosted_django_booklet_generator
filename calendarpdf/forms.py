from django import forms


class MultiImageInput(forms.FileInput):
    allow_multiple_selected = True


class ImageFilesField(forms.FileField):
    def clean(self, data, initial=None):
        if not data:
            if self.required:
                super().clean(data, initial)
            return []
        files = data if isinstance(data, (tuple, list)) else [data]
        return [super(ImageFilesField, self).clean(item, initial) for item in files]


class SubjectFilterForm(forms.Form):
    filter_subjects = forms.BooleanField(
        label="Only include selected subjects",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    subject_filter = forms.CharField(
        label="Subject names or fragments",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. mathematics, physics, statistics",
            "autocomplete": "off",
        }),
        help_text=(
            "Separate entries with commas. A subject is included when its name contains any entry; "
            "capitalization and accents are ignored."
        ),
    )


class CalendarForm(SubjectFilterForm):
    images = ImageFilesField(
        label="Compact timetables",
        widget=MultiImageInput(attrs={"accept": ".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf", "class": "form-control"}),
        help_text="Select multiple images or PDFs. Each printed date will be placed on the calendar.",
    )
