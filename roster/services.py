from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import MIN_REST_HOURS, AuditLog, Notification, Shift, ShiftResponse, StaffProfile


# ================================================================ Users

@transaction.atomic
def create_staff_member(*, role, first_name, last_name, email, phone_number,
                        address="", license_number="", password=None):
    """
    Create a login account and its profile together.
    The login username is the staff ID (e.g. guard05).
    transaction.atomic means: if any part fails, nothing is saved.
    """
    staff_id = StaffProfile.next_staff_id(role)

    user = User.objects.create_user(
        username=staff_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        password=password,
    )
    if role == StaffProfile.Role.ADMIN:
        user.is_staff = True
        user.save()

    return StaffProfile.objects.create(
        user=user,
        staff_id=staff_id,
        role=role,
        phone_number=phone_number,
        address=address,
        license_number=license_number,
    )


@transaction.atomic
def update_staff_member(profile, *, first_name, last_name, email, phone_number,
                        address="", license_number=""):
    """Update a person's details. Staff ID and role are never changed here."""
    user = profile.user
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.save()

    profile.phone_number = phone_number
    profile.address = address
    profile.license_number = license_number
    profile.save()
    return profile


def send_welcome_email(profile, temp_password, login_url):
    """Email a new user their staff ID and temporary password."""
    send_mail(
        subject="Your Roster Management account",
        message=(
            f"Hi {profile.user.first_name},\n\n"
            f"An account has been created for you on Roster Management.\n\n"
            f"Staff ID: {profile.staff_id}\n"
            f"You can log in with your Staff ID or this email address.\n"
            f"Temporary password: {temp_password}\n"
            f"Log in here: {login_url}\n\n"
            f"Please change your password after your first login "
            f"(Change Password in the menu).\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[profile.user.email],
    )


# ================================================================ Shifts

def shift_summary(shift):
    """Readable date and time, e.g. 'Sun 05 Oct 2026, 22:00-06:00 (finishes next day)'."""
    start = timezone.localtime(shift.start_at)
    end = timezone.localtime(shift.end_at)
    text = f"{start:%a %d %b %Y}, {start:%H:%M}-{end:%H:%M}"
    if end.date() != start.date():
        text += " (finishes next day)"
    return text


def check_guard_availability(guard, shift):
    """
    Can this guard work this shift?
    Returns None if yes, or a short reason if not.

    A clash is any shift the guard is already assigned to that starts less than
    6 hours after this one ends, AND ends less than 6 hours before this one starts.
    That single rule covers both overlapping shifts and too little rest.
    """
    rest = timedelta(hours=MIN_REST_HOURS)
    clashes = guard.assigned_shifts.filter(
        status=Shift.Status.ASSIGNED,
        start_at__lt=shift.end_at + rest,
        end_at__gt=shift.start_at - rest,
    ).exclude(pk=shift.pk)

    for other in clashes:
        if other.start_at < shift.end_at and other.end_at > shift.start_at:
            return "already working at that time"
    if clashes.exists():
        return f"less than {MIN_REST_HOURS} hours' rest between shifts"
    return None


def find_eligible_guards(shift):
    """Split all active guards into (eligible, skipped-with-reason)."""
    eligible, skipped = [], []
    guards = StaffProfile.objects.filter(
        role=StaffProfile.Role.GUARD, user__is_active=True
    ).select_related("user")

    for guard in guards:
        reason = check_guard_availability(guard, shift)
        if reason:
            skipped.append((guard, reason))
        else:
            eligible.append(guard)
    return eligible, skipped


def send_new_shift_email(guard, shift, dashboard_url):
    """Information-only email with the shift details (no button)."""
    start = timezone.localtime(shift.start_at)
    lines = [
        f"Hi {guard.user.first_name},",
        "",
        "A new shift is available:",
        "",
        f"Hospital: {shift.hospital_name}",
        f"Ward: {shift.ward}",
        f"When: {shift_summary(shift)}",
        f"Uniform: {shift.uniform or 'Not specified'}",
    ]
    if shift.rules:
        lines += ["", "Rules / notes:", shift.rules]
    lines += [
        "",
        "To accept this shift, log in to your dashboard. "
        "The first guard to accept gets the shift.",
        dashboard_url,
    ]
    send_mail(
        subject=f"New shift: {shift.hospital_name} - {shift.ward}, {start:%d %b}",
        message="\n".join(lines),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[guard.user.email],
    )


def release_shift(shift, *, actor, dashboard_url):
    """
    Offer a newly created shift to every eligible guard:
    1. work out who is eligible (not busy, has had 6 hours' rest)
    2. save an offer + dashboard notification for each of them
    3. email each of them the shift details
    """
    eligible, skipped = find_eligible_guards(shift)
    summary = shift_summary(shift)

    # Database work happens all-or-nothing
    with transaction.atomic():
        AuditLog.objects.create(
            shift=shift, actor=actor, action="SHIFT_CREATED",
            details=f"{shift.hospital_name} / {shift.ward}, {summary}",
        )
        for guard in eligible:
            ShiftResponse.objects.get_or_create(shift=shift, guard=guard)
            Notification.objects.create(
                recipient=guard,
                kind=Notification.Kind.NEW_SHIFT,
                shift=shift,
                message=f"{shift.hospital_name} - {shift.ward}, {summary}"[:255],
            )
        skipped_text = ", ".join(f"{g.staff_id} ({reason})" for g, reason in skipped) or "none"
        AuditLog.objects.create(
            shift=shift, actor="system", action="SHIFT_RELEASED",
            details=f"Offered to {len(eligible)} guard(s). Skipped: {skipped_text}",
        )

    # Emails are sent after saving. One failed email doesn't stop the others.
    email_failed = []
    for guard in eligible:
        try:
            send_new_shift_email(guard, shift, dashboard_url)
        except Exception:  # e.g. mail server unreachable
            email_failed.append(guard.staff_id)

    if email_failed:
        AuditLog.objects.create(
            shift=shift, actor="system", action="EMAIL_FAILED",
            details=", ".join(email_failed),
        )

    return {"eligible": eligible, "skipped": skipped, "email_failed": email_failed}