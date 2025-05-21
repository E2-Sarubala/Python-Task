from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from celery.schedules import crontab
from django.conf import settings  # Import crontab to schedule periodic tasks

# Tells Celery which settings file to use
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meeting_room_project.settings')

app = Celery('meeting_room_project') #Creates a Celery application instance

# Loads all CELERY_ prefixed settings from your settings.py.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Celery Beat schedule to run the auto-cancellation task every 5 minutes
app.conf.beat_schedule = {
    'auto-cancel-unchecked-bookings': {
        'task': 'meetings.tasks.auto_cancel_unchecked_bookings',  # Path to your task function
        'schedule': crontab(minute='*/5'),  # Run every 5 minutes
    },
}
# Useful for debugging Celery setups 
@app.task(bind=True)
def debug_task(self):
    print('Request: {0!r}'.format(self.request))
