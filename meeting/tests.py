from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User, AnonymousUser
from django.utils import timezone
from datetime import timedelta, date, datetime
from .models import Room, Booking, User
from .forms import RoomForm, BookingForm, BookingEditForm
from .utils import get_recurrence_dates
from dateutil.relativedelta import relativedelta
import unittest
from types import SimpleNamespace
from django.urls import reverse
from django.views import View
from meeting.views import AdminRequiredMixin
from django.contrib.auth import get_user_model
from unittest.mock import patch
from uuid import UUID
from rest_framework.test import APIClient
from rest_framework import status
from io import StringIO
import csv
import json

class RoomModelTest(TestCase):
    def setUp(self):
        self.room1 = Room.objects.create(name="Conference A", location="Salem", capacity=10, resources="Projector, Whiteboard")

    def test_room_str(self):
        self.assertEqual(str(self.room1), "Conference A - 1st Floor")

    def test_unique_together_constraint(self):
        with self.assertRaises(Exception):
            # Attempt to create duplicate room with same name and location
            Room.objects.create(name="Conference A", location="Salem", capacity=5, resources="TV")

class BookingModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pass')
        self.room = Room.objects.create(name="Room 101", location="Building A", capacity=5, resources="Projector")
        self.now = timezone.now()
        self.booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=self.now + timedelta(hours=1),
            end_time=self.now + timedelta(hours=2),
            attendees=3,
            recurrence='none'
        )

    def test_booking_str(self):
        expected = f"{self.room.name} - {(self.booking.start_time).strftime('%Y-%m-%d %H:%M')}"
        self.assertEqual(str(self.booking), expected)

    def test_is_conflicting(self):
        # New booking overlapping existing booking (should be True)
        conflicting_booking = Booking(
            user=self.user,
            room=self.room,
            start_time=self.now + timedelta(hours=1, minutes=30),
            end_time=self.now + timedelta(hours=2, minutes=30),
            attendees=2,
        )
        self.assertTrue(conflicting_booking.is_conflicting())

        # Non-overlapping booking (should be False)
        non_conflicting_booking = Booking(
            user=self.user,
            room=self.room,
            start_time=self.now + timedelta(hours=3),
            end_time=self.now + timedelta(hours=4),
            attendees=2,
        )
        self.assertFalse(non_conflicting_booking.is_conflicting())

    def test_is_still_active_property(self):
        # Future booking not cancelled
        self.assertTrue(self.booking.is_still_active)

        # Cancelled booking
        self.booking.cancelled = True
        self.booking.save()
        self.assertFalse(self.booking.is_still_active)

    def test_checkin_allowed(self):
        # Setup a booking starting now
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=timezone.now() - timedelta(minutes=5),
            end_time=timezone.now() + timedelta(minutes=55),
            attendees=2,
            checked_in=False,
            cancelled=False,
        )
        self.assertTrue(booking.checkin_allowed())

        # Already checked in
        booking.checked_in = True
        booking.save()
        self.assertFalse(booking.checkin_allowed())

        # Cancelled booking
        booking.checked_in = False
        booking.cancelled = True
        booking.save()
        self.assertFalse(booking.checkin_allowed())

        # Outside 10 minutes window
        booking.start_time = timezone.now() - timedelta(minutes=20)
        booking.cancelled = False
        booking.save()
        self.assertFalse(booking.checkin_allowed())

    def test_cancel_auto_release(self):
        # Booking within 10 mins of start and not checked in
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=timezone.now() - timedelta(minutes=5),
            end_time=timezone.now() + timedelta(hours=1),
            attendees=2,
            checked_in=False,
            cancelled=False,
        )
        booking.cancel_auto_release()
        booking.refresh_from_db()
        self.assertTrue(booking.cancelled)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_available)

    def test_cancel_method(self):
        # Cancel with enough time before start
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=2),
            attendees=2,
        )
        booking.cancel(self.user)
        booking.refresh_from_db()
        self.assertTrue(booking.cancelled)
        self.assertFalse(booking.is_active)
        self.assertIsNotNone(booking.cancelled_at)
        self.assertEqual(booking.cancelled_by, self.user)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_available)

    def test_cancel_method_too_late(self):
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=timezone.now() + timedelta(minutes=10),
            end_time=timezone.now() + timedelta(hours=1),
            attendees=2,
        )
        with self.assertRaises(ValueError):
            booking.cancel(self.user)

    def test_can_be_cancelled_property(self):
        # Booking >15 min in future and not cancelled
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=timezone.now() + timedelta(minutes=16),
            end_time=timezone.now() + timedelta(hours=1),
            attendees=2,
            cancelled=False,
        )
        self.assertTrue(booking.can_be_cancelled)

        # Booking <15 min in future
        booking.start_time = timezone.now() + timedelta(minutes=10)
        booking.save()
        self.assertFalse(booking.can_be_cancelled)

        # Already cancelled booking
        booking.cancelled = True
        booking.save()
        self.assertFalse(booking.can_be_cancelled)

