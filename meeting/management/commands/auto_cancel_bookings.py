# booking/management/commands/auto_cancel_bookings.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from meeting.models import Booking
from datetime import timedelta

class Command(BaseCommand):
    help = 'Automatically cancels bookings that are not checked in within 10 minutes of their start time.'

    def handle(self, *args, **kwargs):
        current_time = timezone.now()
        bookings = Booking.objects.filter(checked_in=False, cancelled=False)  # Fetches all active bookings

        # Loops all eligible bookings.
        for booking in bookings:
            checkin_window_end = booking.start_time + timedelta(minutes=10)
            if current_time > checkin_window_end: # Checks the check-in window has expired
                booking.cancelled = True
                booking.save()
                # Release the room (mark it as available)
                booking.room.is_available = True
                booking.room.save()
                self.stdout.write(self.style.SUCCESS(f'Booking ID: {booking.id} has been auto-cancelled.'))

        self.stdout.write(self.style.SUCCESS('Auto-cancellation process completed.'))
