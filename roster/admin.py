from django.contrib import admin

from .models import AuditLog, Notification, Shift, ShiftResponse, StaffProfile

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
        # New users are created through the web app so they get a proper ID.
        return False


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
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