from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import MIN_REST_HOURS, AuditLog, Notification, Shift, ShiftResponse, StaffProfile

# Reasons a guard can't take a shift (used in messages and checks)
REASON_OVERLAP = "already working at that time"
REASON_REST = f"less than {MIN_REST_HOURS} hours' rest between shifts"

# A cancellation less than this many hours before the shift starts is flagged as short notice
SHORT_NOTICE_HOURS = 24


class ShiftUnavailable(Exception):
    """The action can't go ahead. The message explains why."""


class RestRuleWarning(Exception):
    """Admin tried to assign a guard who hasn't had 6 hours' rest. Can be overridden."""


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


# ================================================================ Shift helpers

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
    Returns None if yes, otherwise REASON_OVERLAP or REASON_REST.

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
            return REASON_OVERLAP
    if clashes.exists():
        return REASON_REST
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


def _notify_admins(kind, shift, message):
    """Put a notification on every admin's dashboard."""
    for admin in StaffProfile.objects.filter(role=StaffProfile.Role.ADMIN, user__is_active=True):
        Notification.objects.create(recipient=admin, kind=kind, shift=shift, message=message[:255])


# ================================================================ Emails

def _shift_detail_lines(shift):
    lines = [
        f"Hospital: {shift.hospital_name}",
        f"Ward: {shift.ward}",
        f"When: {shift_summary(shift)}",
        f"Uniform: {shift.uniform or 'Not specified'}",
    ]
    if shift.rules:
        lines += ["", "Rules / notes:", shift.rules]
    return lines


def _send_email(subject, lines, recipient):
    """Send one email. Returns False instead of crashing if it fails (e.g. mail server down)."""
    try:
        send_mail(
            subject=subject,
            message="\n".join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
        )
        return True
    except Exception:
        return False


def send_new_shift_email(guard, shift, dashboard_url, again=False):
    """Information-only email with the shift details (no button)."""
    start = timezone.localtime(shift.start_at)
    if again:
        heading = "A shift is available again (the guard who had it has cancelled):"
        subject = f"Shift available again: {shift.hospital_name} - {shift.ward}, {start:%d %b}"
    else:
        heading = "A new shift is available:"
        subject = f"New shift: {shift.hospital_name} - {shift.ward}, {start:%d %b}"

    lines = [f"Hi {guard.user.first_name},", "", heading, ""]
    lines += _shift_detail_lines(shift)
    lines += [
        "",
        "To accept this shift, log in to your dashboard. "
        "The first guard to accept gets the shift.",
        dashboard_url,
    ]
    return _send_email(subject, lines, guard.user.email)


def send_assigned_email(guard, shift):
    start = timezone.localtime(shift.start_at)
    lines = [f"Hi {guard.user.first_name},", "", "You have been assigned this shift:", ""]
    lines += _shift_detail_lines(shift)
    lines += ["", "If you can no longer work it, please cancel it from your dashboard as early as possible."]
    return _send_email(
        f"Shift confirmed: {shift.hospital_name} - {shift.ward}, {start:%d %b}", lines, guard.user.email
    )


def send_removed_email(guard, shift):
    start = timezone.localtime(shift.start_at)
    lines = [
        f"Hi {guard.user.first_name},", "",
        "An admin has given this shift to another guard, so you are no longer assigned to it:", "",
    ]
    lines += _shift_detail_lines(shift)
    return _send_email(
        f"Shift change: {shift.hospital_name} - {shift.ward}, {start:%d %b}", lines, guard.user.email
    )


def send_cancellation_email_to_admins(guard, shift, reason, reoffered_count, short_notice):
    start = timezone.localtime(shift.start_at)
    lines = [f"{guard.full_name} ({guard.staff_id}) has cancelled this shift:", ""]
    lines += _shift_detail_lines(shift)
    lines += [
        "",
        f"Reason given: {reason}",
        f"Short notice (under {SHORT_NOTICE_HOURS} hours): {'YES' if short_notice else 'No'}",
        "",
        f"The shift is open again and has been offered to {reoffered_count} guard(s).",
        "If nobody accepts it, assign a guard from Shift Responses.",
    ]
    subject = (
        f"{'SHORT NOTICE - ' if short_notice else ''}Shift cancelled: "
        f"{shift.hospital_name} - {shift.ward}, {start:%d %b}"
    )
    admins = StaffProfile.objects.filter(
        role=StaffProfile.Role.ADMIN, user__is_active=True
    ).select_related("user")
    for admin in admins:
        _send_email(subject, lines, admin.user.email)


