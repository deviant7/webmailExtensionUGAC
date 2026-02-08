# Use Python 3.13 runtime
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Ensure unbuffered stdout/stderr for real-time logs
ENV PYTHONUNBUFFERED=1
# Default to DEBUG False in container; set DJANGO_DEBUG=True for development
ENV DJANGO_DEBUG=False

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code

# Copy application code
COPY . .

# Ensure static root exists before collectstatic
RUN mkdir -p /app/staticfiles

# Run migrations and collect static files (do not fail the build if migrations require DB not available)
RUN python manage.py migrate --noinput || true
RUN python manage.py collectstatic --noinput || true

# Expose port (for documentation; Cloud Run sets PORT env var)
EXPOSE 8080

# Start gunicorn
CMD exec gunicorn mail_backend.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 4 --timeout 0
