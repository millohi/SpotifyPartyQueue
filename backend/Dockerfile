FROM python:3.11-slim

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and directories
RUN useradd -m -u 10001 appuser
WORKDIR /app
RUN mkdir -p /app/sqlite && chown -R appuser:appuser /app

# Python env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . /app
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
CMD ["uvicorn","api:app","--host","0.0.0.0","--port","8000"]
