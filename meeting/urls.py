# meeting/urls.py
from django.urls import path
from .views import (BookingCreateView, BookingListView, booking_checkin, AvailableRoomsAPIView)
from . import views
from django.contrib.auth import views as auth_views


urlpatterns = [
    # Login Urls
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # Dashboard URLs
    path('dashboard/', views.dashboard, name='dashboard'),
    path('add-room/', views.add_room, name='add_room'),
    path('create-booking/', BookingCreateView.as_view(), name='create_booking'),
    path('room-availability/', views.room_availability_view, name='room-availability'),
    path('room-capacity/', views.analytics_dashboard, name='room_capacity'),

    # Room Urls
    path('rooms/', views.RoomListView.as_view(), name='room-list'),
    path('rooms/add/', views.RoomCreateView.as_view(), name='room-add'),
    path('rooms/<int:pk>/edit/', views.RoomUpdateView.as_view(), name='room-edit'),
    path('rooms/<int:pk>/delete/', views.RoomDeleteView.as_view(), name='room-delete'),

    # Booking URLs
    path('bookings/', BookingListView.as_view(), name='booking-list'),
    path('bookings/new/', BookingCreateView.as_view(), name='booking-create'),
    path('bookings/<int:booking_id>/checkin/', booking_checkin, name='booking-checkin'),
    path('bookings/<int:booking_id>/cancel/', views.cancel_booking, name='booking-cancel'),
    path('edit-recurring-date/<int:booking_id>/<str:date>/', views.edit_recurring_date, name='edit_recurring_date'),
    path('bookings/group/<int:room_id>/', views.booking_group_detail, name='booking-group-detail'),

    # Room availability
    path('api/rooms/available/', AvailableRoomsAPIView.as_view(), name='api-room-availability'),

    # Room analytics
    path('analytics/', views.analytics_dashboard, name='analytics_dashboard'),
    path('analytics/export/csv/', views.export_analytics_csv, name='export_analytics_csv'),
    path('analytics/export/json/', views.export_analytics_json, name='export_analytics_json'),
]

