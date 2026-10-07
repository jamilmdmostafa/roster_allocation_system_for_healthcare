from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction

from .models import StaffProfile


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