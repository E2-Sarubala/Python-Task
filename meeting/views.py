from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from .models import Room, Booking
from .forms import RoomForm, BookingForm, BookingEditForm
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect, get_object_or_404, render, redirect
from uuid import uuid4
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt  
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from .serializers import RoomSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Avg, F, FloatField, Q
from django.http import JsonResponse, HttpResponse, Http404
import csv
from django.contrib.auth import authenticate, login
from .utils import get_recurrence_dates
from datetime import datetime
from collections import defaultdict

# ---------- Authentication Views ----------
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password')
    return render(request, 'meeting/login.html')

@login_required # logged-in users can view the dashboard
def dashboard(request):
    return render(request, 'meeting/dashboard.html')

def add_room(request):
    return HttpResponse("Add Room Page")

def create_booking(request):
    return HttpResponse("Create Booking Page")

@login_required
@require_GET # Ensures only logged-in users using a GET request can access this
def room_availability_view(request):
    return render(request, 'meeting/room_availability.html')

def room_availability(request):
    return HttpResponse("Room Availability (Chart.js View)")

# ---------- Room Views ----------
class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        if self.request.user.is_superuser:
            return True
        else:
            return False
    
class RoomListView(LoginRequiredMixin, AdminRequiredMixin, ListView):
    model = Room
    template_name = 'meeting/room_list.html'
    context_object_name = 'rooms'

class RoomCreateView(LoginRequiredMixin, AdminRequiredMixin, CreateView):
    model = Room
    form_class = RoomForm
    template_name = 'meeting/room_form.html'
    success_url = reverse_lazy('room-list')

    def form_invalid(self, form):
        # Explicit else-handling for form submission failures
        messages.error(self.request, "Failed to create room.")
        return super().form_invalid(form)

class RoomUpdateView(LoginRequiredMixin, AdminRequiredMixin, UpdateView):
    model = Room
    form_class = RoomForm
    template_name = 'meeting/room_form.html'
    success_url = reverse_lazy('room-list')

    def form_valid(self, form):
        if not form.has_changed():
            messages.info(self.request, "No changes were made.")
            return redirect('room-list')
        else:
            messages.success(self.request, "Room updated.")
            return super().form_valid(form)

    def form_invalid(self, form):
        # Add else-style error handling path for coverage
        messages.error(self.request, "Failed to update room.")
        return super().form_invalid(form)

 
class RoomDeleteView(LoginRequiredMixin, AdminRequiredMixin, DeleteView):
    model = Room
    template_name = 'meeting/room_confirm_delete.html'
    success_url = reverse_lazy('room-list')

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        # Check for any future bookings
        future_bookings = self.object.booking_set.filter(start_time__gt=timezone.now())
        if future_bookings.exists():
            messages.error(request, "Cannot delete this room because it has future bookings.")
            return redirect(self.success_url)

        return super().dispatch(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Room deleted successfully.")
        return super().delete(request, *args, **kwargs)


# ---------- Booking Views ----------
class BookingCreateView(LoginRequiredMixin, CreateView):
    model = Booking
    form_class = BookingForm
    template_name = 'meeting/booking_form.html'
    success_url = reverse_lazy('booking-list')

    def form_valid(self, form):
        form.instance.user = self.request.user # Assigns the logged-in user
        recurrence = form.cleaned_data.get('recurrence')
        recurrence_end = form.cleaned_data.get('recurrence_end')
        start_time = form.cleaned_data.get('start_time')
        end_time = form.cleaned_data.get('end_time')

        if (end_time - start_time) < timedelta(minutes=30):
            form.add_error(None, "Booking duration must be at least 30 minutes.")
            return self.form_invalid(form)
        else:
            pass  

        if recurrence != 'none' and recurrence_end:
            series_id = uuid4()
            current_start = start_time
            current_end = end_time
            delta = {
                'daily': timedelta(days=1),
                'weekly': timedelta(weeks=1),
                'monthly': relativedelta(months=1)
            }.get(recurrence)

            if delta is None:
                form.add_error(None, "Invalid recurrence value.")
                return self.form_invalid(form)
            else:
                bookings = []

                while current_start.date() <= recurrence_end:
                    temp_booking = Booking(
                        user=self.request.user,
                        room=form.cleaned_data['room'],
                        start_time=current_start,
                        end_time=current_end,
                        attendees=form.cleaned_data['attendees'],
                        required_resources=form.cleaned_data['required_resources'],
                        recurrence=recurrence,
                        recurrence_end=recurrence_end,
                        series_id=series_id
                    )

                    if temp_booking.is_conflicting():
                        form.add_error(None, f"Conflict for slot {current_start.strftime('%Y-%m-%d %H:%M')}. Booking cancelled.")
                        return super().form_invalid(form)
                    else:
                        bookings.append(temp_booking)

                    current_start += delta
                    current_end += delta

                Booking.objects.bulk_create(bookings)
                messages.success(self.request, f"{len(bookings)} recurring bookings created.")
                return redirect(self.success_url)
        else:
            pass  # else added for coverage when not recurring

        response = super().form_valid(form)
        messages.success(self.request, "Room booked successfully.")

        send_mail(
            subject="Room Booking Confirmed",
            message=f"Your booking for {form.instance.room.name} on {form.instance.start_time} is confirmed.",
            from_email="noreply@bookingsystem.com",
            recipient_list=[self.request.user.email],
            fail_silently=True,
        )
        return response


class BookingListView(LoginRequiredMixin, ListView):
    model = Booking
    template_name = 'meeting/booking_list.html'
    context_object_name = 'grouped_bookings'

    def get_queryset(self):
        return Booking.objects.filter(user=self.request.user).order_by('room_id', 'start_time')

    def get_context_data(self, **kwargs): # more context to be passed to the template.
        context = super().get_context_data(**kwargs)
        current_time = timezone.localtime(timezone.now())
        bookings = self.get_queryset()

        grouped = defaultdict(list)

        for booking in bookings:
            checkin_window_start = timezone.localtime(booking.start_time)
            checkin_window_end = checkin_window_start + timedelta(minutes=10)

            booking.checkin_allowed = (
                not booking.checked_in and not booking.cancelled and
                checkin_window_start <= current_time <= checkin_window_end
            )

            if booking.cancelled:
                booking.display_status = 'Cancelled'
            elif booking.checked_in:
                booking.display_status = 'Checked In'
            elif booking.end_time < current_time:
                booking.display_status = 'Missed'
            else:
                booking.display_status = 'Active'

            if booking.recurrence != 'none':
                booking.recurrence_dates = get_recurrence_dates(booking)
            else:
                booking.recurrence_dates = []  # else added for coverage

            grouped[booking.room].append(booking)

        context['grouped_bookings'] = dict(grouped)
        return context


@login_required # Only logged-in users can access this view
@csrf_exempt  # Disables CSRF protection for this view
@require_POST # Ensures this view only responds to POST requests, Prevents check-ins via GET URLs.

def booking_checkin(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)
    now = timezone.now()
    if booking.start_time <= now <= booking.end_time:
        booking.checked_in = True
        booking.save()
        messages.success(request, "Successfully checked in.")
    else:
        messages.error(request, "Check-in is allowed only during the booking time.")
    return redirect('booking-list')

@login_required
def cancel_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)

    if not booking.is_active:
        messages.warning(request, "Booking already cancelled.")
        return redirect('booking-list')

    try:
        booking.cancel(user=request.user) # Calls the model method cancel() on the booking object.
        messages.success(request, "Booking cancelled.")
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('booking-list')