# ================================================================ Releasing, accepting, assigning, cancelling

def release_shift(shift, *, actor, dashboard_url, reoffer=False):
    """
    Offer a shift to every eligible guard:
    1. work out who is eligible (not busy, has had 6 hours' rest)
    2. save an offer + dashboard notification for each of them
    3. email each of them the shift details
    reoffer=True is used after a cancellation.
    """
    empty = {"eligible": [], "skipped": [], "email_failed": []}
    if reoffer:
        shift.refresh_from_db()
        if shift.status != Shift.Status.OPEN:
            return empty  # someone already took it

    eligible, skipped = find_eligible_guards(shift)

    # Never offer a shift to a guard who has already cancelled it
    cancelled_ids = set(
        shift.responses.filter(cancelled_at__isnull=False).values_list("guard_id", flat=True)
    )
    eligible = [g for g in eligible if g.pk not in cancelled_ids]
    skipped = [(g, r) for g, r in skipped if g.pk not in cancelled_ids]

    summary = shift_summary(shift)
    prefix = "Available again: " if reoffer else ""

    with transaction.atomic():
        if not reoffer:
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
                message=f"{prefix}{shift.hospital_name} - {shift.ward}, {summary}"[:255],
            )
        skipped_text = ", ".join(f"{g.staff_id} ({reason})" for g, reason in skipped) or "none"
        AuditLog.objects.create(
            shift=shift, actor="system",
            action="SHIFT_REOFFERED" if reoffer else "SHIFT_RELEASED",
            details=f"Offered to {len(eligible)} guard(s). Skipped: {skipped_text}",
        )

    # Emails are sent after saving. One failed email doesn't stop the others.
    email_failed = [
        g.staff_id for g in eligible
        if not send_new_shift_email(g, shift, dashboard_url, again=reoffer)
    ]
    if email_failed:
        AuditLog.objects.create(
            shift=shift, actor="system", action="EMAIL_FAILED", details=", ".join(email_failed),
        )

    return {"eligible": eligible, "skipped": skipped, "email_failed": email_failed}


def accept_shift(response):
    """
    A guard clicks Accept. The FIRST guard to accept gets the shift.

    select_for_update() locks the shift's database row until this transaction ends.
    If two guards click at the same moment, the second request waits for the lock,
    then sees the shift is already assigned - so two guards can never both get it.
    """
    guard = response.guard
    if response.cancelled_at:
        raise ShiftUnavailable("You cancelled this shift earlier, so you can't accept it again.")

    with transaction.atomic():
        shift = Shift.objects.select_for_update().get(pk=response.shift_id)
        # Also lock the guard, so the same guard can't grab two clashing shifts at once
        StaffProfile.objects.select_for_update().get(pk=guard.pk)

        if shift.status != Shift.Status.OPEN:
            raise ShiftUnavailable("Sorry, this shift has already been taken.")
        if shift.start_at <= timezone.now():
            raise ShiftUnavailable("This shift has already started.")
        reason = check_guard_availability(guard, shift)
        if reason:
            raise ShiftUnavailable(f"You can't take this shift: {reason}.")

        response.accepted_at = timezone.now()
        response.save(update_fields=["accepted_at"])
        shift.status = Shift.Status.ASSIGNED
        shift.assigned_guard = guard
        shift.save()

        summary = shift_summary(shift)
        _notify_admins(
            Notification.Kind.ACCEPTED, shift,
            f"{guard.staff_id} ({guard.full_name}) accepted {shift.hospital_name} - {shift.ward}, {summary}",
        )
        Notification.objects.create(
            recipient=guard, kind=Notification.Kind.ASSIGNED, shift=shift,
            message=f"You got the shift: {shift.hospital_name} - {shift.ward}, {summary}"[:255],
        )
        AuditLog.objects.create(
            shift=shift, actor=guard.staff_id, action="SHIFT_ACCEPTED",
            details=f"Accepted at {timezone.localtime(response.accepted_at):%d %b %Y %H:%M:%S}",
        )

    send_assigned_email(guard, shift)
    return shift


