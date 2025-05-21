from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.utils.timezone import now

class Room(models.Model):
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    capacity = models.PositiveIntegerField()
    resources = models.CharField(max_length=255)  
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = ('name', 'location')  # Ensures uniqueness at the DB level

    def __str__(self):
        return f"{self.name} - {self.location}"


class Booking(models.Model):
    RECUR_CHOICES = [
        ('none', 'Does not repeat'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    attendees = models.PositiveIntegerField()
    required_resources = models.TextField(blank=True, null=True)
    recurrence = models.CharField(max_length=10, choices=RECUR_CHOICES, default='none')
    recurrence_end = models.DateField(blank=True, null=True)
    series_id = models.UUIDField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    checked_in = models.BooleanField(default=False)  # New field
    cancelled = models.BooleanField(default=False)   # New field
    is_active = models.BooleanField(default=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='cancelled_bookings'
    )  
    recurrence_group = models.IntegerField(null=True, blank=True)
    recurrence_rule = models.CharField(max_length=100, blank=True, null=True)  # e.g. daily, weekly, etc
 

    def __str__(self):
        return f"{self.room.name} - {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    def is_conflicting(self):
        return Booking.objects.filter(
            room=self.room,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
            cancelled=False  # Ignore cancelled bookings
        ).exclude(id=self.id).exists()

    @property
    def is_still_active(self):
        return self.end_time >= timezone.now() and not self.cancelled
    
    def checkin_allowed(self):
        now = timezone.now()
        return (
            not self.checked_in and
            not self.cancelled and
            self.start_time <= now <= self.start_time + timedelta(minutes=10)
        )
    
    def cancel_auto_release(self):
        now = timezone.now()
        if self.start_time <= now <= self.start_time + timedelta(minutes=10) and not self.checked_in:
            self.cancelled = True
            self.room.is_available = True  # Mark the room as available
            self.save()  # Save booking
            self.room.save()  # Save room availability change

    def cancel(self, user):
        now = timezone.now()
        time_diff = self.start_time - now

        if time_diff < timedelta(minutes=15):
            raise ValueError("Cannot cancel the booking less than 15 minutes before the start time.")

        self.is_active = False
        self.cancelled = True
        self.cancelled_at = now
        self.cancelled_by = user
        self.save()

        # Release the room
        self.room.is_available = True
        self.room.save()


    @property
    def can_be_cancelled(self):
        if self.cancelled:
            return False
        now_time = timezone.now()
        start_time = timezone.localtime(self.start_time)  # Convert to same timezone
        time_diff = start_time - timezone.localtime(now_time)
        return time_diff >= timedelta(minutes=15)