import random

from django.core.management.base import BaseCommand
from faker import Faker

from roster.models import StaffProfile
from roster.services import create_staff_member

fake = Faker("en_AU")  # Australian-style names, phones and addresses

# Development-only passwords. Never use these on a real system.
ADMIN_PASSWORD = "Admin@1234"
GUARD_PASSWORD = "Guard@1234"


class Command(BaseCommand):
    help = "Creates admin01 and 10 dummy guards (guard01 to guard10)"

    def handle(self, *args, **options):
        if StaffProfile.objects.exists():
            self.stdout.write(self.style.WARNING("Users already exist - nothing created."))
            return

        admin = create_staff_member(
            role=StaffProfile.Role.ADMIN,
            first_name="System",
            last_name="Admin",
            email="admin01@example.com",
            phone_number="0400000000",
            password=ADMIN_PASSWORD,
        )
        admin.user.is_superuser = True
        admin.user.save()
        self.stdout.write(f"Created {admin.staff_id}  (password: {ADMIN_PASSWORD})")

        for i in range(1, 11):
            guard = create_staff_member(
                role=StaffProfile.Role.GUARD,
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=f"guard{i:02d}@example.com",
                phone_number=fake.phone_number()[:20],
                address=fake.address().replace("\n", ", "),
                license_number=f"SL{random.randint(100000, 999999)}",
                password=GUARD_PASSWORD,
            )
            self.stdout.write(f"Created {guard.staff_id}  {guard.full_name}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. All guards use password: {GUARD_PASSWORD}"
        ))