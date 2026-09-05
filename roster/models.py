import uuid
from django.db import models
from django.contrib.auth.models import User


class StaffProfile(models.Model):
    """
    Extra information about a security guard, linked to a built-in
    Django User account (which handles their login/password).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=20)
    role = models.CharField(max_length=100, default="Security Guard")
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Shift(models.Model):
    """A single shift at a hospital ward that needs a guard assigned."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        FILLED = "FILLED", "Filled"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"

    ward = models.CharField(max_length=100)
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_guard = models.ForeignKey(
        StaffProfile, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_shifts"
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.ward} — {self.shift_date} ({self.start_time}-{self.end_time})"


class ShiftResponse(models.Model):
    """
    One invitation sent to one guard for one shift. The unique 'token'
    is what makes each guard's email link unique and unguessable.
    """
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="responses")
    guard = models.ForeignKey(StaffProfile, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    notified_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("shift", "guard")

    def __str__(self):
        return f"{self.guard} -> {self.shift}"


class AuditLog(models.Model):
    """A permanent record of every important action, for accountability."""
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL)
    actor = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.timestamp}] {self.action}"