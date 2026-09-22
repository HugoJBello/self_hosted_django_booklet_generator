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


class CalendarForm(forms.Form):
    images = ImageFilesField(
        label="Compact timetables",
        widget=MultiImageInput(attrs={"accept": ".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf", "class": "form-control"}),
        help_text="Select multiple images or PDFs. Each printed date will be placed on the calendar.",
    )
