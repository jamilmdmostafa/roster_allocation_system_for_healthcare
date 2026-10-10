import itertools
import threading
from datetime import datetime, time, timedelta

from django.core import mail
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import Notification, Shift, ShiftResponse, StaffProfile
from .services import (
    REASON_OVERLAP,
    REASON_REST,
    ShiftUnavailable,
    accept_shift,
    admin_cancel_shift,
    cancel_shift,
    check_guard_availability,
    create_staff_member,
    deactivate_guard,
    release_shift,
)
PASSWORD = "Test@12345"
DASHBOARD_URL = "http://testserver/my/dashboard/"
_numbers = itertools.count(1)


# ---------------------------------------------------------------- helpers

def make_person(role=StaffProfile.Role.GUARD):
    n = next(_numbers)
    return create_staff_member(
        role=role, first_name=f"Test{n}", last_name="Person",
        email=f"person{n}@test.invalid", phone_number=f"0400{n:06d}",
        address="1 Test St, Adelaide SA", license_number=f"SL{n:06d}", password=PASSWORD,
    )


def in_two_days(hour, minute=0):
    day = timezone.localdate() + timedelta(days=2)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def make_shift(start, hours=8, **extra):
    start = timezone.localtime(start)
    end = start + timedelta(hours=hours)
    return Shift.objects.create(
        hospital_name="Test Hospital", ward="Ward A",
        shift_date=start.date(), start_time=start.time(), end_time=end.time(), **extra,
    )


# ---------------------------------------------------------------- tests

class StaffIdTests(TestCase):
    def test_ids_are_created_in_order(self):
        g1, g2 = make_person(), make_person()
        admin = make_person(StaffProfile.Role.ADMIN)
        self.assertEqual(g1.staff_id, "guard01")
        self.assertEqual(g2.staff_id, "guard02")
        self.assertEqual(admin.staff_id, "admin01")
        self.assertTrue(admin.user.is_staff)


class LoginTests(TestCase):
    def setUp(self):
        self.guard = make_person()

    def test_login_with_staff_id(self):
        self.assertTrue(self.client.login(username=self.guard.staff_id, password=PASSWORD))

    def test_login_with_email_any_case(self):
        self.assertTrue(self.client.login(username=self.guard.user.email.upper(), password=PASSWORD))

    def test_wrong_password_fails(self):
        self.assertFalse(self.client.login(username=self.guard.staff_id, password="wrong"))


class AvailabilityTests(TestCase):
    """The guard already works 08:00-16:00 on the same day."""

    def setUp(self):
        self.guard = make_person()
        make_shift(in_two_days(8), status=Shift.Status.ASSIGNED, assigned_guard=self.guard)

    def test_overlapping_shift_is_blocked(self):
        self.assertEqual(check_guard_availability(self.guard, make_shift(in_two_days(12))), REASON_OVERLAP)

    def test_less_than_six_hours_rest_is_blocked(self):
        self.assertEqual(check_guard_availability(self.guard, make_shift(in_two_days(19), hours=4)), REASON_REST)

    def test_exactly_six_hours_rest_is_allowed(self):
        self.assertIsNone(check_guard_availability(self.guard, make_shift(in_two_days(22))))


class ReleaseTests(TestCase):
    def test_only_free_guards_are_offered_and_emailed(self):
        free, busy = make_person(), make_person()
        make_shift(in_two_days(8), status=Shift.Status.ASSIGNED, assigned_guard=busy)
        new_shift = make_shift(in_two_days(10))

        result = release_shift(new_shift, actor="admin01", dashboard_url=DASHBOARD_URL)

        self.assertEqual(result["eligible"], [free])
        self.assertEqual(result["skipped"][0][0], busy)
        self.assertTrue(ShiftResponse.objects.filter(shift=new_shift, guard=free).exists())
        self.assertFalse(ShiftResponse.objects.filter(shift=new_shift, guard=busy).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [free.user.email])


class AcceptTests(TestCase):
    def test_first_guard_wins_and_admin_is_notified(self):
        admin = make_person(StaffProfile.Role.ADMIN)
        g1, g2 = make_person(), make_person()
        shift = make_shift(in_two_days(8))
        release_shift(shift, actor="admin01", dashboard_url=DASHBOARD_URL)

        accept_shift(ShiftResponse.objects.get(shift=shift, guard=g1))
        with self.assertRaises(ShiftUnavailable):
            accept_shift(ShiftResponse.objects.get(shift=shift, guard=g2))

        shift.refresh_from_db()
        self.assertEqual(shift.assigned_guard, g1)
        self.assertEqual(shift.status, Shift.Status.ASSIGNED)
        self.assertTrue(admin.notifications.filter(kind=Notification.Kind.ACCEPTED).exists())


