import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECRET_KEY is generated at install time and written to /pimiproxy/ui-secret.txt
_secret_file = Path("/pimiproxy/ui-secret.txt")
SECRET_KEY = _secret_file.read_text().strip() if _secret_file.exists() else "insecure-dev-key-replace-at-install"

DEBUG = False
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "api.middleware.AuthRequiredMiddleware",
]

ROOT_URLCONF = "pimiproxy_api.urls"

WSGI_APPLICATION = "pimiproxy_api.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/pimiproxy/ui-sessions.db",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
