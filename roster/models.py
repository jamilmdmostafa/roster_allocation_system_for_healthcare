from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# A guard needs at least this many hours between shifts.
MIN_REST_HOURS = 6


class StaffProfile(models.Model):
    """Profile for everyone in the system, both admins and guards."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        GUARD = "GUARD", "Guard"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    # Unique ID like admin01 or guard07. Created automatically, never edited.
    staff_id = models.CharField(max_length=20, unique=True, editable=False)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.GUARD)
    phone_number = models.CharField(max_length=20)
    address = models.CharField(max_length=255, blank=True)
    license_number = models.CharField("Security licence number", max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["staff_id"]

    def __str__(self):
        return f"{self.staff_id} - {self.full_name}"

    @property
    def full_name(self):
        return self.user.get_full_name()

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @classmethod
    def next_staff_id(cls, role):
        """Work out the next free ID, e.g. guard11 if guard01-guard10 exist."""
        prefix = "admin" if role == cls.Role.ADMIN else "guard"
        existing = cls.objects.filter(staff_id__startswith=prefix).values_list("staff_id", flat=True)
        numbers = [int(s[len(prefix):]) for s in existing if s[len(prefix):].isdigit()]
        return f"{prefix}{max(numbers, default=0) + 1:02d}"

    def completed_shifts(self):
        """Assigned shifts that have already finished."""
        return self.assigned_shifts.filter(
            status=Shift.Status.ASSIGNED, end_at__lt=timezone.now()
        ).order_by("-start_at")

    def upcoming_shifts(self):
        """Assigned shifts that haven't finished yet."""
        return self.assigned_shifts.filter(
            status=Shift.Status.ASSIGNED, end_at__gte=timezone.now()
        ).order_by("start_at")


class Shift(models.Model):
    """A shift at a hospital ward that needs a guard."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        CANCELLED = "CANCELLED", "Cancelled"

    hospital_name = models.CharField(max_length=150)
    ward = models.CharField(max_length=100)
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    uniform = models.CharField(max_length=200, blank=True)
    rules = models.TextField("Rules / notes", blank=True)

    # Full date+time of start and finish, calculated automatically in save().
    # These make overlap and 6-hour-rest checks simple, including overnight shifts.
    start_at = models.DateTimeField(editable=False)
    end_at = models.DateTimeField(editable=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_guard = models.ForeignKey(
        StaffProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_shifts", limit_choices_to={"role": "GUARD"},
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_shifts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at"]

    def __str__(self):
        return (f"{self.hospital_name} / {self.ward} - {self.shift_date} "
                f"{self.start_time:%H:%M}-{self.end_time:%H:%M}")

    def calculate_times(self):

        """
        Work out start_at / end_at from the date and times (handles overnight shifts).
        """

        tz = timezone.get_current_timezone()
        start = datetime.combine(self.shift_date, self.start_time)
        end = datetime.combine(self.shift_date, self.end_time)
        if end <= start:
            # e.g. 22:00 to 06:00 finishes the next morning
            end += timedelta(days=1)
        self.start_at = timezone.make_aware(start, tz)
        self.end_at = timezone.make_aware(end, tz)

    def save(self, *args, **kwargs):
        self.calculate_times()
        super().save(*args, **kwargs)

    @property
    def is_past(self):
        return self.end_at < timezone.now()
    @property
    def has_started(self):
        return self.start_at <= timezone.now()


class ShiftResponse(models.Model):
    """
    One shift offer to one eligible guard. It shows on the guard's dashboard
    with an Accept button. 'accepted_at' is filled when the guard clicks Accept.
    """

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="responses")
    guard = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="shift_responses")
    offered_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
        # Filled in if the guard later cancels this shift
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        unique_together = ("shift", "guard")
        ordering = ["accepted_at", "offered_at"]

    def __str__(self):
        return f"{self.guard} -> {self.shift}"


class Notification(models.Model):
    """A message shown on someone's dashboard - admin or guard."""

    class Kind(models.TextChoices):
        NEW_SHIFT = "NEW_SHIFT", "New shift available"
        ACCEPTED = "ACCEPTED", "Shift accepted"
        ASSIGNED = "ASSIGNED", "Shift assigned"
        CANCELLATION = "CANCELLATION", "Shift cancelled"

    # Who sees this notification (admins and guards both have a StaffProfile)
    recipient = models.ForeignKey(
        StaffProfile, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"To {self.recipient.staff_id}: {self.message}"


class AuditLog(models.Model):
    """Permanent record of every important action, for accountability."""

    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL)
    actor = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.action}"