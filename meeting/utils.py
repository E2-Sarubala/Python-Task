# utils.py
from datetime import timedelta
from dateutil.relativedelta import relativedelta  # used to shift dates

def get_recurrence_dates(booking):
    recurrence = booking.recurrence
    start_date = booking.start_time.date()
    end_date = booking.recurrence_end

    if not end_date:  # No recurrence set
        return []

    recurrence_dates = []
    current_date = start_date

    if recurrence == 'daily':
        while current_date <= end_date:
            recurrence_dates.append(current_date)
            current_date += timedelta(days=1)

    elif recurrence == 'weekly':
        while current_date <= end_date:
            recurrence_dates.append(current_date)
            current_date += timedelta(weeks=1)

    elif recurrence == 'monthly':
        while current_date <= end_date:
            recurrence_dates.append(current_date)
            current_date += relativedelta(months=1)

    return recurrence_dates
