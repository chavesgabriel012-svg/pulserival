# Imagen para Fly.io. Chica a propósito: la máquina más barata tiene 256 MB.
FROM python:3.11-slim

# PYTHONUNBUFFERED para que los logs salgan al momento en `fly logs`: sin
# esto, el planificador parece no hacer nada durante horas porque su salida
# se queda en el buffer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias primero, en su propia capa: cambian mucho menos que el
# código, así que un deploy normal no las reinstala.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Donde se monta el disco. La base vive acá, no en la imagen: la imagen se
# reemplaza en cada deploy y el disco no.
ENV PULSERIVAL_DB=/datos/pulserival.db
EXPOSE 8080

# --workers 1 no es tacañería: SQLite aguanta muchos lectores y un solo
# escritor, y el planificador corre dentro del proceso web. Con dos workers
# habría dos planificadores compitiendo por la misma base.
#
# --timeout 120 porque previsualizar un correo con muchos anuncios puede
# tardar; el ciclo no pasa por acá, corre en su propio hilo.
#
# El puerto sale de PORT si está (Railway lo inyecta) y si no del 8080 (Fly lo
# toma del fly.toml). Tiene que ser forma shell: la forma de lista no expande
# variables y gunicorn recibiría el texto "$PORT".
CMD ["sh", "-c", "exec gunicorn 'pulserival.web.app:wsgi()' \
  --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 120 \
  --access-logfile - --error-logfile -"]
