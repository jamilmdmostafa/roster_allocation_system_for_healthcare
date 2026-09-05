from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from faker import Faker
from roster.models import StaffProfile

fake = Faker()


class Command(BaseCommand):
    help = "Creates 50 synthetic guard accounts for testing"

    def handle(self, *args, **kwargs):
        created_count = 0

        for i in range(50):
            username = f"guard{i+1:03d}"

            # Skip if this guard already exists (safe to re-run)
            if User.objects.filter(username=username).exists():
                continue

            user = User.objects.create_user(
                username=username,
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=f"{username}@example.com",
                password="testpass123",
            )

            StaffProfile.objects.create(
                user=user,
                phone_number=fake.phone_number()[:20],
                role="Security Guard",
                is_available=fake.boolean(chance_of_getting_true=80),
            )

            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Created {created_count} synthetic guard profiles.")
        )