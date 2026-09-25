import json
import re

from django import forms

from .models import Printer


def _json_object(value):
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise forms.ValidationError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise forms.ValidationError("Enter a JSON object with option/value pairs.")
    return {str(key): str(item) for key, item in parsed.items()}


class PrinterForm(forms.ModelForm):
    default_options_text = forms.CharField(label="Default CUPS options (JSON)", required=False, widget=forms.Textarea(attrs={"rows": 5, "placeholder": '{"media": "A4", "sides": "two-sided-long-edge"}'}))

    class Meta:
        model = Printer
        fields = ("name", "description", "location", "device_uri", "driver", "is_enabled", "is_shared", "is_default")
        widgets = {"device_uri": forms.TextInput(attrs={"placeholder": "ipp://printer.local/ipp/print"}), "driver": forms.TextInput(attrs={"placeholder": "everywhere"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["default_options_text"].initial = json.dumps(self.instance.default_options, indent=2, ensure_ascii=False)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"

    def clean_default_options_text(self):
        return _json_object(self.cleaned_data["default_options_text"])

    def save(self, commit=True):
        instance = super().save(False)
        instance.default_options = self.cleaned_data["default_options_text"]
        if commit:
            instance.save()
        return instance


class PrintForm(forms.Form):
    printer = forms.ModelChoiceField(queryset=Printer.objects.none(), empty_label=None)
    source = forms.ChoiceField(choices=(("upload", "Upload a PDF"), ("recent", "Choose from recent activity")), widget=forms.RadioSelect)
    document = forms.FileField(required=False, widget=forms.FileInput(attrs={"accept": "application/pdf,.pdf"}))
    artifact = forms.ChoiceField(required=False, choices=())
    copies = forms.IntegerField(min_value=1, max_value=999, initial=1)
    page_ranges = forms.CharField(required=False, help_text="Examples: 1-4, 7, 10-12")
    media = forms.CharField(required=False)
    sides = forms.ChoiceField(required=False, choices=(("", "Printer default"), ("one-sided", "One-sided"), ("two-sided-long-edge", "Duplex, long edge"), ("two-sided-short-edge", "Duplex, short edge")))
    orientation_requested = forms.ChoiceField(required=False, choices=(("", "Automatic"), ("3", "Portrait"), ("4", "Landscape")))
    print_color_mode = forms.ChoiceField(required=False, choices=(("", "Printer default"), ("color", "Color"), ("monochrome", "Monochrome")))
    scaling = forms.ChoiceField(required=False, choices=(("", "Original size"), ("fit-to-page", "Fit to page"), ("fill", "Fill page")))
    collate = forms.BooleanField(required=False, initial=True)
    extra_options = forms.CharField(required=False, label="Additional CUPS options (JSON)", widget=forms.Textarea(attrs={"rows": 4, "placeholder": '{"InputSlot": "Tray2"}'}))

    def __init__(self, *args, artifacts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["printer"].queryset = Printer.objects.filter(is_enabled=True)
        self.fields["artifact"].choices = [(str(item.pk), f"{item.activity.created_at:%Y-%m-%d %H:%M} · {item.name}") for item in artifacts]
        for field in self.fields.values():
            if isinstance(field.widget, forms.RadioSelect):
                continue
            field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean_page_ranges(self):
        value = self.cleaned_data["page_ranges"].replace(" ", "")
        if value and not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", value):
            raise forms.ValidationError("Use page numbers and ranges, for example 1-4,7.")
        return value

    def clean_extra_options(self):
        return _json_object(self.cleaned_data["extra_options"])

    def clean(self):
        data = super().clean()
        source = data.get("source")
        if source == "upload" and not data.get("document"):
            self.add_error("document", "Select a PDF to upload.")
        if source == "recent" and not data.get("artifact"):
            self.add_error("artifact", "Select a recent PDF.")
        document = data.get("document")
        if document and document.content_type not in ("application/pdf", "application/x-pdf") and not document.name.lower().endswith(".pdf"):
            self.add_error("document", "Only PDF documents are accepted.")
        return data
