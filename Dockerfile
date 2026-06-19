FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project so all subdirectories and __init__.py files are included
COPY . .

# Default port for local docker-compose (no $PORT env var there).
# Railway overrides this entirely via railway.toml's startCommand, which
# correctly expands $PORT through a shell — exec form below can't do that.
ENV PORT=8000
CMD ["/bin/sh", "-c", "uvicorn scheduler.main:app --host 0.0.0.0 --port $PORT"]