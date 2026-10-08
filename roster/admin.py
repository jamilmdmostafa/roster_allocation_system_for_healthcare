from django import forms
from django.contrib import admin

from .models import AuditLog, Notification, Shift, ShiftResponse, StaffProfile
from .services import check_guard_availability


class ShiftAdminForm(forms.ModelForm):
    """Makes the Django admin obey the same rules as the app."""

    class Meta:
        model = Shift
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        guard = cleaned.get("assigned_guard")
        status = cleaned.get("status")

        # Keep status and guard consistent
        if guard and status == Shift.Status.OPEN:
            cleaned["status"] = Shift.Status.ASSIGNED
        if status == Shift.Status.ASSIGNED and not guard:
            self.add_error("assigned_guard", "Choose a guard, or set the status to Open.")

        # Check overlap and the 6-hour rest rule
        date = cleaned.get("shift_date")
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if guard and date and start and end and cleaned.get("status") == Shift.Status.ASSIGNED:
            draft = Shift(pk=self.instance.pk, shift_date=date, start_time=start, end_time=end)
            draft.calculate_times()
            reason = check_guard_availability(guard, draft)
            if reason:
                self.add_error("assigned_guard", f"{guard.staff_id} can't take this shift: {reason}.")
        return cleaned

admin.site.site_header = "Roster Management"
admin.site.site_title = "Roster Management"
admin.site.index_title = "Site Administration"


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("staff_id", "full_name", "role", "phone_number", "license_number")
    list_filter = ("role",)
    search_fields = ("staff_id", "user__first_name", "user__last_name", "user__email", "phone_number")
    readonly_fields = ("staff_id",)

    def has_add_permission(self, request):
    def has_delete_permission(self, request, obj=None):
        # Staff records are kept for the audit history, never deleted.
        return False
        # New users are created through the web app so they get a proper ID.
       


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    form = ShiftAdminForm
    list_display = ("hospital_name", "ward", "start_at", "end_at", "status", "assigned_guard")
    list_filter = ("status", "hospital_name")
    readonly_fields = ("start_at", "end_at")


@admin.register(ShiftResponse)
class ShiftResponseAdmin(admin.ModelAdmin):
    list_display = ("shift", "guard", "offered_at", "accepted_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient", "kind", "message", "is_read")
    list_filter = ("kind", "is_read")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "action", "shift", "actor")
    list_filter = ("action",)