def assign_shift(shift, guard, *, actor, override_rest=False):
    """
    Admin assigns (or reassigns) a shift to any guard.
    - Overlapping shifts are always blocked: nobody can be in two places at once.
    - The 6-hour rest rule raises a warning that the admin can choose to override.
    """
    with transaction.atomic():
        shift = Shift.objects.select_for_update().get(pk=shift.pk)
        StaffProfile.objects.select_for_update().get(pk=guard.pk)

        if shift.status == Shift.Status.CANCELLED:
            raise ShiftUnavailable("This shift has been cancelled.")
        if shift.end_at <= timezone.now():
            raise ShiftUnavailable("This shift has already finished.")

        previous = shift.assigned_guard
        if previous and previous.pk == guard.pk:
            raise ShiftUnavailable(f"{guard.staff_id} is already assigned to this shift.")

        reason = check_guard_availability(guard, shift)
        if reason == REASON_OVERLAP:
            raise ShiftUnavailable(
                f"{guard.staff_id} is {REASON_OVERLAP}. A guard can't work two shifts at once."
            )
        if reason == REASON_REST and not override_rest:
            raise RestRuleWarning(f"{guard.staff_id} would have {REASON_REST}.")

        shift.assigned_guard = guard
        shift.status = Shift.Status.ASSIGNED
        shift.save()

        summary = shift_summary(shift)
        Notification.objects.create(
            recipient=guard, kind=Notification.Kind.ASSIGNED, shift=shift,
            message=f"An admin assigned you: {shift.hospital_name} - {shift.ward}, {summary}"[:255],
        )
        if previous:
            Notification.objects.create(
                recipient=previous, kind=Notification.Kind.CANCELLATION, shift=shift,
                message=f"You are no longer assigned to: {shift.hospital_name} - {shift.ward}, {summary}"[:255],
            )

        details = guard.staff_id
        if previous:
            details += f" (replacing {previous.staff_id})"
        if reason == REASON_REST:
            details += " - 6-hour rest rule overridden by admin"
        AuditLog.objects.create(
            shift=shift, actor=actor,
            action="SHIFT_REASSIGNED" if previous else "SHIFT_ASSIGNED",
            details=details,
        )

    send_assigned_email(guard, shift)
    if previous:
        send_removed_email(previous, shift)
    return shift


def cancel_shift(shift, guard, *, reason, dashboard_url):
    """
    A guard cancels a shift they were assigned to.
    1. the shift goes back to Open (locked, all-or-nothing)
    2. it is re-offered to every eligible guard, except anyone who cancelled it
    3. admins get a red dashboard notification and an email
    """
    with transaction.atomic():
        shift = Shift.objects.select_for_update().get(pk=shift.pk)
        if shift.status != Shift.Status.ASSIGNED or shift.assigned_guard_id != guard.pk:
            raise ShiftUnavailable("This shift is no longer assigned to you.")

        now = timezone.now()
        if shift.start_at <= now:
            raise ShiftUnavailable(
                "This shift has already started, so it can't be cancelled online. Please call your admin."
            )

        response, _ = ShiftResponse.objects.get_or_create(shift=shift, guard=guard)
        response.cancelled_at = now
        response.cancel_reason = reason
        response.save(update_fields=["cancelled_at", "cancel_reason"])

        shift.assigned_guard = None
        shift.status = Shift.Status.OPEN
        shift.save()

        short_notice = shift.start_at - now < timedelta(hours=SHORT_NOTICE_HOURS)
        AuditLog.objects.create(
            shift=shift, actor=guard.staff_id, action="SHIFT_CANCELLED",
            details=f"{'Short notice. ' if short_notice else ''}Reason: {reason}",
        )

    # Re-offer it straight away
    result = release_shift(shift, actor="system", dashboard_url=dashboard_url, reoffer=True)
    count = len(result["eligible"])

    _notify_admins(
        Notification.Kind.CANCELLATION, shift,
        f"{'SHORT NOTICE: ' if short_notice else ''}{guard.staff_id} ({guard.full_name}) cancelled "
        f"{shift.hospital_name} - {shift.ward}, {shift_summary(shift)}. "
        f"Re-offered to {count} guard(s). Reason: {reason}",
    )
    send_cancellation_email_to_admins(guard, shift, reason, count, short_notice)
    return result