import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECRET_KEY is generated at install time and written to /pimiproxy/ui-secret.txt.
# If the file goes missing (corrupted install, new volume, manual delete), auto-
# generate a fresh random key and persist it. This is still cryptographically
# strong — only existing sessions are invalidated, forcing re-login. Never fall
# back to a hardcoded/dev key.
_secret_file = Path("/pimiproxy/ui-secret.txt")


def _load_or_create_secret_key() -> str:
    if _secret_file.exists():
        existing = _secret_file.read_text().strip()
        if existing:
            return existing
    _secret_file.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(50)
    _secret_file.write_text(generated + "\n")
    try:
        os.chmod(_secret_file, 0o600)
    except OSError:
        pass
    print(f"[pimiproxy-api] generated new SECRET_KEY at {_secret_file}", flush=True)
    return generated


SECRET_KEY = _load_or_create_secret_key()

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
