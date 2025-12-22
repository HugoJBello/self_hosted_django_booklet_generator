#!/usr/bin/env sh
set -eu

# Defaults razonables (sobrescribibles por env)
: "${DJANGO_SETTINGS_MODULE:=pdf_manager_project.settings}"
: "${DJANGO_DEBUG:=False}"
: "${DJANGO_ALLOWED_HOSTS:=localhost,127.0.0.1}"
: "${DJANGO_SECRET_KEY:=change-me}"
: "${APP_SUBPATH:=/pdf_manager}"

# Persistencia (dentro del contenedor)
: "${DJANGO_DB_PATH:=/app/data/db.sqlite3}"
: "${DJANGO_MEDIA_ROOT:=/app/data/media}"

# Gunicorn
: "${GUNICORN_BIND:=0.0.0.0:8000}"
: "${GUNICORN_WORKERS:=2}"
: "${GUNICORN_TIMEOUT:=300}"

export DJANGO_SETTINGS_MODULE
export DJANGO_DEBUG
export DJANGO_ALLOWED_HOSTS
export DJANGO_SECRET_KEY
export APP_SUBPATH
export DJANGO_DB_PATH
export DJANGO_MEDIA_ROOT

# Carpetas idempotentes (no rompe nada)
mkdir -p /app/data \
  /app/data/media \
  /app/data/uploads \
  /app/data/booklets_outputs \
  /app/data/uploads_ocr \
  /app/data/ocr_outputs

echo ""
echo "➡ PDF Manager arrancando"
echo "   Subpath : ${APP_SUBPATH}"
echo "   URL     : (detrás del proxy) ${APP_SUBPATH}/booklets/  |  ${APP_SUBPATH}/ocr/"
echo ""

# Migraciones + collectstatic (para web y worker)
python manage.py migrate --noinput
python manage.py collectstatic --noinput || true

# ------------------------------------------------------------
# MODO WORKER: /entrypoint.sh rqworker default
# ------------------------------------------------------------
if [ "${1:-}" = "rqworker" ]; then
  QUEUE="${2:-default}"
  echo "   Mode    : rqworker"
  echo "   Queue   : ${QUEUE}"
  echo ""
  exec python manage.py rqworker "${QUEUE}"
fi

# ------------------------------------------------------------
# MODO WEB (por defecto): gunicorn
# ------------------------------------------------------------
echo "   Mode    : web (gunicorn)"
echo "   Bind    : ${GUNICORN_BIND}"
echo ""

exec gunicorn pdf_manager_project.wsgi:application \
  --bind "${GUNICORN_BIND}" \
  --workers "${GUNICORN_WORKERS}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  --access-logfile "-" \
  --error-logfile "-"

