#!/bin/sh
set -e
# Run migrations to initialise the session DB on first start
python manage.py migrate --run-syncdb --no-input
exec gunicorn pimiproxy_api.wsgi:application --bind 0.0.0.0:8000 --workers 2
