# Install Python dependencies in a separate stage so the final runtime image
# only contains what is needed to serve the app.
FROM python:3.13-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Ensure unbuffered stdout/stderr for real-time logs
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Default to DEBUG False in container; set DJANGO_DEBUG=True for development
ENV DJANGO_DEBUG=False

# Copy installed Python packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Ensure static root exists before collectstatic
RUN mkdir -p /app/staticfiles

# Preserve the existing build-time behavior for migrations and static files.
RUN python manage.py migrate --noinput || true
RUN python manage.py collectstatic --noinput || true

# Expose port (for documentation; Cloud Run sets PORT env var)
EXPOSE 8080

# Start gunicorn
CMD exec gunicorn mail_backend.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 4 --timeout 0
