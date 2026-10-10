from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Replace
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_POST

from .decorators import admin_required, guard_required
from .forms import ShiftForm, StaffForm, profile_initial
from .models import AuditLog, Shift, ShiftResponse, StaffProfile
from .services import (
    REASON_OVERLAP,
    RestRuleWarning,
    ShiftUnavailable,
    accept_shift,
    assign_shift,
    cancel_shift,
    SHORT_NOTICE_HOURS,
    check_guard_availability,
    create_staff_member,
    release_shift,
    send_welcome_email,
    shift_summary,
    update_staff_member,
)


# ================================================================ Home & dashboards

@login_required
def home(request):
    """After login, send each person to the right dashboard for their role."""
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return redirect("admin:index")
    if profile.is_admin:
        return redirect("admin_dashboard")
    return redirect("guard_dashboard")


@admin_required
def admin_dashboard(request):
    now = timezone.localtime()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    # Any shift that runs during today, including overnight shifts from yesterday
    todays_shifts = (
        Shift.objects
        .filter(start_at__lt=end_of_day, end_at__gt=start_of_day)
        .exclude(status=Shift.Status.CANCELLED)
        .select_related("assigned_guard__user")
    )

    profile = request.user.profile
    context = {
        "today": now.date(),
        "todays_shifts": todays_shifts,
        "open_shifts_count": Shift.objects.filter(
            status=Shift.Status.OPEN, end_at__gte=now
        ).count(),
        "guard_count": StaffProfile.objects.filter(role=StaffProfile.Role.GUARD).count(),
        "notifications": profile.notifications.select_related("shift")[:10],
        "unread_count": profile.notifications.filter(is_read=False).count(),
    }
    return render(request, "roster/admin_dashboard.html", context)


@guard_required
def guard_dashboard(request):
    profile = request.user.profile

    # Shifts offered to this guard that nobody has taken yet and haven't started
    open_offers = (
        profile.shift_responses
        .filter(shift__status=Shift.Status.OPEN, shift__start_at__gt=timezone.now(),
                cancelled_at__isnull=True)
        .select_related("shift")
        .order_by("shift__start_at")
    )
    # Pair each offer with None (can accept) or the reason they can't
    offers = [(offer, check_guard_availability(profile, offer.shift)) for offer in open_offers]

    context = {
        "offers": offers,
        "upcoming_shifts": profile.upcoming_shifts(),
        "previous_shifts": profile.completed_shifts(),
        "notifications": profile.notifications.select_related("shift")[:10],
        "unread_count": profile.notifications.filter(is_read=False).count(),
    }
    return render(request, "roster/guard_dashboard.html", context)


@guard_required
@require_POST
def shift_accept(request, response_id):
    # guard=... means a guard can only ever accept offers that were made to THEM
    offer = get_object_or_404(
        ShiftResponse.objects.select_related("shift", "guard__user"),
        pk=response_id, guard=request.user.profile,
    )
    try:
        shift = accept_shift(offer)
    except ShiftUnavailable as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            f"You got the shift: {shift.hospital_name} - {shift.ward}, {shift_summary(shift)}. "
            f"The details have been emailed to you.",
        )
    return redirect("guard_dashboard")
@guard_required
def shift_cancel(request, shift_id):
    profile = request.user.profile
    # assigned_guard=profile: a guard can only cancel their OWN shift
    shift = get_object_or_404(
        Shift, pk=shift_id, assigned_guard=profile, status=Shift.Status.ASSIGNED
    )

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Please give a reason for cancelling.")
        else:
            try:
                cancel_shift(
                    shift, profile, reason=reason[:500],
                    dashboard_url=request.build_absolute_uri(reverse("guard_dashboard")),
                )
            except ShiftUnavailable as error:
                messages.error(request, str(error))
            else:
                messages.success(
                    request,
                    "Your shift has been cancelled. The admin has been notified "
                    "and the shift has been offered to other guards.",
                )
            return redirect("guard_dashboard")

    short_notice = shift.start_at - timezone.now() < timedelta(hours=SHORT_NOTICE_HOURS)
    return render(request, "roster/shift_cancel.html", {
        "shift": shift,
        "summary": shift_summary(shift),
        "short_notice": short_notice,
        "short_notice_hours": SHORT_NOTICE_HOURS,
    })


@login_required
@require_POST
def mark_notifications_read(request):
    profile = getattr(request.user, "profile", None)
    if profile:
        profile.notifications.filter(is_read=False).update(is_read=True)
    return redirect("home")


# ================================================================ User management (admin)

