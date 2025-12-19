# Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias del sistema (mínimas)
# - ca-certificates: HTTPS
# - build-essential: por si alguna dep requiere compile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Instalar deps Python
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copiar proyecto
COPY . .

# Crear carpetas de data (por si no existen)
RUN mkdir -p /app/data /app/data/media /app/data/booklets_outputs /app/data/uploads

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

