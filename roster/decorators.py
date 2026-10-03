from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(role):
    """Only let logged-in users with the given role ("ADMIN" or "GUARD") see the page."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            profile = getattr(request.user, "profile", None)
            if profile is None or profile.role != role:
                raise PermissionDenied  # shows a "403 Forbidden" page
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


admin_required = role_required("ADMIN")
guard_required = role_required("GUARD")