def _phone_without_symbols():
    """Phone number with spaces, brackets and dashes removed, so '0812345678' finds '(08) 1234 5678'."""
    expression = F("phone_number")
    for symbol in (" ", "(", ")", "-"):
        expression = Replace(expression, Value(symbol), Value(""))
    return expression


@admin_required
def user_list(request):
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    now = timezone.now()
    assigned = Q(assigned_shifts__status=Shift.Status.ASSIGNED)

    people = StaffProfile.objects.select_related("user").annotate(
        done_count=Count("assigned_shifts", filter=assigned & Q(assigned_shifts__end_at__lt=now)),
        upcoming_count=Count("assigned_shifts", filter=assigned & Q(assigned_shifts__end_at__gte=now)),
        phone_plain=_phone_without_symbols(),
    )

    # Every word typed must match at least one of these fields
    for word in query.split():
        people = people.filter(
            Q(staff_id__icontains=word)
            | Q(user__first_name__icontains=word)
            | Q(user__last_name__icontains=word)
            | Q(user__email__icontains=word)
            | Q(phone_plain__icontains=word)
        )

    if role in StaffProfile.Role.values:
        people = people.filter(role=role)

    context = {
        "people": people,
        "query": query,
        "role": role,
        "roles": StaffProfile.Role.choices,
    }
    return render(request, "roster/user_list.html", context)


@admin_required
def user_add(request):
    if request.method == "POST":
        form = StaffForm(request.POST)
        if form.is_valid():
            temp_password = get_random_string(12)  # cryptographically secure random string
            profile = create_staff_member(**form.cleaned_data, password=temp_password)
            email_sent = send_welcome_email(profile, temp_password, request.build_absolute_uri(reverse("login")))

            AuditLog.objects.create(
                actor=request.user.username,
                action="USER_CREATED",
                details=f"{profile.staff_id} ({profile.get_role_display()})",
            )
            if email_sent:
                messages.success(
                    request,
                    f"{profile.full_name} created with ID {profile.staff_id}. "
                    f"Login details were emailed to {profile.user.email}.",
                )
            else:
                messages.warning(
                    request,
                    f"{profile.full_name} created with ID {profile.staff_id}, but the welcome "
                    f"email could not be sent. Give them their login details another way.",
                )
            if settings.DEBUG:
                # Development only, so you can test logging in as the new user
                messages.info(request, f"Development only - temporary password: {temp_password}")
            return redirect("user_detail", staff_id=profile.staff_id)
    else:
        form = StaffForm()

    return render(request, "roster/user_form.html", {
        "form": form,
        "title": "Add New User",
        "button_label": "Create user",
        "cancel_url": reverse("user_list"),
    })


@admin_required
def user_detail(request, staff_id):
    person = get_object_or_404(StaffProfile.objects.select_related("user"), staff_id=staff_id)
    return render(request, "roster/user_detail.html", {
        "person": person,
        "completed": person.completed_shifts(),
        "upcoming": person.upcoming_shifts(),
    })


@admin_required
def user_edit(request, staff_id):
    person = get_object_or_404(StaffProfile.objects.select_related("user"), staff_id=staff_id)
    detail_url = reverse("user_detail", args=[person.staff_id])
    return _edit_profile(request, person, title=f"Edit {person.staff_id}",
                         done_url=detail_url, cancel_url=detail_url)


# ================================================================ Guard's own profile

@guard_required
def my_profile(request):
    return _edit_profile(request, request.user.profile, title="My Profile",
                         done_url=reverse("my_profile"), cancel_url=reverse("guard_dashboard"))


def _edit_profile(request, person, *, title, done_url, cancel_url):
    """Shared edit logic for admins editing anyone and guards editing themselves."""
    if request.method == "POST":
        form = StaffForm(request.POST, user=person.user, role=person.role)
        if form.is_valid():
            update_staff_member(person, **form.cleaned_data)
            AuditLog.objects.create(
                actor=request.user.username, action="USER_UPDATED", details=person.staff_id,
            )
            messages.success(request, "Profile updated.")
            return redirect(done_url)
    else:
        form = StaffForm(user=person.user, role=person.role, initial=profile_initial(person))

    return render(request, "roster/user_form.html", {
        "form": form,
        "title": title,
        "person": person,
        "button_label": "Save changes",
        "cancel_url": cancel_url,
    })


# ================================================================ Shifts (admin)

