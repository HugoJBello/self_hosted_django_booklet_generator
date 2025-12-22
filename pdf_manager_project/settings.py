# pdf_manager_project/settings.py
from pathlib import Path
import os

# ------------------------------------------------------------
# Base
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------
# Core settings (ENV-first, con defaults)
# ------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

raw_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").strip()

# Permitir cualquier host si DJANGO_ALLOWED_HOSTS="*"
if raw_hosts == "*":
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [h.strip() for h in raw_hosts.split(",") if h.strip()]

# ------------------------------------------------------------
# Subpath fijo (NO usar FORCE_SCRIPT_NAME)
# ------------------------------------------------------------
APP_SUBPATH = os.environ.get("APP_SUBPATH", "/pdf_manager").rstrip("/")

# ------------------------------------------------------------
# Applications
# ------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Static serving (como en tu otro proyecto)
    "whitenoise.runserver_nostatic",
    "django_rq",
    # App principal
    "booklets",
    "ocrpdf",
]

RQ_QUEUES = {
    "default": {
        "HOST": os.environ.get("REDIS_HOST", "localhost"),
        "PORT": int(os.environ.get("REDIS_PORT", "6379")),
        "DB": int(os.environ.get("REDIS_DB", "0")),
        "DEFAULT_TIMEOUT": 3600,  # OCRs largos
    }
}

# ------------------------------------------------------------
# Middleware
# ------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # WhiteNoise: sirve static bajo gunicorn
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ------------------------------------------------------------
# URLs / WSGI / ASGI
# ------------------------------------------------------------
ROOT_URLCONF = "pdf_manager_project.urls"

WSGI_APPLICATION = "pdf_manager_project.wsgi.application"
ASGI_APPLICATION = "pdf_manager_project.asgi.application"

# ------------------------------------------------------------
# Templates
# ------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",  # por si más adelante añades globales
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# ------------------------------------------------------------
# Database (SQLite persistente en /data)
# ------------------------------------------------------------
DB_PATH = os.environ.get(
    "DJANGO_DB_PATH",
    str(BASE_DIR / "db.sqlite3"),
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_PATH,
    }
}

# ------------------------------------------------------------
# Internationalization
# ------------------------------------------------------------
LANGUAGE_CODE = "es-es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------
# Static & Media (bajo subpath)
# ------------------------------------------------------------
STATIC_URL = f"{APP_SUBPATH}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_ROOT = os.environ.get(
    "DJANGO_MEDIA_ROOT",
    str(BASE_DIR / "media"),
)
MEDIA_URL = f"{APP_SUBPATH}/media/"

# WhiteNoise: compresión y cacheo
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ------------------------------------------------------------
# Reverse proxy / HTTPS (nginx + Cloudflare)
# ------------------------------------------------------------
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Redirigir a https si quieres (normalmente lo hace Cloudflare, pero lo dejo por env)
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "False").lower() == "true"

# ------------------------------------------------------------
# CSRF / Cookies (clave para 403 detrás de proxy)
# ------------------------------------------------------------
# Si ALLOWED_HOSTS="*" NO podemos construir CSRF_TRUSTED_ORIGINS con wildcard.
# Permitimos configurar explícitamente por env:
#   DJANGO_CSRF_TRUSTED_ORIGINS="https://api-android18.hjbello.org,https://otro"
raw_csrf = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()

if raw_csrf:
    CSRF_TRUSTED_ORIGINS = [x.strip() for x in raw_csrf.split(",") if x.strip()]
else:
    if ALLOWED_HOSTS == ["*"]:
        CSRF_TRUSTED_ORIGINS = []
    else:
        CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("*",)]

# Cookies seguras en HTTPS detrás de proxy.
# Si estás en dev http, puedes desactivarlo por env.
COOKIE_SECURE = os.environ.get("DJANGO_COOKIE_SECURE", "True").lower() == "true"
CSRF_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_SECURE = COOKIE_SECURE

# SameSite recomendado para formularios normales (evita problemas)
CSRF_COOKIE_SAMESITE = os.environ.get("DJANGO_CSRF_SAMESITE", "Lax")
SESSION_COOKIE_SAMESITE = os.environ.get("DJANGO_SESSION_SAMESITE", "Lax")

# ------------------------------------------------------------
# Default PK
# ------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

