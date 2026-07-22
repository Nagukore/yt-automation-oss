# Backend image: FastAPI + Celery worker + scheduler all share this image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg for video assembly + subtitle burn-in; build tools for some wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
# Install runtime deps only (media extras are heavy; enable by editing this line).
RUN pip install --upgrade pip && pip install -e .

COPY backend ./backend
COPY alembic ./alembic
COPY alembic.ini ./

ENV PYTHONPATH=/app/backend

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
