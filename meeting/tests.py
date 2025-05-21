from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from meeting.models import Room, Booking
from datetime import timedelta

class URLTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.room = Room.objects.create(name='Conference Room A', capacity=10)
        self.booking = Booking.objects.create(
            room=self.room,
            user=self.user,
            meeting_title='Test Meeting',
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=2)
        )

    # ------------------------ AUTHENTICATION ------------------------
    def test_login_view_get(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_view_post_valid(self):
        response = self.client.post(reverse('login'), {'username': 'testuser', 'password': 'testpass'})
        self.assertRedirects(response, reverse('dashboard'))

    def test_login_view_post_invalid(self):
        response = self.client.post(reverse('login'), {'username': 'wrong', 'password': 'wrong'})
        self.assertContains(response, 'Invalid username or password')

    def test_logout_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('logout'))
        self.assertRedirects(response, reverse('login'))

    # ------------------------ DASHBOARD + STATIC PAGES ------------------------
    def test_dashboard_authenticated(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_unauthenticated_redirect(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, '/login/?next=/dashboard/')

    def test_add_room_view(self):
        response = self.client.get(reverse('add_room'))
        self.assertEqual(response.status_code, 200)

    def test_create_booking_view(self):
        response = self.client.get(reverse('create_booking'))
        self.assertEqual(response.status_code, 200)

    def test_room_availability_template_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('room-availability'))
        self.assertEqual(response.status_code, 200)

    # ------------------------ ROOM CRUD VIEWS ------------------------
    def test_room_list_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('room-list'))
        self.assertEqual(response.status_code, 200)

    def test_room_add_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('room-add'))
        self.assertEqual(response.status_code, 200)

    def test_room_edit_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('room-edit', args=[self.room.id]))
        self.assertEqual(response.status_code, 200)

    def test_room_delete_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('room-delete', args=[self.room.id]))
        self.assertEqual(response.status_code, 200)

    # ------------------------ BOOKINGS ------------------------
    def test_booking_list_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('booking-list'))
        self.assertEqual(response.status_code, 200)

    def test_booking_create_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('booking-create'))
        self.assertEqual(response.status_code, 200)

    def test_booking_checkin_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('booking-checkin', args=[self.booking.id]))
        self.assertIn(response.status_code, [200, 302])  # Allow redirect if implemented that way

    def test_booking_cancel_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('booking-cancel', args=[self.booking.id]))
        self.assertIn(response.status_code, [200, 302])

    def test_edit_recurring_date_view(self):
        self.client.login(username='testuser', password='testpass')
        date_str = self.booking.start_time.date().strftime('%Y-%m-%d')
        response = self.client.get(reverse('edit_recurring_date', args=[self.booking.id, date_str]))
        self.assertIn(response.status_code, [200, 302])

    def test_booking_group_detail_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('booking-group-detail', args=[self.room.id]))
        self.assertEqual(response.status_code, 200)

    # ------------------------ API & ANALYTICS ------------------------
    def test_api_available_rooms_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('api-room-availability'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_analytics_dashboard_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('analytics_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_export_analytics_csv_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('export_analytics_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_export_analytics_json_view(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get(reverse('export_analytics_json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
