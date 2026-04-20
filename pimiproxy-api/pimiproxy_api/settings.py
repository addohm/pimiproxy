import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECRET_KEY is generated at install time and written to /pimiproxy/ui-secret.txt.
# Refuse to start rather than fall back to a known default — a missing secret
# file means the install is broken, not that dev-mode should silently take over.
_secret_file = Path("/pimiproxy/ui-secret.txt")
if not _secret_file.exists():
    raise RuntimeError(
        f"{_secret_file} is missing — reinstall pimiproxy or restore the file. "
        "Refusing to start with an insecure fallback key."
    )
SECRET_KEY = _secret_file.read_text().strip()
if not SECRET_KEY:
    raise RuntimeError(f"{_secret_file} is empty — regenerate it with `openssl rand -hex 32`.")

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
