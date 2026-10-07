from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class StaffIdOrEmailBackend(ModelBackend):
    """
    Lets people log in with EITHER their staff ID (e.g. guard01)
    OR their email address, plus their password.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        User = get_user_model()
        login_value = username.strip()

        # If it contains "@", treat it as an email; otherwise as a staff ID.
        if "@" in login_value:
            user = User.objects.filter(email__iexact=login_value).first()
        else:
            user = User.objects.filter(username__iexact=login_value).first()

        if user is None:
            # Still run the slow password hash, so a wrong ID takes the same time
            # as a wrong password. This stops attackers from discovering which
            # staff IDs or emails exist by timing the response.
            User().set_password(password)
            return None

        # Correct password AND the account is active
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None