from datetime import datetime

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Shift, StaffProfile


# ---------------------------------------------------------------- Users

class StaffForm(forms.Form):
    """
    One form for both creating and editing a person.
    - Creating: shows the Role field.
    - Editing: Role is hidden (staff ID and role never change).
    """

    role = forms.ChoiceField(choices=StaffProfile.Role.choices, initial=StaffProfile.Role.GUARD)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone_number = forms.CharField(max_length=20)
    address = forms.CharField(max_length=255, required=False, help_text="Required for guards.")
    license_number = forms.CharField(
        label="Security licence number", max_length=50, required=False,
        help_text="Required for guards.",
    )

    def __init__(self, *args, user=None, role=None, **kwargs):
        # user = the existing login account being edited (None when creating)
        self.existing_user = user
        self.fixed_role = role
        super().__init__(*args, **kwargs)
        if user is not None:
            del self.fields["role"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        others = User.objects.filter(email__iexact=email)
        if self.existing_user:
            others = others.exclude(pk=self.existing_user.pk)
        if others.exists():
            raise forms.ValidationError("Another user already has this email address.")
        return email

    def clean_license_number(self):
        number = self.cleaned_data["license_number"].strip().upper()
        if number:
            others = StaffProfile.objects.filter(license_number__iexact=number)
            if self.existing_user:
                others = others.exclude(user=self.existing_user)
            if others.exists():
                raise forms.ValidationError("Another user already has this licence number.")
        return number

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role", self.fixed_role)
        if role == StaffProfile.Role.GUARD:
            for field in ("address", "license_number"):
                if not cleaned.get(field) and field not in self.errors:
                    self.add_error(field, "This field is required for guards.")
        return cleaned


def profile_initial(profile):
    """Pre-fill the edit form with a person's current details."""
    user = profile.user
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone_number": profile.phone_number,
        "address": profile.address,
        "license_number": profile.license_number,
    }


# ---------------------------------------------------------------- Shifts

def _time_24h_widget():
    """A plain text box that only accepts 24-hour time like 07:00 or 22:30."""
    return forms.TimeInput(
        format="%H:%M",
        attrs={
            "placeholder": "HH:MM, e.g. 07:00",
            "pattern": "([01][0-9]|2[0-3]):[0-5][0-9]",
            "title": "24-hour time, e.g. 07:00 or 22:30",
            "inputmode": "numeric",
        },
    )


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ["hospital_name", "ward", "shift_date", "start_time", "end_time", "uniform", "rules"]
        labels = {
            "ward": "Ward name",
            "shift_date": "Date",
            "start_time": "Start time (24-hour)",
            "end_time": "Finish time (24-hour)",
        }
        help_texts = {
            "end_time": "If earlier than the start time, the shift finishes the next day.",
            "uniform": "e.g. White shirt, navy cargo pants, black boots",
        }
        widgets = {
            "shift_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "start_time": _time_24h_widget(),
            "end_time": _time_24h_widget(),
            "rules": forms.Textarea(attrs={"rows": 4}),
        }

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get("shift_date")
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")

        if date and start and end:
            if start == end:
                self.add_error("end_time", "Finish time must be different from start time.")
            start_at = timezone.make_aware(datetime.combine(date, start))
            if start_at <= timezone.now():
                self.add_error("start_time", "The shift must start in the future.")
        return cleaned