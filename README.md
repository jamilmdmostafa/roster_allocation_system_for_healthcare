# Roster and Allocation System for Healthcare Security Personnel

A web application that replaces phone-call rostering for hospital security and
patient-minder staff. When an admin creates a shift, every eligible guard is
notified at once, and **the first guard to accept gets the shift**.

Industry Dissertation 2 project, UniSC - MD Mostafa Jamil (1211525).

## Features

**Admin**
- Dashboard: today's shifts, who is covering, open shifts, and notifications (cancellations highlighted)
- Add and edit users with automatic IDs (`admin01`, `guard01`, ...) and an emailed temporary password
- Users list with role column and search by name, email, phone or staff ID
- User profile with shifts completed and upcoming shifts
- Create shifts with hospital, ward, date, 24-hour times, uniform and rules
- Shift Responses: who accepted first and exactly when; assign or reassign any shift

**Guard**
- Log in with staff ID or email
- Dashboard: available shifts with an Accept button, upcoming and previous shifts, notifications
- Cancel a shift with a reason (admin alerted, flagged if short notice)
- Edit own profile (not the staff ID) and change password

**Rules enforced automatically**
- A guard can never be assigned two overlapping shifts
- A guard needs at least 6 hours' rest between shifts (admins can override, which is logged)
- Shifts are only offered to guards who are free and rested
- A cancelled shift is re-offered to everyone except the guard who cancelled

## Tech stack

Python, Django 6.1, PostgreSQL, Bootstrap 5, Git/GitHub.

## Key design decisions

- **First-to-accept is race-condition safe.** Accepting a shift locks the shift's
  database row (`select_for_update`) inside a transaction, so two simultaneous
  acceptances can never both succeed. An automated test proves this with two threads.
- **One rule for overlap and rest.** A clash is any assigned shift within 6 hours
  either side, which covers both cases and handles overnight shifts.
- **Audit log.** Every creation, offer, acceptance, assignment, override and cancellation is recorded.
- **Security.** Role-based access on every page; guards can only act on their own
  offers and shifts; passwords hashed by Django; secrets kept in `.env`; login errors
  don't reveal whether an account exists; timing-safe login checks.

## Setup (Windows)

1. Install Python 3.12+, Git and PostgreSQL.
2. Clone and create a virtual environment:
```
   git clone https://github.com/jamilmdmostafa/roster_allocation_system_for_healthcare.git
   cd roster_allocation_system_for_healthcare
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
```
3. In pgAdmin, create a database called `hospital_roster`.
4. Copy `.env.example` to `.env` and fill it in. Generate a secret key with:
```
   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```
5. Build the database and add dummy data (`admin01` and 10 guards):
```
   python manage.py migrate
   python manage.py seed_guards
   python manage.py runserver
```
6. Open http://127.0.0.1:8000 and log in as `admin01` / `Admin@1234`
   (guards: `guard01`-`guard10` / `Guard@1234`). Development-only passwords.

## Email

- `EMAIL_MODE=console` (default): emails are printed in the terminal.
- `EMAIL_MODE=smtp`: real emails via the SMTP account in `.env` (e.g. Gmail with an App Password).
  Dummy `@example.com` addresses are skipped in this mode so they don't bounce.

## Running the tests

```
python manage.py test roster
```

## Future work

- Send emails in the background with Celery + Redis so pages return instantly
- Deactivate staff who leave (instead of deleting records)
- SMS notifications for urgent short-notice shifts
- Deployment to a cloud host