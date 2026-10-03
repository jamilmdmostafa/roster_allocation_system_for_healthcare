from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import admin_required, guard_required
from .models import Shift, StaffProfile


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
    context = {
        "upcoming_shifts": profile.upcoming_shifts(),
        "previous_shifts": profile.completed_shifts(),
        "notifications": profile.notifications.select_related("shift")[:10],
        "unread_count": profile.notifications.filter(is_read=False).count(),
    }
    return render(request, "roster/guard_dashboard.html", context)


@login_required
@require_POST
def mark_notifications_read(request):
    profile = getattr(request.user, "profile", None)
    if profile:
        profile.notifications.filter(is_read=False).update(is_read=True)
    return redirect("home")


# ---- Placeholder pages, replaced in later phases ----

def _coming_soon(request, title, phase):
    return render(request, "roster/coming_soon.html", {"title": title, "phase": phase})


@admin_required
def user_add(request):
    return _coming_soon(request, "Add New User", 3)


@admin_required
def user_list(request):
    return _coming_soon(request, "Users", 3)


@admin_required
def shift_create(request):
    return _coming_soon(request, "Create Shift", 4)


@admin_required
def shift_responses(request):
    return _coming_soon(request, "Shift Responses", 5)


@guard_required
def my_profile(request):
    return _coming_soon(request, "My Profile", 6)