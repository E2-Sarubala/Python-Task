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
from django.utils.timezone import make_aware
import pdb
from django.db import connection
from django.contrib.messages import get_messages


class RoomModelTest(TestCase):
    def setUp(self):
        self.room1 = Room.objects.create(name="Conference A", location="Salem", capacity=10, resources="Projector, Whiteboard")

    def test_room_str(self):
        # pdb.set_trace()
        # print("Current DB:", connection.settings_dict['NAME'])
        self.assertEqual(str(self.room1), "Conference A - Salem")

    def test_unique_together_constraint(self):
        with self.assertRaises(Exception):              # Attempt to create duplicate room with same name and location
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
        # pdb.set_trace()
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
            'new_date': (date.today() + timedelta(days=1)).strftime('%Y-%m-%d'),
            'attendees': 5,  # add all required fields here
        })
        print(form.errors)  # inspect form errors
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
        request = self.factory.get('/some-admin-url/')
        request.user = self.superuser
        view = DummyAdminView(request)
        self.assertTrue(view.test_func())

    def test_regular_user_fails_test_func(self):
        request = self.factory.get('/some-admin-url/')
        request.user = self.regular_user
        view = DummyAdminView(request)
        self.assertFalse(view.test_func())

    def test_anonymous_user_fails_test_func(self):
        request = self.factory.get('/some-admin-url/')
        request.user = AnonymousUser()
        view = DummyAdminView(request)
        self.assertFalse(view.test_func())


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
            self.assertRedirects(response, reverse('booking-list'))  # Should redirect to success_url (booking list)
            self.assertEqual(Booking.objects.count(), 4)     # 4 bookings: day 1 + 3 recurring days
            series_id = Booking.objects.first().series_id

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
        booking = self.create_booking(timedelta(minutes=20), timedelta(minutes=40))
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
    
        # Shared 'now' for consistency across all tests
        self.now = timezone.now().replace(second=0, microsecond=0)

        # Create rooms
        self.room1 = Room.objects.create(name="Room 1", capacity=5, is_available=True, resources="projector,whiteboard")
        self.room2 = Room.objects.create(name="Room 2", capacity=10, is_available=True, resources="whiteboard")
        self.room3 = Room.objects.create(name="Room 3", capacity=3, is_available=False, resources="projector")

        # Booking for room1 overlaps with test query
        self.booking1 = Booking.objects.create(
            user=self.user,
            room=self.room1,
            start_time=self.now + timedelta(minutes=10),
            end_time=self.now + timedelta(hours=1),
            attendees=2,
            is_active=True
        )

        # Booking for room2 is outside test query
        self.booking2 = Booking.objects.create(
            user=self.user,
            room=self.room2,
            start_time=self.now + timedelta(hours=5),
            end_time=self.now + timedelta(hours=6),
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

    def test_resource_filter_multiple_resources(self):
        url = reverse('api-room-availability')
        now = timezone.now()
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.get(url, {'start': start, 'end': end, 'resources': 'projector,whiteboard'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        room_names = [room['name'] for room in response.data]
        self.assertEqual(room_names, [self.room1.name])


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
        self.assertEqual(rows[0], ['Room Name','Location', 'Start Date', 'End Date', 'Capacity', 'Resources',  'Booking Count'])
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
            attendees=5
        )

        response = self.client.get(self.csv_url)
        content = response.content.decode('utf-8')
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        self.assertEqual(rows[0], ['Room Name', 'Location', 'Start Date', 'End Date', 'Capacity', 'Resources', 'Booking Count'])
        self.assertEqual(rows[1][0], "Room A")
        self.assertEqual(rows[1][-1], "1")

    def test_export_json_authenticated_with_bookings(self):
        self.client.login(username='testuser', password='testpass')

        room = Room.objects.create(name="Room A", capacity=10, location="1st Floor")
        Booking.objects.create(
            room=room,
            user=self.user,
            start_time=datetime.now() + timedelta(hours=1),
            end_time=datetime.now() + timedelta(hours=2),
            attendees=5
        )

        response = self.client.get(self.json_url)
        data = json.loads(response.content)

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], "Room A")
        self.assertEqual(data[0]['bookings_count'], 1)



class EditRecurringDateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='password')
        self.room = Room.objects.create(name='Room A', location='Floor 1', capacity=10, resources='Projector')
        
        # Create a booking for tomorrow
        start = make_aware(datetime.now() + timedelta(days=1, hours=2))
        end = start + timedelta(hours=1)
        self.booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=start,
            end_time=end,
            attendees=5
        )
        self.edit_url = reverse('edit_recurring_date', args=[self.booking.id, start.date()])

    def test_edit_recurring_date_unauthenticated(self):
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_get_edit_recurring_date_form(self):
        self.client.login(username='tester', password='password')
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit Recurring Date')

    def test_post_valid_data_updates_booking(self):
        self.client.login(username='tester', password='password')
        new_date = (self.booking.start_time + timedelta(days=2)).date()
        response = self.client.post(self.edit_url, {
            'attendees': 8,
            'new_date': new_date
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.attendees, 8)
        self.assertEqual(self.booking.start_time.date(), new_date)
        self.assertContains(response, 'Recurring booking updated successfully!')

    def test_post_exceeds_room_capacity(self):
        self.client.login(username='tester', password='password')
        response = self.client.post(self.edit_url, {
            'attendees': 15,
            'new_date': self.booking.start_time.date()
        }, follow=True)
        self.assertContains(response, 'Attendees must be less than room capacity')

    def test_post_conflicting_booking(self):
        self.client.login(username='tester', password='password')

        # Create a conflicting booking on same new date/time
        conflict_start = self.booking.start_time + timedelta(days=2)
        conflict_end = conflict_start + timedelta(hours=1)
        Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=conflict_start,
            end_time=conflict_end,
            attendees=3
        )

        response = self.client.post(self.edit_url, {
            'attendees': 4,
            'new_date': conflict_start.date()
        }, follow=True)
        self.assertContains(response, 'A booking already exists for this room')

class BookingEditViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.room = Room.objects.create(name="Room A", location="1st Floor", capacity=10, resources="Projector,Whiteboard")

        # Login the user
        self.client.login(username='testuser', password='testpass')

        # Create a non-recurring booking
        self.booking = Booking.objects.create(
            # meeting_name="Test Meeting",
            user=self.user,
            room=self.room,
            start_time=datetime.now() + timedelta(days=1),
            end_time=datetime.now() + timedelta(days=1, hours=1),
            attendees=5,
            required_resources="Projector",
            recurrence='none'
        )

        # Create a group of recurring bookings
        self.recurrence_group_id = "123"
        self.recurring_booking1 = Booking.objects.create(
            # meeting_name="Weekly Standup",
            user=self.user,
            room=self.room,
            start_time=datetime.now() + timedelta(days=2),
            end_time=datetime.now() + timedelta(days=2, hours=1),
            attendees=3,
            required_resources="Whiteboard",
            recurrence='weekly',
            recurrence_group=self.recurrence_group_id
        )
        self.recurring_booking2 = Booking.objects.create(
            # meeting_name="Weekly Standup",
            user=self.user,
            room=self.room,
            start_time=datetime.now() + timedelta(days=9),
            end_time=datetime.now() + timedelta(days=9, hours=1),
            attendees=3,
            required_resources="Whiteboard",
            recurrence='weekly',
            recurrence_group=self.recurrence_group_id
        )

    def test_get_edit_booking_page(self):
        response = self.client.get(reverse('booking-edit', args=[self.booking.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/booking_edit.html')
        self.assertContains(response, 'name="attendees"')

    def test_post_edit_non_recurring_booking(self):
        response = self.client.post(reverse('booking-edit', args=[self.booking.pk]), {
            # 'meeting_name': 'Updated Meeting',
            'room': self.room.id,
            'start_time': datetime.now() + timedelta(days=1, hours=2),
            'end_time': datetime.now() + timedelta(days=1, hours=3),
            'attendees': 6,
            'required_resources': 'Whiteboard',
            'recurrence': 'none'
        })
        self.assertRedirects(response, reverse('booking-list'))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.attendees, 6)
        self.assertEqual(self.booking.required_resources, 'Whiteboard')

    def test_invalid_post_does_not_update(self):
        response = self.client.post(reverse('booking-edit', args=[self.booking.pk]), {
            'meeting_name': '',  # Invalid: required field
            'room': '',
            'start_time': '',
            'end_time': '',
            'attendees': '',
            'required_resources': ''
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'attendees', 'This field is required.')

    def test_booking_not_found_returns_404(self):
        invalid_pk = self.booking.pk + 999
        response = self.client.get(reverse('booking-edit', args=[invalid_pk]))
        self.assertEqual(response.status_code, 404)

class BookingGroupDetailViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.room = Room.objects.create(name='Test Room', location='Test Location', capacity=5, resources='Projector')
        self.url = reverse('booking-group-detail', args=[self.room.id])

    def create_booking(self, **kwargs):
        defaults = {
            'user': self.user,
            'room': self.room,
            'start_time': timezone.now() + timedelta(minutes=1),
            'end_time': timezone.now() + timedelta(minutes=30),
            'checked_in': False,
            'attendees': 5,
            'cancelled': False,
            'recurrence': 'none'
        }
        defaults.update(kwargs)
        return Booking.objects.create(**defaults)

    def test_booking_group_detail_successful(self):
        self.client.login(username='testuser', password='testpass')
        booking = self.create_booking()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/booking_group_detail.html')
        self.assertIn('group_bookings', response.context)
        self.assertIn(booking, response.context['group_bookings'])

    def test_booking_group_detail_no_bookings_raises_404(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_checkin_window_allowed(self):
        self.client.login(username='testuser', password='testpass')
        start_time = timezone.now() - timedelta(minutes=5)
        end_time = start_time + timedelta(minutes=20)
        booking = self.create_booking(start_time=start_time, end_time=end_time)
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertTrue(any(b.checkin_allowed for b in bookings))

    def test_booking_status_checked_in(self):
        self.client.login(username='testuser', password='testpass')
        booking = self.create_booking(checked_in=True)
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertEqual(bookings[0].display_status, 'Checked In')

    def test_booking_status_cancelled(self):
        self.client.login(username='testuser', password='testpass')
        booking = self.create_booking(cancelled=True)
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertEqual(bookings[0].display_status, 'Cancelled')

    def test_booking_status_missed(self):
        self.client.login(username='testuser', password='testpass')
        past_time = timezone.now() - timedelta(hours=2)
        booking = self.create_booking(start_time=past_time, end_time=past_time + timedelta(minutes=30))
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertEqual(bookings[0].display_status, 'Missed')

    def test_booking_status_active(self):
        self.client.login(username='testuser', password='testpass')
        future_time = timezone.now() + timedelta(minutes=30)
        booking = self.create_booking(start_time=future_time, end_time=future_time + timedelta(minutes=30))
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertEqual(bookings[0].display_status, 'Active')

    def test_recurrence_dates_added_if_not_none(self):
        self.client.login(username='testuser', password='testpass')
        booking = self.create_booking(recurrence='daily')
        response = self.client.get(self.url)
        bookings = response.context['group_bookings']
        self.assertTrue(hasattr(bookings[0], 'recurrence_dates'))

class RoomDeleteViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='admin', password='adminpass', is_staff=True, is_superuser=True)
        self.client.login(username='admin', password='adminpass')

        self.room = Room.objects.create(
            name='Conference Room',
            location='First Floor',
            capacity=10,
            resources='Projector, Whiteboard'
        )

        self.delete_url = reverse('room-delete', args=[self.room.id])


    def test_delete_room_with_active_future_bookings_blocked(self):
        Booking.objects.create(
            room=self.room,
            user=self.user,
            # title="Active Booking",
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1),
            cancelled=False,
            attendees=3,
        )

        response = self.client.post(self.delete_url, follow=True)
        self.assertRedirects(response, reverse('room-list'))
        self.assertTrue(Room.objects.filter(id=self.room.id).exists())
        self.assertContains(response, "Cannot delete this room because it has active future bookings.")


class BookingDeleteViewTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create test user and login
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')

        # Create a test room and booking
        self.room = Room.objects.create(name='Test Room', location='1st Floor', capacity=5, resources='Projector')
        self.booking = Booking.objects.create(
            room=self.room,
            user=self.user,
            # meeting_name='Test Meeting',
            start_time=datetime.now() + timedelta(hours=1),
            end_time=datetime.now() + timedelta(hours=2),
            attendees=3
        )

        self.delete_url = reverse('booking-delete', args=[self.booking.pk])  # adjust name as per your `urls.py`

    def test_booking_delete_get(self):
        """GET request should render the confirmation template."""
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'meeting/booking_confirm_delete.html')
        # self.assertContains(response, 'Test Meeting')

    def test_booking_delete_post(self):
        """POST request should delete the booking and redirect."""
        response = self.client.post(self.delete_url)
        self.assertRedirects(response, reverse('booking-list'))
        self.assertFalse(Booking.objects.filter(pk=self.booking.pk).exists())

    def test_booking_delete_not_logged_in(self):
        """Non-authenticated users should be redirected to login."""
        self.client.logout()
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_booking_delete_invalid_pk(self):
        """Invalid booking ID should return 404."""
        invalid_url = reverse('booking-delete', args=[9999])
        response = self.client.get(invalid_url)
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()

