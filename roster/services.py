from django.contrib.auth.models import User
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