@admin_required
def shift_create(request):
    if request.method == "POST":
        form = ShiftForm(request.POST)
        if form.is_valid():
            shift = form.save(commit=False)
            shift.created_by = request.user
            shift.save()

            result = release_shift(
                shift,
                actor=request.user.username,
                dashboard_url=request.build_absolute_uri(reverse("guard_dashboard")),
            )

            count = len(result["eligible"])
            if count:
                messages.success(
                    request,
                    f"Shift created and offered to {count} guard{'s' if count != 1 else ''} "
                    f"by email and dashboard notification.",
                )
            else:
                messages.warning(request, "Shift created, but no guards are free for it. It stays open.")

            if result["skipped"]:
                skipped_text = "; ".join(f"{g.staff_id} - {reason}" for g, reason in result["skipped"])
                messages.info(request, f"Not offered to: {skipped_text}")

            if result["email_failed"]:
                messages.error(request, "Email could not be sent to: " + ", ".join(result["email_failed"]))

            return redirect("shift_create")
    else:
        form = ShiftForm()

    upcoming = (
        Shift.objects
        .filter(end_at__gte=timezone.now())
        .exclude(status=Shift.Status.CANCELLED)
        .select_related("assigned_guard__user")
        .annotate(offered_count=Count("responses"))[:10]
    )
    return render(request, "roster/shift_form.html", {"form": form, "upcoming": upcoming})


@admin_required
def shift_responses(request):
    show = request.GET.get("show", "waiting")
    shifts = (
        Shift.objects
        .filter(end_at__gte=timezone.now())
        .exclude(status=Shift.Status.CANCELLED)
        .select_related("assigned_guard__user")
        .annotate(offered_count=Count("responses"))
        .order_by("start_at")
    )
    if show == "waiting":
        shifts = shifts.filter(status=Shift.Status.OPEN)
    elif show == "assigned":
        shifts = shifts.filter(status=Shift.Status.ASSIGNED)
    else:
        show = "all"

    shifts = list(shifts)
    # Who accepted each shift, and when
    acceptances = {
        r.shift_id: r
        for r in ShiftResponse.objects.filter(
                shift__in=shifts, accepted_at__isnull=False, cancelled_at__isnull=True
        ).select_related("guard__user")
    }
    rows = [(shift, acceptances.get(shift.pk)) for shift in shifts]
    return render(request, "roster/shift_responses.html", {"rows": rows, "show": show})


@admin_required
def shift_detail(request, shift_id):
    shift = get_object_or_404(
        Shift.objects.select_related("assigned_guard__user", "created_by"), pk=shift_id
    )

    if request.method == "POST":
        guard_id = request.POST.get("guard", "")
        if not guard_id.isdigit():
            messages.error(request, "Choose a guard first.")
            return redirect("shift_detail", shift_id=shift.pk)

        guard = get_object_or_404(StaffProfile, pk=guard_id, role=StaffProfile.Role.GUARD)
        override = request.POST.get("override_rest") == "yes"
        try:
            assign_shift(shift, guard, actor=request.user.username, override_rest=override)
        except RestRuleWarning as warning:
            messages.warning(
                request,
                f"{warning} If you're sure, tick 'Override the 6-hour rest rule' and click Assign again.",
            )
            return redirect(f"{reverse('shift_detail', args=[shift.pk])}?guard={guard.pk}")
        except ShiftUnavailable as error:
            messages.error(request, str(error))
            return redirect("shift_detail", shift_id=shift.pk)

        messages.success(
            request,
            f"Shift assigned to {guard.staff_id} ({guard.full_name}). "
            f"They have been notified by email and on their dashboard.",
        )
        return redirect("shift_detail", shift_id=shift.pk)

    # Availability of every guard for this shift
    guard_rows = []
    guards = StaffProfile.objects.filter(
        role=StaffProfile.Role.GUARD, user__is_active=True
    ).select_related("user")
    for guard in guards:
        is_current = guard.pk == shift.assigned_guard_id
        reason = None if is_current else check_guard_availability(guard, shift)
        guard_rows.append({
            "guard": guard,
            "reason": reason,
            "blocked": reason == REASON_OVERLAP,
            "current": is_current,
        })

    responses = shift.responses.select_related("guard__user").order_by(
        F("accepted_at").asc(nulls_last=True), "guard__staff_id"
    )
    return render(request, "roster/shift_detail.html", {
        "shift": shift,
        "summary": shift_summary(shift),
        "guard_rows": guard_rows,
        "responses": responses,
        "selected": request.GET.get("guard", ""),
        "can_assign": shift.status != Shift.Status.CANCELLED and not shift.is_past,
    })