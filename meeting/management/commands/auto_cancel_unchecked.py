from django.core.management.base import BaseCommand
from django.utils import timezone
from meeting.models import Booking
from datetime import timedelta
from django.core.mail import send_mail


class Command(BaseCommand):
    help = 'Auto-cancel bookings not checked in within 10 minutes after start time'

    def handle(self, *args, **kwargs):
        now = timezone.now()
        cutoff_time = now - timedelta(minutes=10)  # checks - Booking started more than 10 minutes ago.

        expired = Booking.objects.filter(
            checked_in=False,
            cancelled=False,
            start_time__lte=cutoff_time,
            end_time__gt=now   # Booking end time is still in the future
        )

        for booking in expired:
            booking.cancelled = True
            booking.save()

            # Notify user
            send_mail(
                subject="Booking Auto-Cancelled",
                message=f"Your booking for {booking.room.name} at {booking.start_time.strftime('%Y-%m-%d %H:%M')} "
                        f"was auto-cancelled because you did not check in within 10 minutes of the start time.",
                from_email="noreply@bookingsystem.com",
                recipient_list=[booking.user.email],
                fail_silently=True,
            )

            self.stdout.write(f"Auto-cancelled booking ID {booking.id} for {booking.user.username}")