class CancelTests(TestCase):
    def test_cancel_reopens_reoffers_and_alerts_admin(self):
        admin = make_person(StaffProfile.Role.ADMIN)
        g1, g2 = make_person(), make_person()
        shift = make_shift(in_two_days(8))
        release_shift(shift, actor="admin01", dashboard_url=DASHBOARD_URL)
        accept_shift(ShiftResponse.objects.get(shift=shift, guard=g1))

        cancel_shift(shift, g1, reason="Sick", dashboard_url=DASHBOARD_URL)

        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.Status.OPEN)
        self.assertIsNone(shift.assigned_guard)
        self.assertTrue(admin.notifications.filter(kind=Notification.Kind.CANCELLATION).exists())
        # Re-offered to g2, but never back to the guard who cancelled
        self.assertTrue(g2.notifications.filter(message__startswith="Available again").exists())
        self.assertFalse(g1.notifications.filter(message__startswith="Available again").exists())
        with self.assertRaises(ShiftUnavailable):
            accept_shift(ShiftResponse.objects.get(shift=shift, guard=g1))


class SecurityTests(TestCase):
    def setUp(self):
        self.g1, self.g2 = make_person(), make_person()

    def test_logged_out_users_are_sent_to_login(self):
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_guard_cannot_open_admin_pages(self):
        self.client.login(username=self.g1.staff_id, password=PASSWORD)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)

    def test_guard_cannot_accept_someone_elses_offer(self):
        shift = make_shift(in_two_days(8))
        release_shift(shift, actor="admin01", dashboard_url=DASHBOARD_URL)
        offer_for_g1 = ShiftResponse.objects.get(shift=shift, guard=self.g1)

        self.client.login(username=self.g2.staff_id, password=PASSWORD)
        response = self.client.post(reverse("shift_accept", args=[offer_for_g1.pk]))
        self.assertEqual(response.status_code, 404)


class SimultaneousAcceptTest(TransactionTestCase):
    """
    Two guards press Accept at the SAME instant, from two separate threads,
    each with its own database connection - like two real phones.
    The row lock must let exactly one of them win.
    """

    def test_only_one_guard_wins(self):
        g1, g2 = make_person(), make_person()
        shift = make_shift(in_two_days(8))
        release_shift(shift, actor="admin01", dashboard_url=DASHBOARD_URL)
        offer_ids = list(ShiftResponse.objects.filter(shift=shift).values_list("pk", flat=True))

        start_together = threading.Barrier(2)
        results = []

        def press_accept(offer_id):
            try:
                offer = ShiftResponse.objects.select_related("guard", "shift").get(pk=offer_id)
                start_together.wait()  # both threads release at the same moment
                accept_shift(offer)
                results.append("won")
            except ShiftUnavailable:
                results.append("lost")
            finally:
                connection.close()

        threads = [threading.Thread(target=press_accept, args=(pk,)) for pk in offer_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), ["lost", "won"])
        self.assertEqual(ShiftResponse.objects.filter(shift=shift, accepted_at__isnull=False).count(), 1)
class AdminCancelTests(TestCase):
    def test_admin_cancel_notifies_and_emails_the_guard(self):
        guard = make_person()
        shift = make_shift(in_two_days(8), status=Shift.Status.ASSIGNED, assigned_guard=guard)

        returned = admin_cancel_shift(shift, actor="admin01", reason="Ward closed")

        shift.refresh_from_db()
        self.assertEqual(shift.status, Shift.Status.CANCELLED)
        self.assertEqual(shift.cancel_reason, "Ward closed")
        self.assertEqual(returned, guard)
        self.assertTrue(guard.notifications.filter(kind=Notification.Kind.CANCELLATION).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [guard.user.email])
        # A cancelled shift no longer blocks the guard from other shifts
        self.assertIsNone(check_guard_availability(guard, make_shift(in_two_days(10))))


class DeactivateTests(TestCase):
    def test_deactivated_guard_cannot_log_in_and_shifts_are_reoffered(self):
        admin = make_person(StaffProfile.Role.ADMIN)
        leaving, other = make_person(), make_person()
        shift = make_shift(in_two_days(8), status=Shift.Status.ASSIGNED, assigned_guard=leaving)

        reopened = deactivate_guard(leaving, actor="admin01", dashboard_url=DASHBOARD_URL)

        shift.refresh_from_db()
        self.assertEqual(reopened, [shift])
        self.assertEqual(shift.status, Shift.Status.OPEN)
        self.assertIsNone(shift.assigned_guard)
        self.assertTrue(ShiftResponse.objects.filter(shift=shift, guard=other).exists())
        self.assertFalse(ShiftResponse.objects.filter(shift=shift, guard=leaving).exists())
        self.assertTrue(admin.notifications.filter(kind=Notification.Kind.CANCELLATION).exists())
        self.assertFalse(self.client.login(username=leaving.staff_id, password=PASSWORD))


class GuardNotificationFilterTests(TestCase):
    def test_notifications_for_finished_shifts_are_hidden(self):
        guard = make_person()
        two_days_ago = timezone.localdate() - timedelta(days=2)
        past_shift = make_shift(timezone.make_aware(datetime.combine(two_days_ago, time(8, 0))))
        future_shift = make_shift(in_two_days(8))
        Notification.objects.create(recipient=guard, kind=Notification.Kind.NEW_SHIFT,
                                    shift=past_shift, message="old shift")
        Notification.objects.create(recipient=guard, kind=Notification.Kind.NEW_SHIFT,
                                    shift=future_shift, message="new shift")

        self.client.login(username=guard.staff_id, password=PASSWORD)
        response = self.client.get(reverse("guard_dashboard"))

        shown = [n.message for n in response.context["notifications"]]
        self.assertEqual(shown, ["new shift"])