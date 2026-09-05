from django.contrib import admin
from .models import StaffProfile, Shift, ShiftResponse, AuditLog

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "role", "is_available")
    list_filter = ("is_available", "role")

@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("ward", "shift_date", "start_time", "end_time", "status", "assigned_guard")
    list_filter = ("status", "ward")

@admin.register(ShiftResponse)
class ShiftResponseAdmin(admin.ModelAdmin):
    list_display = ("shift", "guard", "notified_at", "accepted_at")

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "action", "shift", "actor")
    list_filter = ("action",)