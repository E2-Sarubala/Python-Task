from django import forms
from .models import Room, Booking
from django.utils import timezone
from datetime import date
from django.forms.widgets import CheckboxSelectMultiple


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ['name', 'location', 'capacity', 'resources']

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        location = cleaned_data.get('location')

        if Room.objects.filter(name=name, location=location).exclude(id=self.instance.id).exists():
            raise forms.ValidationError("A room with this name already exists at this location.")

        return cleaned_data

class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['room', 'start_time', 'end_time', 'attendees', 'required_resources', 'recurrence', 'recurrence_end']
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'recurrence_end': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        room = cleaned_data.get('room')
        start = cleaned_data.get('start_time')
        end = cleaned_data.get('end_time')
        attendees = cleaned_data.get('attendees')
        resources = cleaned_data.get('required_resources')
        recurrence = cleaned_data.get('recurrence')
        recurrence_end = cleaned_data.get('recurrence_end')

        if start and end:
            if start < timezone.now():
                raise forms.ValidationError("Booking must be in the future.")
            if start >= end:
                raise forms.ValidationError("End time must be after start time.")

        if recurrence != 'none' and not recurrence_end:
            raise forms.ValidationError("Recurrence end date is required for recurring bookings.")

        if room:
            if attendees and room.capacity < attendees:
                raise forms.ValidationError("Room does not have enough capacity.")
            if resources and resources.lower() not in room.resources.lower():
                raise forms.ValidationError("Requested resource not available in room.")

class BookingEditForm(forms.ModelForm):
    new_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    class Meta:
        model = Booking
        fields = ['start_time', 'end_time', 'new_date']
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        } 