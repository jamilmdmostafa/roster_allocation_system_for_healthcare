from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),

    # Login / logout / password (Django's built-in, secure views)
    path("login/", auth_views.LoginView.as_view(
        template_name="roster/login.html", redirect_authenticated_user=True
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", auth_views.PasswordChangeView.as_view(
        template_name="roster/password_change.html"
    ), name="password_change"),
    path("password/change/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="roster/password_change_done.html"
    ), name="password_change_done"),

    # Admin pages
    path("dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("users/", views.user_list, name="user_list"),
    path("users/add/", views.user_add, name="user_add"),  # must stay ABOVE the <staff_id> lines
    path("users/<str:staff_id>/", views.user_detail, name="user_detail"),
    path("users/<str:staff_id>/edit/", views.user_edit, name="user_edit"),
    path("shifts/create/", views.shift_create, name="shift_create"),
    path("shifts/responses/", views.shift_responses, name="shift_responses"),
    path("shifts/<int:shift_id>/", views.shift_detail, name="shift_detail"),

    # Guard pages
    path("my/dashboard/", views.guard_dashboard, name="guard_dashboard"),
    path("my/profile/", views.my_profile, name="my_profile"),
    path("my/offers/<int:response_id>/accept/", views.shift_accept, name="shift_accept"),

    # Shared
    path("notifications/read/", views.mark_notifications_read, name="mark_notifications_read"),
]