class RoomFormTest(TestCase):
    def setUp(self):
        Room.objects.create(name="RoomX", location="Floor1", capacity=5, resources="Projector")

    def test_valid_data(self):
        form = RoomForm(data={
            'name': "RoomY",
            'location': "Floor1",
            'capacity': 10,
            'resources': "TV"
        })
        self.assertTrue(form.is_valid())

    def test_duplicate_room(self):
        form = RoomForm(data={
            'name': "RoomX",
            'location': "Floor1",
            'capacity': 5,
            'resources': "Projector"
        })
        self.assertFalse(form.is_valid())
        self.assertIn('A room with this name already exists at this location.', form.errors['__all__'])

class BookingFormTest(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name="RoomZ", location="Floor2", capacity=5, resources="Projector, TV")

    def test_valid_booking(self):
        start = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 3,
            'required_resources': 'Projector',
            'recurrence': 'none',
            'recurrence_end': ''
        })
        self.assertTrue(form.is_valid())

    def test_start_in_past(self):
        start = (timezone.now() - timedelta(days=1)).replace(microsecond=0)
        end = timezone.now() + timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 2,
            'required_resources': 'Projector',
            'recurrence': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Booking must be in the future.', form.errors['__all__'])

    def test_end_before_start(self):
        start = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        end = start - timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 2,
            'required_resources': 'Projector',
            'recurrence': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('End time must be after start time.', form.errors['__all__'])

    def test_recurrence_without_end(self):
        start = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 2,
            'required_resources': 'Projector',
            'recurrence': 'daily',
            'recurrence_end': ''
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Recurrence end date is required for recurring bookings.', form.errors['__all__'])

    def test_attendees_exceed_capacity(self):
        start = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 10,
            'required_resources': 'Projector',
            'recurrence': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Room does not have enough capacity.', form.errors['__all__'])

    def test_resource_not_available(self):
        start = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(hours=1)
        form = BookingForm(data={
            'room': self.room.id,
            'start_time': start.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'attendees': 2,
            'required_resources': 'Microphone',
            'recurrence': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Requested resource not available in room.', form.errors['__all__'])

class BookingEditFormTest(TestCase):
    def test_valid_form(self):
        form = BookingEditForm(data={
            'start_time': (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
            'end_time': (timezone.now() + timedelta(days=1, hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'new_date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        })
        self.assertTrue(form.is_valid())

class RecurrenceUtilsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.room = Room.objects.create(name='Room A', location='Building A', capacity=10, resources='Projector,Whiteboard')

    def test_daily_recurrence_dates(self):
        start_time = datetime(2025, 5, 1, 10, 0)
        recurrence_end = date(2025, 5, 3)

        booking = Booking(
            user=self.user,
            room=self.room,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            attendees=5,
            recurrence='daily',
            recurrence_end=recurrence_end
        )

        expected_dates = [date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 3)]
        actual_dates = get_recurrence_dates(booking)
        self.assertEqual(actual_dates, expected_dates)

    def test_weekly_recurrence_dates(self):
        start_time = datetime(2025, 5, 1, 10, 0)
        recurrence_end = date(2025, 5, 29)

        booking = Booking(
            user=self.user,
            room=self.room,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            attendees=5,
            recurrence='weekly',
            recurrence_end=recurrence_end
        )

        expected_dates = [
            date(2025, 5, 1),
            date(2025, 5, 8),
            date(2025, 5, 15),
            date(2025, 5, 22),
            date(2025, 5, 29),
        ]
        actual_dates = get_recurrence_dates(booking)
        self.assertEqual(actual_dates, expected_dates)

    def test_monthly_recurrence_dates(self):
        start_time = datetime(2025, 1, 15, 14, 0)
        recurrence_end = date(2025, 4, 15)

        booking = Booking(
            user=self.user,
            room=self.room,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            attendees=3,
            recurrence='monthly',
            recurrence_end=recurrence_end
        )

        expected_dates = [
            date(2025, 1, 15),
            date(2025, 2, 15),
            date(2025, 3, 15),
            date(2025, 4, 15),
        ]
        actual_dates = get_recurrence_dates(booking)
        self.assertEqual(actual_dates, expected_dates)

    def test_none_recurrence_returns_empty_list(self):
        start_time = datetime(2025, 5, 1, 10, 0)

        booking = Booking(
            user=self.user,
            room=self.room,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            attendees=5,
            recurrence='none',
            recurrence_end=None
        )

        actual_dates = get_recurrence_dates(booking)
        self.assertEqual(actual_dates, [])


class GetRecurrenceDatesTestCase(unittest.TestCase):

    def create_booking(self, start_date, recurrence, recurrence_end):
        """Helper to create a mock booking object"""
        return SimpleNamespace(
            start_time=datetime.combine(start_date, datetime.min.time()),
            recurrence=recurrence,
            recurrence_end=recurrence_end
        )

    def test_no_recurrence_end_returns_empty_list(self):
        booking = self.create_booking(date(2025, 5, 1), 'daily', None)
        result = get_recurrence_dates(booking)
        self.assertEqual(result, [])

    def test_daily_recurrence(self):
        booking = self.create_booking(date(2025, 5, 1), 'daily', date(2025, 5, 5))
        expected = [
            date(2025, 5, 1),
            date(2025, 5, 2),
            date(2025, 5, 3),
            date(2025, 5, 4),
            date(2025, 5, 5),
        ]
        result = get_recurrence_dates(booking)
        self.assertEqual(result, expected)

    def test_weekly_recurrence(self):
        booking = self.create_booking(date(2025, 5, 1), 'weekly', date(2025, 5, 29))
        expected = [
            date(2025, 5, 1),
            date(2025, 5, 8),
            date(2025, 5, 15),
            date(2025, 5, 22),
            date(2025, 5, 29),
        ]
        result = get_recurrence_dates(booking)
        self.assertEqual(result, expected)

    def test_monthly_recurrence(self):
        booking = self.create_booking(date(2025, 1, 31), 'monthly', date(2025, 4, 30))
        expected = [
            date(2025, 1, 31),
            date(2025, 2, 28),  # February fallback
            date(2025, 3, 31),
            date(2025, 4, 30),
        ]
        result = get_recurrence_dates(booking)
        self.assertEqual(result, expected)

    def test_unrecognized_recurrence_type_returns_empty(self):
        booking = self.create_booking(date(2025, 5, 1), 'yearly', date(2025, 5, 10))
        result = get_recurrence_dates(booking)
        self.assertEqual(result, [])  # Since no code block for 'yearly'

class AuthDashboardViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = 'testuser'
        self.password = 'pass12345'
        self.user = User.objects.create_user(username=self.username, password=self.password)

        self.login_url = reverse('login')
        self.logout_url = reverse('logout')
        self.dashboard_url = reverse('dashboard')
        self.add_room_url = reverse('add_room')
        self.create_booking_url = reverse('create_booking')
        self.room_availability_view_url = reverse('room-availability')
        self.room_capacity_url = reverse('room_capacity')

    # ---------- Login Tests ----------
    def test_login_get(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/login.html')

    def test_login_valid_post(self):
        response = self.client.post(self.login_url, {
            'username': self.username,
            'password': self.password
        })
        self.assertRedirects(response, self.dashboard_url)

    def test_login_invalid_post(self):
        response = self.client.post(self.login_url, {
            'username': self.username,
            'password': 'wrongpass'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid username or password')
        self.assertTemplateUsed(response, 'meeting/login.html')

    # ---------- Logout Test ----------
    def test_logout_redirects_to_login(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.logout_url)
        self.assertRedirects(response, self.login_url)

    # ---------- Dashboard Tests ----------
    def test_dashboard_requires_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertRedirects(response, f"{self.login_url}?next={self.dashboard_url}")

    def test_dashboard_view_logged_in(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/dashboard.html')

    # ---------- Static Navigation Views ----------
    def test_add_room_view(self):
        response = self.client.get(self.add_room_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Room Page")

    def test_create_booking_view(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.create_booking_url)
        self.assertEqual(response.status_code, 200)

    # ---------- Room Availability Views ----------
    def test_room_availability_view_requires_login(self):
        response = self.client.get(self.room_availability_view_url)
        self.assertRedirects(response, f"{self.login_url}?next={self.room_availability_view_url}")

    def test_room_availability_view_logged_in(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.room_availability_view_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/room_availability.html')

    def test_room_availability_view_post_not_allowed(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.post(self.room_availability_view_url)
        self.assertEqual(response.status_code, 405)  # Method Not Allowed

    def test_room_capacity_placeholder_view(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(self.room_capacity_url)
        self.assertEqual(response.status_code, 200)

class DummyAdminView(AdminRequiredMixin, View):
    def __init__(self, request):
        self.request = request

class AdminRequiredMixinTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        # Create a superuser
        self.superuser = User.objects.create_user(username='admin', password='adminpass', is_superuser=True)
        # Create a regular user
        self.regular_user = User.objects.create_user(username='john', password='johnpass', is_superuser=False)

    def test_superuser_passes_test_func(self):
        """Superuser should pass the AdminRequiredMixin test."""
        request = self.factory.get('/some-admin-url/')
        request.user = self.superuser
        view = DummyAdminView(request)
        self.assertTrue(view.test_func())

    def test_regular_user_fails_test_func(self):
        """Non-superuser should fail the AdminRequiredMixin test."""
        request = self.factory.get('/some-admin-url/')
        request.user = self.regular_user
        view = DummyAdminView(request)
        self.assertFalse(view.test_func())

    def test_anonymous_user_fails_test_func(self):
        """AnonymousUser should also fail the AdminRequiredMixin test."""
        request = self.factory.get('/some-admin-url/')
        request.user = AnonymousUser()
        view = DummyAdminView(request)
        self.assertFalse(view.test_func())

class RoomCreateViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.create_url = reverse('add_room')

        # Superuser for allowed access
        self.admin_user = User.objects.create_user(username='admin', password='adminpass', is_superuser=True)

        # Regular user (not admin)
        self.normal_user = User.objects.create_user(username='user', password='userpass')

    def test_redirect_if_not_logged_in(self):
        """Unauthenticated users should be redirected to login page."""
        response = self.client.get(self.create_url)
        self.assertRedirects(response, f'/login/?next={self.create_url}')

    def test_forbidden_for_non_admin_user(self):
        """Logged-in user without superuser privileges should get 403."""
        self.client.login(username='user', password='userpass')
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 403)  # AdminRequiredMixin should restrict this

    def test_get_room_create_page_as_superuser(self):
        """Superuser should be able to access the create room page."""
        self.client.login(username='admin', password='adminpass')
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/room_form.html')

    def test_post_valid_room_data(self):
        """Valid POST request by superuser should create a room."""
        self.client.login(username='admin', password='adminpass')
        response = self.client.post(self.create_url, {
            'name': 'Board Room',
            'capacity': 10,
            'location': '2nd Floor'
        })
        self.assertRedirects(response, reverse('room-list'))
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Room.objects.first().name, 'Board Room')

    def test_post_invalid_room_data(self):
        """Invalid POST request should not create room and should show error."""
        self.client.login(username='admin', password='adminpass')
        response = self.client.post(self.create_url, {
            'name': '',  # Name is required, so this is invalid
            'capacity': -5,  # Invalid capacity
            'location': ''
        })
        self.assertEqual(response.status_code, 200)  # Form renders again
        self.assertFormError(response, 'form', 'name', 'This field is required.')
        self.assertFormError(response, 'form', 'capacity', 'Ensure this value is greater than or equal to 1.')
        self.assertContains(response, "Failed to create room.")
        self.assertEqual(Room.objects.count(), 0)

class RoomUpdateViewTests(TestCase):

    def setUp(self):
        # Create superuser
        self.superuser = User.objects.create_user(username='admin', password='adminpass', is_superuser=True)
        # Create normal user
        self.user = User.objects.create_user(username='user', password='userpass')
        # Create room to update
        self.room = Room.objects.create(name='Board Room', capacity=10)
        # Update URL
        self.update_url = reverse('room-edit', kwargs={'pk': self.room.pk})
        self.client = Client()

    def test_redirect_if_not_logged_in(self):
        response = self.client.get(self.update_url)
        self.assertRedirects(response, f'/login/?next={self.update_url}')

    def test_forbidden_for_non_admin_user(self):
        self.client.login(username='user', password='userpass')
        response = self.client.get(self.update_url)
        self.assertEqual(response.status_code, 403)

    def test_get_room_update_page_as_superuser(self):
        self.client.login(username='admin', password='adminpass')
        response = self.client.get(self.update_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/room_form.html')
        self.assertIsInstance(response.context['form'], RoomForm)

    def test_post_no_changes(self):
        self.client.login(username='admin', password='adminpass')
        response = self.client.post(self.update_url, {
            'name': self.room.name,
            'capacity': self.room.capacity
        }, follow=True)
        self.assertRedirects(response, reverse('room-list'))
        self.assertContains(response, "No changes were made.")

    def test_post_valid_changes(self):
        self.client.login(username='admin', password='adminpass')
        response = self.client.post(self.update_url, {
            'name': 'Updated Room',
            'capacity': 12
        }, follow=True)
        self.assertRedirects(response, reverse('room-list'))
        self.assertContains(response, "Room updated.")
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, 'Updated Room')
        self.assertEqual(self.room.capacity, 12)

    def test_post_invalid_data(self):
        self.client.login(username='admin', password='adminpass')
        response = self.client.post(self.update_url, {
            'name': '',  # Invalid: required field
            'capacity': 10
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed to update room.")
        self.assertFormError(response, 'form', 'name', 'This field is required.')


User = get_user_model()

class BookingCreateViewTests(TestCase):

    def setUp(self):
        # Create user and login client
        self.user = User.objects.create_user(username='testuser', password='pass')
        self.client = Client()
        self.client.login(username='testuser', password='pass')

        # Create a test room
        self.room = Room.objects.create(name="Test Room", capacity=10)

        self.url = reverse('booking-create')

        # Default booking times
        self.start_time = timezone.now() + timedelta(days=1, hours=1)
        self.end_time = self.start_time + timedelta(hours=1)

    def test_booking_create_success_no_recurrence(self):
        data = {
            'room': self.room.id,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'attendees': 5,
            'required_resources': '',
            'recurrence': 'none',
            'recurrence_end': '',
        }

        with patch('meeting.views.send_mail') as mock_send_mail:
            response = self.client.post(self.url, data)
            self.assertRedirects(response, reverse('booking-list'))
            self.assertEqual(Booking.objects.count(), 1)
            booking = Booking.objects.first()
            self.assertEqual(booking.user, self.user)
            self.assertEqual(booking.room, self.room)
            mock_send_mail.assert_called_once()

    def test_booking_duration_less_than_30_minutes(self):
        short_end = self.start_time + timedelta(minutes=20)
        data = {
            'room': self.room.id,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': short_end.strftime('%Y-%m-%d %H:%M:%S'),
            'attendees': 3,
            'required_resources': '',
            'recurrence': 'none',
            'recurrence_end': '',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)  # form invalid returns form page
        self.assertFormError(response, 'form', None, 'Booking duration must be at least 30 minutes.')
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_create_with_daily_recurrence(self):
        recurrence_end = (self.start_time + timedelta(days=3)).date()
        data = {
            'room': self.room.id,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'attendees': 4,
            'required_resources': '',
            'recurrence': 'daily',
            'recurrence_end': recurrence_end.strftime('%Y-%m-%d'),
        }

        with patch('meeting.views.send_mail') as mock_send_mail:
            response = self.client.post(self.url, data)
            # Should redirect to success_url (booking list)
            self.assertRedirects(response, reverse('booking-list'))
            # 4 bookings: day 1 + 3 recurring days
            self.assertEqual(Booking.objects.count(), 4)

            series_id = Booking.objects.first().series_id
            # All bookings in series should have the same series_id
            self.assertTrue(all(isinstance(b.series_id, UUID) for b in Booking.objects.all()))
            self.assertTrue(all(b.series_id == series_id for b in Booking.objects.all()))

            mock_send_mail.assert_not_called()  # Email only sent for single booking, not bulk create

    def test_booking_create_with_invalid_recurrence(self):
        recurrence_end = (self.start_time + timedelta(days=3)).date()
        data = {
            'room': self.room.id,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'attendees': 4,
            'required_resources': '',
            'recurrence': 'yearly',  # invalid recurrence
            'recurrence_end': recurrence_end.strftime('%Y-%m-%d'),
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', None, 'Invalid recurrence value.')
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_create_conflicting_booking_blocks(self):
        # Create existing booking to conflict with
        Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=self.start_time,
            end_time=self.end_time,
            attendees=5,
        )

        # Try to create conflicting booking (same time)
        data = {
            'room': self.room.id,
            'start_time': self.start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': self.end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'attendees': 4,
            'required_resources': '',
            'recurrence': 'none',
            'recurrence_end': '',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        # Should include conflict error in form non-field errors
        self.assertContains(response, "Conflict")
        # Only original booking exists
        self.assertEqual(Booking.objects.count(), 1)

class BookingViewsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='user1', password='pass')
        self.other_user = User.objects.create_user(username='user2', password='pass2')

        self.room = Room.objects.create(name="Test Room", capacity=5)

        self.client = Client()
        self.client.login(username='user1', password='pass')

    def create_booking(self, start_offset, end_offset, user=None, checked_in=False, is_active=True):
        if user is None:
            user = self.user
        booking = Booking.objects.create(
            user=user,
            room=self.room,
            start_time=timezone.now() + start_offset,
            end_time=timezone.now() + end_offset,
            attendees=1,
            checked_in=checked_in,
            is_active=is_active
        )
        return booking

    # Tests for booking_checkin view

    def test_checkin_success_within_booking_time(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10))
        url = reverse('booking-checkin', kwargs={'booking_id': booking.id})

        response = self.client.post(url, follow=True)
        booking.refresh_from_db()

        self.assertTrue(booking.checked_in)
        self.assertRedirects(response, reverse('booking-list'))
        messages = list(response.context['messages'])
        self.assertTrue(any("Successfully checked in." in str(m) for m in messages))

    def test_checkin_fails_before_booking_time(self):
        booking = self.create_booking(timedelta(minutes=10), timedelta(minutes=20))
        url = reverse('booking-checkin', kwargs={'booking_id': booking.id})

        response = self.client.post(url, follow=True)
        booking.refresh_from_db()

        self.assertFalse(booking.checked_in)
        self.assertRedirects(response, reverse('booking-list'))
        messages = list(response.context['messages'])
        self.assertTrue(any("Check-in is allowed only during the booking time." in str(m) for m in messages))

    def test_checkin_fails_after_booking_time(self):
        booking = self.create_booking(timedelta(minutes=-30), timedelta(minutes=-10))
        url = reverse('booking-checkin', kwargs={'booking_id': booking.id})

        response = self.client.post(url, follow=True)
        booking.refresh_from_db()

        self.assertFalse(booking.checked_in)
        self.assertRedirects(response, reverse('booking-list'))
        messages = list(response.context['messages'])
        self.assertTrue(any("Check-in is allowed only during the booking time." in str(m) for m in messages))

    def test_checkin_get_method_not_allowed(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10))
        url = reverse('booking-checkin', kwargs={'booking_id': booking.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)  # Method Not Allowed

    def test_checkin_unauthorized_booking_returns_404(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10), user=self.other_user)
        url = reverse('booking-checkin', kwargs={'booking_id': booking.id})

        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    # Tests for cancel_booking view

    def test_cancel_booking_success(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10))
        url = reverse('booking-cancel', kwargs={'booking_id': booking.id})

        response = self.client.get(url, follow=True)
        booking.refresh_from_db()

        self.assertFalse(booking.is_active)
        self.assertRedirects(response, reverse('booking-list'))
        messages = list(response.context['messages'])
        self.assertTrue(any("Booking cancelled." in str(m) for m in messages))

    def test_cancel_booking_already_cancelled(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10), is_active=False)
        url = reverse('booking-cancel', kwargs={'booking_id': booking.id})

        response = self.client.get(url, follow=True)
        messages = list(response.context['messages'])

        self.assertRedirects(response, reverse('booking-list'))
        self.assertTrue(any("Booking already cancelled." in str(m) for m in messages))

    def test_cancel_booking_unauthorized_booking_returns_404(self):
        booking = self.create_booking(timedelta(minutes=-10), timedelta(minutes=10), user=self.other_user)
        url = reverse('booking-cancel', kwargs={'booking_id': booking.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

class AvailableRoomsAPIViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='testuser', password='pass')
        # Create some rooms
        self.room1 = Room.objects.create(name="Room 1", capacity=5, is_available=True, resources="projector,whiteboard")
        self.room2 = Room.objects.create(name="Room 2", capacity=10, is_available=True, resources="whiteboard")
        self.room3 = Room.objects.create(name="Room 3", capacity=3, is_available=False, resources="projector")  # unavailable room

        # Create bookings overlapping with certain time range
        now = timezone.now()
        # Booking for room1 from now +10 min to now + 1 hour
        self.booking1 = Booking.objects.create(
            user=self.user,
            room=self.room1,
            start_time=now + timedelta(minutes=10),
            end_time=now + timedelta(hours=1),
            attendees=2,
            is_active=True
        )

        # Booking for room2 but outside test query range (to check availability)
        self.booking2 = Booking.objects.create(
            user=self.user,
            room=self.room2,
            start_time=now + timedelta(hours=5),
            end_time=now + timedelta(hours=6),
            attendees=4,
            is_active=True
        )

    def test_missing_start_or_end_params(self):
        url = reverse('api-room-availability')

        response = self.client.get(url, data={})  # no params
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

        response = self.client.get(url, data={'start': '2025-01-01T10:00'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.get(url, data={'end': '2025-01-01T12:00'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_datetime_format(self):
        url = reverse('api-room-availability')
        params = {'start': 'invalid', 'end': '2025-01-01T12:00'}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

        params = {'start': '2025-01-01T10:00', 'end': 'invalid'}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_after_end(self):
        url = reverse('api-room-availability')
        params = {'start': '2025-01-01T12:00', 'end': '2025-01-01T10:00'}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_capacity_not_integer(self):
        url = reverse('api-room-availability')
        params = {
            'start': '2025-01-01T10:00',
            'end': '2025-01-01T12:00',
            'capacity': 'notanumber'
        }
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_successful_room_availability_no_filters(self):
        url = reverse('api-room-availability')
        now = timezone.now()
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.get(url, {'start': start, 'end': end})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # room1 has booking overlapping with time, so excluded
        # room3 is unavailable
        room_names = [room['name'] for room in response.data]
        self.assertIn(self.room2.name, room_names)
        self.assertNotIn(self.room1.name, room_names)
        self.assertNotIn(self.room3.name, room_names)

    def test_successful_room_availability_with_capacity_filter(self):
        url = reverse('api-room-availability')
        now = timezone.now()
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.get(url, {'start': start, 'end': end, 'capacity': '8'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Only room2 has capacity >= 8 and is available and not overlapping
        room_names = [room['name'] for room in response.data]
        self.assertEqual(room_names, [self.room2.name])

    def test_successful_room_availability_with_resource_filter(self):
        url = reverse('api-room-availability')
        now = timezone.now()
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

        # Filter by resource "whiteboard"
        response = self.client.get(url, {'start': start, 'end': end, 'resources': 'whiteboard'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        room_names = [room['name'] for room in response.data]
        # room2 has whiteboard and is available + no overlap
        # room1 overlaps booking, room3 is unavailable
        self.assertIn(self.room2.name, room_names)
        self.assertNotIn(self.room1.name, room_names)
        self.assertNotIn(self.room3.name, room_names)

    def test_resource_filter_multiple_resources(self):
        url = reverse('api-room-availability')
        now = timezone.now()
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

        # room2 has "whiteboard"
        # room1 has "projector,whiteboard"
        # Filter by "projector,whiteboard" (both)
        response = self.client.get(url, {'start': start, 'end': end, 'resources': 'projector,whiteboard'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Only room2 will remain because room1 has booking overlap
        room_names = [room['name'] for room in response.data]
        self.assertEqual(room_names, [self.room2.name])


class ExportAnalyticsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.csv_url = reverse('export_analytics_csv')
        self.json_url = reverse('export_analytics_json')

    def test_export_csv_unauthenticated(self):
        response = self.client.get(self.csv_url)
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Unauthorized", response.content)

    def test_export_json_unauthenticated(self):
        response = self.client.get(self.json_url)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Unauthorized"})

    def test_export_csv_authenticated_no_bookings(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(self.csv_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

        content = response.content.decode('utf-8')
        reader = csv.reader(StringIO(content))
        rows = list(reader)
        self.assertEqual(rows[0], ['Room Name', 'Booking Count'])
        self.assertEqual(len(rows), 1)  # Only header row

    def test_export_json_authenticated_no_bookings(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(self.json_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_export_csv_authenticated_with_bookings(self):
        self.client.login(username='testuser', password='testpass')

        room = Room.objects.create(name="Room A", capacity=10, location="1st Floor")
        Booking.objects.create(
            room=room,
            user=self.user,
            start_time=datetime.now() + timedelta(hours=1),
            end_time=datetime.now() + timedelta(hours=2),
            meeting_name="Test Meeting"
        )

        response = self.client.get(self.csv_url)
        content = response.content.decode('utf-8')
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        self.assertEqual(rows[0], ['Room Name', 'Booking Count'])
        self.assertEqual(rows[1][0], "Room A")
        self.assertEqual(rows[1][1], "1")

    def test_export_json_authenticated_with_bookings(self):
        self.client.login(username='testuser', password='testpass')

        room = Room.objects.create(name="Room A", capacity=10, location="1st Floor")
        Booking.objects.create(
            room=room,
            user=self.user,
            start_time=datetime.now() + timedelta(hours=1),
            end_time=datetime.now() + timedelta(hours=2),
            meeting_name="Test Meeting"
        )

        response = self.client.get(self.json_url)
        data = json.loads(response.content)

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], "Room A")
        self.assertEqual(data[0]['bookings_count'], 1)

class BookingGroupingViewsTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='alice', password='password123')
        self.room = Room.objects.create(name='Conference Room', capacity=8, location='First Floor')

        now = timezone.now()
        self.booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            meeting_name='Team Sync',
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=1),
            recurrence='daily',
            recurrence_group=1  # matching own id
        )
        self.booking.recurrence_group = self.booking.id
        self.booking.save()

        self.url_booking_list = reverse('booking-list')
        self.url_group_detail = reverse('booking-group-detail', args=[self.room.id])

    def test_booking_list_view_requires_login(self):
        response = self.client.get(self.url_booking_list)
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_booking_list_view_with_grouped_booking(self):
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_booking_list)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/booking_list.html')
        self.assertIn('grouped_bookings', response.context)
        self.assertEqual(len(response.context['grouped_bookings']), 1)

    def test_booking_list_view_with_no_bookings(self):
        self.booking.delete()
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_booking_list)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['grouped_bookings']), [])

    def test_group_detail_view_returns_bookings(self):
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_group_detail)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/booking_room_detail.html')
        self.assertIn('group_bookings', response.context)
        self.assertEqual(len(response.context['group_bookings']), 1)

    def test_group_detail_raises_404_if_no_bookings(self):
        self.booking.delete()
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_group_detail)
        self.assertEqual(response.status_code, 404)

    def test_group_detail_view_sets_display_status(self):
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_group_detail)
        bookings = response.context['group_bookings']
        for booking in bookings:
            self.assertIn(booking.display_status, ['Active', 'Cancelled', 'Checked In', 'Missed'])

    def test_group_detail_checkin_allowed_logic(self):
        # Create a booking starting now to test checkin_allowed
        now = timezone.localtime(timezone.now())
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            meeting_name='Now Meeting',
            start_time=now,
            end_time=now + timedelta(minutes=30),
        )
        self.client.login(username='alice', password='password123')
        response = self.client.get(reverse('booking-group-detail', args=[self.room.id]))
        bookings = response.context['group_bookings']
        match = [b for b in bookings if b.id == booking.id][0]
        self.assertTrue(hasattr(match, 'checkin_allowed'))

    def test_group_detail_view_adds_recurrence_dates(self):
        self.client.login(username='alice', password='password123')
        response = self.client.get(self.url_group_detail)
        bookings = response.context['group_bookings']
        self.assertTrue(hasattr(bookings[0], 'recurrence_dates'))



if __name__ == '__main__':
    unittest.main()