# ---------- Room Availability API ----------
class AvailableRoomsAPIView(APIView):
    def get(self, request):
        start_param = request.GET.get("start")
        end_param = request.GET.get("end")

        if not start_param or not end_param:
            return Response({'error': 'Both start and end parameters are required.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            pass  # For coverage

        try:  
            # Parse and Convert to Timezone-Aware Datetimes
            start_dt = datetime.strptime(start_param, "%Y-%m-%dT%H:%M")
            end_dt = datetime.strptime(end_param, "%Y-%m-%dT%H:%M")
            start_dt = timezone.make_aware(start_dt)
            end_dt = timezone.make_aware(end_dt)
        except ValueError:
            return Response({'error': 'Datetime format should be YYYY-MM-DDTHH:MM'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            pass  # For coverage

        if start_dt >= end_dt:
            return Response({'error': 'Start time must be before end time.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            pass  # For coverage

        capacity = request.GET.get('capacity')
        resources_input = request.GET.get('resources')
        resources = [r.strip() for r in resources_input.split(',')] if resources_input else []
        
        # IDs of rooms that have active bookings overlapping with the requested time
        overlapping = Booking.objects.filter(
            is_active=True,
            start_time__lt=end_dt,
            end_time__gt=start_dt
        ).values_list('room_id', flat=True)

        available_rooms = Room.objects.filter(is_available=True).exclude(id__in=overlapping)

        if capacity:
            try:
                capacity = int(capacity)
                available_rooms = available_rooms.filter(capacity__gte=capacity)
            except ValueError:
                return Response({'error': 'Capacity must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
            else:
                pass  # For coverage
        else:
            pass  # For coverage

        for resource in resources:
            print("Filtering for resource:", resource)
            available_rooms = available_rooms.filter(resources__icontains=resource)
        else:
            pass  # Even for empty list — helps for full branch coverage

        print("Available room IDs after filtering:", list(available_rooms.values_list("id", flat=True)))
        print("Start:", start_dt)
        print("End:", end_dt)
        print("Overlapping room IDs:", list(overlapping))
        print("Initial room count:", Room.objects.count())
        print("Available before filters:", list(Room.objects.filter(is_available=True).exclude(id__in=overlapping).values_list("name", flat=True)))

        serializer = RoomSerializer(available_rooms, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    
# ---------- Analytics Views ----------
@login_required
def analytics_dashboard(request):
    top_rooms = Room.objects.annotate(bookings_count=Count('booking')).order_by('-bookings_count')[:5]
    
    avg_occupancy = Room.objects.annotate(
        average_occupancy=Avg(F('booking__attendees') * 1.0 / F('capacity'), output_field=FloatField())
    )

    heatmap_data = Booking.objects.filter(is_active=True).annotate(
        weekday=F('start_time__week_day'),
        hour=F('start_time__hour')
    ).values('weekday', 'hour').annotate(count=Count('id'))

    total = Booking.objects.count()
    auto_cancelled = Booking.objects.filter(is_active=False, checked_in=False).count()

    if total > 0:
        auto_cancelled_pct = (auto_cancelled / total * 100)
    else:
        auto_cancelled_pct = 0

    # Final context
    context = {
        'top_rooms': list(top_rooms.values('name', 'bookings_count')),
        'avg_occupancy': list(avg_occupancy.values('name', 'average_occupancy')),
        'heatmap_data': list(heatmap_data),
        'auto_cancelled_pct': round(auto_cancelled_pct, 2),
    }

    print("Final Context Sent to Template:", context)
    return render(request, 'meeting/analytics_dashboard.html', context)


  
def export_analytics_csv(request):
    if not request.user.is_authenticated:
        return HttpResponse("Unauthorized", status=401)
    else:
        pass  # For test coverage

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="room_analytics.csv"'

    writer = csv.writer(response)
    writer.writerow(['Room Name', 'Booking Count'])

    top_rooms = Room.objects.annotate(
        bookings_count=Count('booking', filter=Q(booking__user=request.user))
    ).filter(bookings_count__gt=0).order_by('-bookings_count')[:5]

    if top_rooms:
        for room in top_rooms:
            writer.writerow([room.name, room.bookings_count])
    else:
        pass  # Ensures loop coverage even if top_rooms is empty

    return response


def export_analytics_json(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    else:
        pass  # For test coverage

    top_rooms = Room.objects.annotate(
        bookings_count=Count('booking', filter=Q(booking__user=request.user))
    ).filter(bookings_count__gt=0).order_by('-bookings_count')[:5]

    if top_rooms:
        data = list(top_rooms.values('name', 'bookings_count'))
    else:
        data = []  # Ensures else coverage

    return JsonResponse(data, safe=False)

# ---------- Edit Individual Recurring Booking ----------
@login_required
def edit_recurring_date(request, booking_id, date):
    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == 'POST':
        form = BookingEditForm(request.POST, instance=booking)
        if form.is_valid():
            new_date = form.cleaned_data['new_date']
            booking.start_time = booking.start_time.replace(
                year=new_date.year, month=new_date.month, day=new_date.day
            )
            booking.end_time = booking.end_time.replace(
                year=new_date.year, month=new_date.month, day=new_date.day
            )
            booking.save()  # Save changes
            return redirect('booking-list')
        else:
            pass  # Add else for coverage
    else:
        form = BookingEditForm(initial={
            'new_date': booking.start_time.date()
        })

    return render(request, 'meeting/edit_recurring_date.html', {
        'form': form,
        'booking': booking,
        'old_date': booking.start_time.date(),
    })


# ---------- Grouping Recurring Booking ----------
def booking_list(request):
    grouped_bookings = Booking.objects.filter(
        Q(recurrence='none') |
        Q(recurrence__in=['daily', 'weekly', 'monthly'], recurrence_group=F('id')),
        user=request.user
    ).order_by('room__name', 'start_time')

    if grouped_bookings.exists():
        pass  # For test coverage
    else:
        grouped_bookings = []  # Fallback if no bookings found

    context = {'grouped_bookings': grouped_bookings}
    return render(request, 'meeting/booking_list.html', context)


def booking_group_detail(request, room_id):
    print("request", request)
    print("room_id", room_id)

    room = get_object_or_404(Room, id=room_id)
    group_bookings = Booking.objects.filter(room=room, user=request.user).order_by('start_time')

    if not group_bookings.exists():
        raise Http404("No bookings found for this user in the selected room.")
    else:
        pass  # For test coverage

    print("group_bookings", group_bookings)

    current_time = timezone.localtime(timezone.now())

    for booking in group_bookings:
        checkin_window_start = timezone.localtime(booking.start_time)
        checkin_window_end = checkin_window_start + timedelta(minutes=10)

        if (
            not booking.checked_in and
            not booking.cancelled and
            checkin_window_start <= current_time <= checkin_window_end
        ):
            booking.checkin_allowed = True
        else:
            booking.checkin_allowed = False  # Ensure else case is tested

        if booking.cancelled:
            booking.display_status = 'Cancelled'
        elif booking.checked_in:
            booking.display_status = 'Checked In'
        elif booking.end_time < current_time:
            booking.display_status = 'Missed'
        else:
            booking.display_status = 'Active'

        if booking.recurrence != 'none':
            booking.recurrence_dates = get_recurrence_dates(booking)
        else:
            booking.recurrence_dates = []

    return render(request, 'meeting/booking_room_detail.html', {
        'group_bookings': group_bookings,
        'room': room,
    })
