from celery import shared_task # A decorator that registers a function as a task runs async
from django.utils import timezone
from datetime import timedelta
from .models import Booking

@shared_task # task scheduled or called in background as async
def auto_cancel_unchecked_bookings():
    now = timezone.now()
    # Fetch all bookings where start_time has passed but not checked_in
    bookings = Booking.objects.filter(
        checked_in=False, 
        start_time__lt=now,  # The start time is in the past.
        cancelled=False
    )

    for booking in bookings:
        if now - booking.start_time >= timedelta(minutes=10):  # Check if more than 10 minutes passed
            booking.cancel_auto_release()  # Auto-cancel and release the room
            print(f"Booking {booking.id} has been auto-canceled.")

